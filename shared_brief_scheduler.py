"""Opt-in periodic scheduler. No Flask import and no game writes or provider fetching."""
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile

from pre_match_brief import utc, require
from shared_brief_store import BriefStore
from shared_brief_workflow import run_batch, clock, generate_team_brief
from correspondent.writer import DEFAULT_MODEL
import shared_brief_operations as operations

FRESHNESS = timedelta(minutes=15)


def source_connection(source):
    db=sqlite3.connect(Path(source).resolve(strict=True).as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def schedule(source, season_year, now):
    with closing(source_connection(source)) as db:
        db.execute('BEGIN')
        rows=db.execute('''SELECT f.external_match_id,f.home,f.away,f.kickoff_utc,f.api_status,w.number
            FROM fixtures f JOIN weeks w ON w.id=f.week_id JOIN seasons s ON s.id=w.season_id
            WHERE s.api_competition_code='PL' AND s.api_season_year=? ORDER BY w.number,f.external_match_id''',(season_year,)).fetchall()
        states=db.execute('''SELECT a.* FROM api_sync_states a JOIN seasons s ON s.id=a.season_id
            WHERE s.api_competition_code='PL' AND s.api_season_year=?''',(season_year,)).fetchall()
    require(rows, 'No official schedule')
    fresh=bool(states) and all(r['provider']=='football-data.org' and r['last_success_at'] and
        timedelta(0) <= utc(now)-utc(r['last_success_at']) <= FRESHNESS and not r['last_error'] and
        r['unmatched_matches']==0 for r in states)
    rounds={}
    for r in rows:
        item=dict(r)
        group=rounds.setdefault(r['number'],{})
        key=r['external_match_id']
        require(type(key) is int and key>0 and (key not in group or group[key]==item), 'Conflicting official copies')
        group[key]=item
    return rounds,fresh


def counts(store, year, week, version, now):
    rows=store.db.execute('''SELECT external_fixture_id,team_id,status,attempts,error_code,lease_until,completed_at
        FROM team_briefs WHERE season_year=? AND matchweek=? AND content_version=?''',(year,week,version)).fetchall()
    result={}
    entries=[]
    successful=[]
    for r in rows:
        status=r['status']
        if status=='running' and r['lease_until'] and utc(r['lease_until'])<=utc(now): status='uncertain'
        result[status]=result.get(status,0)+1
        if status!='succeeded':
            entries.append(dict(fixture_id=r['external_fixture_id'],team_id=r['team_id'],status=status,
                                attempts=r['attempts'],error_code=r['error_code']))
        elif r['completed_at']: successful.append(r['completed_at'])
    return result,entries,max(successful) if successful else None


def round_status(store, year, week, version, fixtures, fresh, now):
    tally,entries,last=counts(store,year,week,version,now)
    report=dict(season_year=year,matchweek=week,content_version=version,successful=tally.get('succeeded',0),
                total=20,counts=tally,entries=entries,last_successful_generation=last,first_kickoff=None,
                eligible_at=None,target_at=None,intervention_required=False)
    valid=len(fixtures)==10 and len({t for f in fixtures.values() for t in (f['home'],f['away'])})==20
    valid=valid and all(f['kickoff_utc'] for f in fixtures.values())
    if not valid:
        return dict(report,status='invalid_schedule',intervention_required=True)
    first=min(utc(f['kickoff_utc']) for f in fixtures.values())
    opens,deadline=first-timedelta(hours=24),first-timedelta(hours=18)
    report.update(first_kickoff=first.isoformat(),eligible_at=opens.isoformat(),target_at=deadline.isoformat())
    batch=store.batch(year,week,version)
    if batch:
        slots=store.frozen_slots(year,week,version)
        saved={(s['identity']['external_fixture_id'],s['home'],s['away'],utc(s['kickoff'])) for s in slots}
        current={(f['external_match_id'],f['home'],f['away'],utc(f['kickoff_utc'])) for f in fixtures.values()}
        if saved!=current:
            return dict(report,status='schedule_changed',intervention_required=True)
    if report['successful']==20:
        if first>utc(now) and not all(f['api_status'] in ('TIMED','SCHEDULED') for f in fixtures.values()):
            return dict(report,status='schedule_changed',intervention_required=True)
        late=utc(last)>deadline
        return dict(report,status='completed_late' if late else 'completed',intervention_required=late)
    if utc(now)>=deadline:
        return dict(report,status='window_missed',intervention_required=True)
    if not fresh:
        return dict(report,status='official_data_stale',intervention_required=True)
    if not all(f['api_status'] in ('TIMED','SCHEDULED') for f in fixtures.values()):
        return dict(report,status='unconfirmed_schedule',intervention_required=True)
    if utc(now)<opens:
        return dict(report,status='waiting')
    attention=any(tally.get(k,0) for k in ('blocked','uncertain','expired')) or any(e['status']=='failed' and e['attempts']>=3 for e in entries)
    if batch and not tally.get('pending') and not any(e['status']=='failed' and e['attempts']<3 for e in entries):
        return dict(report,status='intervention_required' if attention else 'in_progress',intervention_required=bool(attention))
    return dict(report,status='eligible',intervention_required=bool(attention))


def tick(source, store_path, operations_path, *, season_year=2026, content_version, model=DEFAULT_MODEL,
         now=clock, writer=generate_team_brief):
    require(len({Path(p).resolve() for p in (source,store_path,operations_path)})==3, 'Source, content and operations paths must differ')
    token=operations.claim(operations_path,now())
    if token is None: return dict(status='already_running')
    store=None
    try:
        rounds,fresh=schedule(source,season_year,now())
        store=BriefStore(store_path)
        known={r[0] for r in store.db.execute('SELECT matchweek FROM brief_batches WHERE season_year=? AND content_version=?',(season_year,content_version))}
        # Keep tracked rounds visible after kickoff; don't flag all old unprepared rounds.
        active={w:fs for w,fs in rounds.items() if w in known or any(not f['kickoff_utc'] or utc(f['kickoff_utc'])>utc(now()) for f in fs.values())}
        require(known<=set(rounds),'Prepared matchweek missing from official schedule')
        reports=[round_status(store,season_year,w,content_version,fs,fresh,now()) for w,fs in active.items()]
        eligible=sorted((r for r in reports if r['status']=='eligible'),key=lambda r:r['first_kickoff'])
        if eligible:
            target=eligible[0]; week=target['matchweek']
            if not store.batch(season_year,week,content_version):
                # Capture reference and official rows atomically, then freeze observation time.
                with tempfile.TemporaryDirectory(prefix='brief-source-') as temp:
                    snapshot=Path(temp)/'source.db'
                    with closing(source_connection(source)) as src, closing(sqlite3.connect(snapshot)) as dest:
                        src.backup(dest, pages=256, sleep=0.01)
                    frozen=utc(now())
                    snap_rounds,snap_fresh=schedule(snapshot,season_year,frozen)
                    decision=round_status(store,season_year,week,content_version,snap_rounds[week],snap_fresh,frozen)
                    require(decision['status']=='eligible','Window or freshness changed during snapshot')
                    run_batch(snapshot,store_path,season_year=season_year,matchweek=week,
                              content_version=content_version,model=model,now=lambda:frozen)
            # Recheck the entire round and freshness after preparation, before any paid call.
            rounds,fresh=schedule(source,season_year,now())
            decision=round_status(store,season_year,week,content_version,rounds[week],fresh,now())
            if decision['status']=='eligible':
                run_batch(source,store_path,season_year=season_year,matchweek=week,content_version=content_version,
                    model=model,generate=True,retry_failed=True,now=now,writer=writer,resume_frozen=True,
                    max_entries=1,max_attempts=3,retry_delay_seconds=900,start_before=decision['target_at'])
            rounds,fresh=schedule(source,season_year,now())
            reports=[round_status(store,season_year,w,content_version,rounds[w],fresh,now()) for w in active]
        future=[r for r in reports if r['first_kickoff'] and utc(r['first_kickoff'])>utc(now())]
        upcoming=min(future,key=lambda r:r['first_kickoff']) if future else None
        last=max((r['last_successful_generation'] for r in reports if r['last_successful_generation']),default=None)
        report=dict(status='checked',upcoming_matchweek=upcoming['matchweek'] if upcoming else None,
                    last_successful_generation=last,rounds=reports,intervention_required=any(r['intervention_required'] for r in reports))
    except Exception:
        report=dict(status='unavailable',error_code='brief_scheduler_failed',intervention_required=True,rounds=[])
    finally:
        if store: store.close()
    operations.finish(operations_path,token,now(),report)
    return report
