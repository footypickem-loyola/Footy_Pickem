"""Offline canonical batch/concurrency/read-path regressions; never calls OpenAI."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from pick_insight_enrichment import verified_mapping
from pre_match_brief import CorrespondentError, all_facts, serialize
from shared_brief_context import build_round, validate_team_context, CONTEXT_VERSION, PROMPT_VERSION
from shared_brief_store import BriefStore, initialize, read_saved, digest, WHERE, key_values
from shared_brief_workflow import run_batch
from correspondent.shared_team_writer import generate_team_brief, load_team_prompt, build_team_request
from correspondent.pre_match_writer import validate_fixture_output


def output(context):
    f = context['fixture']
    fact = all_facts(f)[0]
    return dict(fixture_id=f['fixture_id'], picked_team=f['picked_team'], heading=f"{f['home']} vs {f['away']}",
                sentences=[dict(text=fact['claim'].replace('stored ', '').replace('last ', ''), used_fact_ids=[fact['id']])])


def fake_writer(context, model='test-model'):
    payload = output(context)
    validate_fixture_output(payload, context['fixture'])
    return dict(identity=context['identity'], output=payload, body=payload['sentences'][0]['text'], model=model,
        provider_response_id='mock-response', prompt_version=PROMPT_VERSION, context_version=CONTEXT_VERSION,
        context_sha256=digest(context), prompt_sha256=sha256(load_team_prompt().encode()).hexdigest())


class SharedBriefTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)/'game.db'
        self.store = Path(self.temp.name)/'content.db'
        self.now = datetime(2026, 9, 4, 15, tzinfo=timezone.utc)
        teams = list(verified_mapping().values())
        with closing(sqlite3.connect(self.source)) as db, db:
            db.executescript('''CREATE TABLE seasons(id INTEGER,api_competition_code TEXT,api_season_year INTEGER);
                INSERT INTO seasons VALUES(47,'PL',2026);
                CREATE TABLE weeks(id INTEGER,season_id INTEGER,number INTEGER);
                CREATE TABLE fixtures(id INTEGER,week_id INTEGER,external_match_id INTEGER,home TEXT,away TEXT,kickoff_utc TEXT);
                CREATE TABLE results(fixture_id INTEGER,outcome TEXT,home_score INTEGER,away_score INTEGER,source TEXT,updated_at TEXT);''')
            for week in range(1,7):
                db.execute('INSERT INTO weeks VALUES(?,?,?)',(week+50,47,week))
                for i in range(10):
                    date = datetime(2026,8,1,15,tzinfo=timezone.utc)+timedelta(days=7*(week-1),hours=i)
                    fid=week*100+i
                    db.execute('INSERT INTO fixtures VALUES(?,?,?,?,?,?)',(fid,week+50,fid+500000,teams[2*i],teams[2*i+1],date.isoformat()))
                    db.execute('INSERT INTO results VALUES(?,?,?,?,?,?)',(fid,'Home',2,0,'official',(date+timedelta(hours=2)).isoformat()))
        initialize(self.store)

    def slots(self, source=None):
        return build_round(source or self.source,season_year=2026,matchweek=6,as_of=self.now)

    def run_batch(self, **kwargs):
        return run_batch(self.source,self.store,season_year=2026,matchweek=6,content_version='v1',
                         model='test-model',now=lambda:self.now,**kwargs)

    def prepare(self):
        self.assertEqual(self.run_batch(), {'pending':20})

    def test_twenty_canonical_contexts_without_any_pick_or_player_tables(self):
        before=self.source.read_bytes()
        slots=self.slots()
        self.assertEqual(len(slots),20)
        self.assertEqual(len({s['identity']['external_fixture_id'] for s in slots}),10)
        self.assertEqual(len({s['identity']['team_id'] for s in slots}),20)
        for slot in slots:
            validate_team_context(slot['context'])
            self.assertEqual(slot['context']['fixture']['fixture_id'],slot['identity']['external_fixture_id'])
            self.assertNotIn('player',slot['context'])
            self.assertNotIn('picks',slot['context'])
            self.assertNotIn('47',str(slot['context']['identity']))
        self.assertEqual(before,self.source.read_bytes())

    def test_twenty_thin_team_contexts_keep_editorial_ranking_and_are_deterministic(self):
        from pre_match_brief import build_fixture_facts
        kwargs = dict(season_year=2026, matchweek=2, as_of='2026-08-07T15:00:00Z')
        before = self.source.read_bytes()
        actual = build_round(self.source, **kwargs)
        def previous_fallback(*args, **kw):
            kw['recent_form'] = False
            return build_fixture_facts(*args, **kw)
        with patch('shared_brief_context.build_fixture_facts', side_effect=previous_fallback):
            baseline = build_round(self.source, **kwargs)
        self.assertEqual(actual, build_round(self.source, **kwargs))
        self.assertEqual(len(actual), 20)
        self.assertEqual({s['team'] for s in actual}, set(verified_mapping().values()))
        for old, new in zip(baseline, actual):
            self.assertTrue(new['context'])
            self.assertEqual(old['context']['fixture']['candidates'], new['context']['fixture']['candidates'])
            self.assertNotIn('player', new['context'])
            self.assertNotIn('league_id', new['context'])
        self.assertEqual(before, self.source.read_bytes())

    def test_local_ids_and_league_copies_do_not_change_canonical_context(self):
        before=self.slots()
        other=Path(self.temp.name)/'other-league.db'
        shutil.copyfile(self.source,other)
        with closing(sqlite3.connect(other)) as db, db:
            db.executescript('''UPDATE seasons SET id=id+1000; UPDATE weeks SET id=id+1000,season_id=season_id+1000;
                UPDATE fixtures SET id=id+1000,week_id=week_id+1000; UPDATE results SET fixture_id=fixture_id+1000;''')
        self.assertEqual(before,self.slots(other))
        # Equivalent copies in one source still yield twenty, not forty.
        with closing(sqlite3.connect(self.source)) as db, db:
            db.executescript('''INSERT INTO seasons SELECT id+1000,api_competition_code,api_season_year FROM seasons;
                INSERT INTO weeks SELECT id+1000,season_id+1000,number FROM weeks;
                INSERT INTO fixtures SELECT id+1000,week_id+1000,external_match_id,home,away,kickoff_utc FROM fixtures;
                INSERT INTO results SELECT fixture_id+1000,outcome,home_score,away_score,source,updated_at FROM results;''')
        self.assertEqual(before,self.slots())

    def test_unknown_mapping_and_incomplete_round_fail_before_provider(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute('UPDATE fixtures SET external_match_id=NULL WHERE id=600')
        writer=Mock()
        with self.assertRaises(CorrespondentError): self.run_batch(generate=True,writer=writer)
        writer.assert_not_called()
        with closing(sqlite3.connect(self.store)) as db, db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM team_briefs').fetchone()[0],0)

    def test_cutoff_excludes_future_scores_and_refreshed_results(self):
        before=self.slots()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE results SET home_score=99,updated_at='invalid' WHERE fixture_id>=600")
        self.assertEqual(before,self.slots())
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE results SET updated_at='2026-10-05' WHERE fixture_id<600")
        self.assertTrue(all(s['context'] is None and s['error'] for s in self.slots()))
        self.assertEqual(self.run_batch(generate=True,writer=Mock(side_effect=AssertionError())),{'blocked':20})

    def test_generate_once_then_read_across_leagues_and_retry_failure_only(self):
        original=self.source.read_bytes()
        calls=[]
        def flaky(context,model):
            calls.append(context['identity'])
            if len(calls)==1: raise CorrespondentError('test invalid output')
            return fake_writer(context,model)
        self.assertEqual(self.run_batch(generate=True,writer=flaky),{'failed':1,'succeeded':19})
        self.assertEqual(self.run_batch(generate=True,writer=flaky),{'failed':1,'succeeded':19})
        self.assertEqual(len(calls),20)
        self.assertEqual(self.run_batch(generate=True,retry_failed=True,writer=flaky),{'succeeded':20})
        self.assertEqual(len(calls),21)
        self.assertEqual(self.run_batch(generate=True,retry_failed=True,writer=Mock(side_effect=AssertionError())),{'succeeded':20})
        for slot in self.slots():
            text=read_saved(self.store,slot['identity'],'v1',kickoff=slot['kickoff'],home=slot['home'],away=slot['away'],team=slot['team'])
            self.assertTrue(text)
        self.assertEqual(original,self.source.read_bytes())

    def test_parallel_workers_claim_each_key_once(self):
        self.prepare()
        calls=[]
        def write(context,model):
            calls.append((context['identity']['external_fixture_id'],context['identity']['team_id']))
            return fake_writer(context,model)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(self.run_batch,generate=True,writer=write) for _ in range(2)]
            for f in futures:f.result()
        self.assertEqual(len(calls),20)
        self.assertEqual(len(set(calls)),20)
        self.assertEqual(self.run_batch(),{'succeeded':20})

    def test_expired_claim_uncertain_and_old_worker_fenced(self):
        self.prepare()
        slot=self.slots()[0]
        store=BriefStore(self.store)
        self.addCleanup(store.close)
        first=store.claim(slot['identity'],'v1',self.now)
        later=self.now+timedelta(minutes=11)
        self.assertIsNone(store.claim(slot['identity'],'v1',later))
        second=store.claim(slot['identity'],'v1',later,retry_uncertain=True)
        self.assertTrue(second)
        self.assertFalse(store.finish(slot['identity'],'v1',first['token'],later,generated=fake_writer(first['context'])))
        self.assertTrue(store.finish(slot['identity'],'v1',second['token'],later,generated=fake_writer(second['context'])))

    def test_unknown_transport_outcome_requires_explicit_retry(self):
        writer=Mock(side_effect=TimeoutError('not logged'))
        self.assertEqual(self.run_batch(generate=True,writer=writer),{'uncertain':20})
        self.assertEqual(writer.call_count,20)
        self.run_batch(generate=True,retry_failed=True,writer=writer)
        self.assertEqual(writer.call_count,20)
        self.assertEqual(self.run_batch(generate=True,retry_uncertain=True,writer=fake_writer),{'succeeded':20})

    def test_kickoff_gate_before_call_and_after_response(self):
        self.prepare()
        self.now += timedelta(days=2)
        writer=Mock(side_effect=AssertionError())
        self.assertEqual(self.run_batch(generate=True,writer=writer),{'expired':20})
        writer.assert_not_called()

    def test_no_late_publication(self):
        self.prepare()
        slot=self.slots()[0]
        store=BriefStore(self.store)
        self.addCleanup(store.close)
        claim=store.claim(slot['identity'],'v1',self.now)
        self.assertFalse(store.finish(slot['identity'],'v1',claim['token'],self.now+timedelta(days=2),generated=fake_writer(claim['context'])))
        self.assertIsNone(read_saved(self.store,slot['identity'],'v1',kickoff=slot['kickoff'],home=slot['home'],away=slot['away'],team=slot['team']))

    def test_version_change_required_for_prompt_or_schedule_changes(self):
        self.prepare()
        with patch('shared_brief_workflow.load_team_prompt',return_value='changed'), self.assertRaises(CorrespondentError):
            self.run_batch(generate=True,writer=Mock(side_effect=AssertionError()))
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE fixtures SET kickoff_utc='2026-09-07T15:00:00+00:00' WHERE id=600")
        with self.assertRaises(CorrespondentError):self.run_batch()

    def test_store_refuses_game_db_and_corrupt_reads_are_unavailable(self):
        original=self.source.read_bytes()
        with self.assertRaises(CorrespondentError):initialize(self.source)
        self.assertEqual(original,self.source.read_bytes())
        slot=self.slots()[0]
        self.assertIsNone(read_saved(self.source,slot['identity'],'v1',kickoff=slot['kickoff'],home=slot['home'],away=slot['away'],team=slot['team']))
        self.run_batch(generate=True,writer=fake_writer)
        with closing(sqlite3.connect(self.store)) as db, db:
            db.execute("UPDATE team_briefs SET generated_json='{}'")
        self.assertIsNone(read_saved(self.store,slot['identity'],'v1',kickoff=slot['kickoff'],home=slot['home'],away=slot['away'],team=slot['team']))

    def test_single_writer_reuses_guards_and_preserves_metadata(self):
        c=self.slots()[0]['context']
        client=NS(responses=NS(create=Mock(return_value=NS(id='mock-id',status='completed',output_text=json.dumps(output(c))))))
        result=generate_team_brief(c,client=client,model='test-model')
        self.assertEqual(result['context_sha256'],digest(c))
        self.assertEqual(client.responses.create.call_count,1)
        self.assertEqual(client.responses.create.call_args.kwargs,build_team_request(c,model='test-model'))
        bad=output(c)
        bad['sentences'][0]['text']='The stored evidence confirms a win.'
        client.responses.create.return_value.output_text=json.dumps(bad)
        with self.assertRaises(CorrespondentError):generate_team_brief(c,client=client)
        prompt=load_team_prompt()
        self.assertIn('at 90+3',prompt)
        self.assertIn('club they scored for',prompt)
        self.assertNotIn('five',prompt)
        self.assertNotIn('committed picks',prompt)

    def test_schedule_change_during_provider_call_is_not_published(self):
        changed=[]
        def write(context,model):
            if not changed:
                changed.append(context['identity']['external_fixture_id'])
                with closing(sqlite3.connect(self.source)) as db, db:
                    db.execute("UPDATE fixtures SET kickoff_utc='2026-09-08' WHERE external_match_id=?",(changed[0],))
            return fake_writer(context,model)
        result=self.run_batch(generate=True,writer=write)
        self.assertEqual(result,{'failed':2,'succeeded':18})

    def test_read_adapter_maps_different_local_ids_to_same_shared_content(self):
        from shared_brief_view import selected_briefs
        self.run_batch(generate=True,writer=fake_writer)
        slots=self.slots()[::2][:5]
        def load(offset):
            fixtures=[NS(id=offset+i,external_match_id=s['identity']['external_fixture_id'],home=s['home'],away=s['away'],
                         kickoff_utc=s['kickoff']) for i,s in enumerate(slots)]
            picks=[dict(fixture_id=f.id,home=f.home,away=f.away,team=s['team']) for f,s in zip(fixtures,slots)]
            db=Mock()
            db.query.return_value.filter.return_value.all.return_value=fixtures
            return selected_briefs(db,NS(Fixture=NS(id=Mock(),week_id=Mock())),
                    NS(api_competition_code='PL',api_season_year=2026),NS(id=offset),picks)
        with patch.dict(os.environ,{'SHARED_BRIEF_STORE':str(self.store),'SHARED_BRIEF_CONTENT_VERSION':'v1'}), \
             patch('shared_brief_workflow.run_batch',side_effect=AssertionError('GET cannot orchestrate')), \
             patch('correspondent.shared_team_writer.generate_team_brief',side_effect=AssertionError('GET cannot generate')):
            first=load(1)
            self.assertEqual(first,load(5000))
            self.assertEqual(len(first),5)
            self.assertTrue(all(r['body'] for r in first))


if __name__ == '__main__':
    unittest.main()
