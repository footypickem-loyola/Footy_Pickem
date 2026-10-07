"""Offline automatic timing/recovery tests. No paid provider or production writes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock, patch

import test_shared_briefs as shared
from shared_brief_scheduler import tick
from shared_brief_workflow import run_batch
from shared_brief_store import BriefStore
from pre_match_brief import CorrespondentError
import shared_brief_operations as operations


class SchedulerTests(unittest.TestCase):
    setUpBase=shared.SharedBriefTests.setUp

    def setUp(self):
        self.setUpBase()
        self.ops=Path(self.temp.name)/'ops.db'
        operations.initialize(self.ops)
        self.time=self.now
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("ALTER TABLE fixtures ADD COLUMN api_status TEXT DEFAULT 'TIMED'")
            db.execute('''CREATE TABLE api_sync_states(season_id INTEGER,provider TEXT,last_success_at TEXT,last_error TEXT,unmatched_matches INTEGER)''')
            db.execute("INSERT INTO api_sync_states VALUES(47,'football-data.org',?,NULL,0)",((self.time-timedelta(minutes=1)).isoformat(),))

    def fresh(self):
        with closing(sqlite3.connect(self.source)) as db,db:
            db.execute('UPDATE api_sync_states SET last_success_at=?',((self.time-timedelta(minutes=1)).isoformat(),))

    def tick(self,writer=None):
        return tick(self.source,self.store,self.ops,content_version='auto-v1',model='test-model',
                    now=lambda:self.time,writer=writer or shared.fake_writer)

    def target(self,report):
        self.assertEqual(report['status'],'checked',report)
        return next(r for r in report['rounds'] if r['matchweek']==6)

    def test_30_hour_boundary_and_exact_18_hour_stop(self):
        writer=Mock(side_effect=shared.fake_writer)
        self.time=self.now-timedelta(hours=6,seconds=1); self.fresh()
        self.assertEqual(self.target(self.tick(writer))['status'],'waiting')
        writer.assert_not_called()
        self.time=self.now-timedelta(hours=6); self.fresh()
        self.assertEqual(self.target(self.tick(writer))['successful'],1)
        self.time=self.now+timedelta(hours=6); self.fresh()
        r=self.target(self.tick(writer))
        self.assertEqual(r['status'],'hard_window_missed'); self.assertTrue(r['intervention_required'])
        self.assertEqual(writer.call_count,1)

    def test_friday_and_midweek_are_driven_by_actual_first_kickoff(self):
        for day_shift in (-1,-3):
            with self.subTest(day_shift=day_shift):
                # First kickoff Friday or Wednesday; no Saturday convention.
                with closing(sqlite3.connect(self.source)) as db,db:
                    for i in range(10):
                        dt=self.now+timedelta(days=1+day_shift,hours=i)
                        db.execute('UPDATE fixtures SET kickoff_utc=? WHERE week_id=56 AND id=?',(dt.isoformat(),600+i))
                self.time=self.now+timedelta(days=day_shift)-timedelta(hours=6,seconds=1); self.fresh()
                r=self.target(self.tick())
                self.assertEqual(r['status'],'waiting')
                self.assertEqual(r['eligible_at'],(self.now+timedelta(days=day_shift,hours=-6)).isoformat())
                self.assertEqual(r['target_at'],(self.now+timedelta(days=day_shift)).isoformat())
                self.assertEqual(r['hard_stop_at'],(self.now+timedelta(days=day_shift,hours=6)).isoformat())

    def test_schedule_changes_after_partial_and_complete_require_intervention(self):
        self.tick()
        with closing(sqlite3.connect(self.source)) as db,db:
            db.execute("UPDATE fixtures SET kickoff_utc='2026-09-06T15:00:00+00:00' WHERE id=600")
        writer=Mock(side_effect=shared.fake_writer)
        r=self.target(self.tick(writer))
        self.assertEqual(r['status'],'schedule_changed'); self.assertTrue(r['intervention_required'])
        writer.assert_not_called()

    def test_stale_official_data_prevents_preparation(self):
        self.time=self.now+timedelta(minutes=16)
        writer=Mock()
        r=self.target(self.tick(writer))
        self.assertEqual(r['status'],'readiness_target_missed_recovering')
        self.assertEqual(r['blocking_reasons'],['official_data_stale'])
        self.assertFalse(r['generation_allowed'])
        writer.assert_not_called()
        s=BriefStore(self.store)
        try: self.assertIsNone(s.batch(2026,6,'auto-v1'))
        finally: s.close()

    def test_duplicate_and_overlapping_ticks_one_attempt_per_identity(self):
        writer=Mock(side_effect=shared.fake_writer)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.tick(writer),range(2)))
        self.assertTrue(all(r['status'] in ('checked','already_running') for r in results))
        identities=[json.dumps(c.args[0]['identity'],sort_keys=True) for c in writer.call_args_list]
        self.assertEqual(len(identities),len(set(identities)))

    def test_twenty_saved_reused_after_restart_without_rebuilding_facts(self):
        before=self.source.read_bytes()
        writer=Mock(side_effect=shared.fake_writer)
        self.tick(writer)
        # New imports, including refreshed official timestamps, cannot change saved context.
        with patch('shared_brief_workflow.build_round',side_effect=AssertionError('must use frozen context')):
            for _ in range(19): r=self.tick(writer)
            self.assertEqual(self.target(r)['successful'],20)
            self.assertEqual(self.target(self.tick(writer))['status'],'ready_on_time')
        self.assertEqual(writer.call_count,20)
        self.assertEqual(before,self.source.read_bytes())
        self.assertEqual(len({c.args[0]['as_of'] for c in writer.call_args_list}),1)
        self.assertTrue(self.target(r)['last_successful_generation'])

    def test_updated_results_after_preparation_do_not_remove_or_add_facts(self):
        first=Mock(side_effect=shared.fake_writer); self.tick(first)
        with closing(sqlite3.connect(self.source)) as db,db:
            db.execute('UPDATE results SET home_score=99,updated_at=?',((self.time+timedelta(days=1)).isoformat(),))
        writer=Mock(side_effect=shared.fake_writer)
        self.tick(writer)
        self.assertEqual(writer.call_count,1)
        self.assertNotIn('99-',json.dumps(writer.call_args.args[0]))
        self.assertEqual(writer.call_args.args[0]['as_of'],first.call_args.args[0]['as_of'])

    def test_known_failure_retries_bounded_and_uncertain_never_retries(self):
        calls={}
        def provider(c,model):
            key=c['identity']['team_id'];calls[key]=calls.get(key,0)+1
            if key==117: raise TimeoutError('secret must never be logged')
            raise CorrespondentError('known invalid output')
        # Each cycle prioritizes pending entries while failed rows cool down.
        for _ in range(20): self.tick(provider)
        self.assertTrue(all(n==1 for n in calls.values()))
        self.tick(provider)
        self.assertTrue(all(n==1 for n in calls.values()))
        for attempt in range(2):
            self.time+=timedelta(minutes=15);self.fresh()
            for _ in range(20): final=self.tick(provider)
        self.assertEqual(calls[117],1)
        self.assertTrue(all(n==3 for k,n in calls.items() if k!=117))
        self.assertTrue(self.target(final)['intervention_required'])
        self.assertNotIn('secret',json.dumps(final))

    def test_restart_stale_scheduler_lease_and_uncertain_entry(self):
        token=operations.claim(self.ops,self.time)
        self.assertEqual(self.tick()['status'],'already_running')
        self.time+=timedelta(minutes=6);self.fresh()
        self.tick()
        operations.finish(self.ops,token,self.time,dict(status='stale-worker'))
        self.assertEqual(operations.read_status(self.ops,self.time)['status'],'checked')
        s=BriefStore(self.store)
        try:
            slots=s.frozen_slots(2026,6,'auto-v1')
            claimed=next(slot for slot in slots if s.claim(slot['identity'],'auto-v1',self.time))
        finally:s.close()
        self.time+=timedelta(minutes=11);self.fresh()
        final=self.tick()
        entry=next(e for e in self.target(final)['entries'] if e['fixture_id']==claimed['identity']['external_fixture_id'] and e['team_id']==claimed['identity']['team_id'])
        self.assertEqual(entry['status'],'uncertain')

    def test_deadline_alert_if_no_worker_ran_in_window_and_stale_heartbeat(self):
        self.time+=timedelta(hours=6);self.fresh()
        writer=Mock()
        self.assertEqual(self.target(self.tick(writer))['status'],'hard_window_missed')
        writer.assert_not_called()
        r=operations.read_status(self.ops,self.time+timedelta(minutes=6))
        self.assertTrue(r['heartbeat_stale']);self.assertTrue(r['intervention_required'])

    def test_success_arriving_after_target_is_explicitly_late(self):
        for _ in range(19):self.tick()
        self.time=self.now+timedelta(hours=6,seconds=-1);self.fresh()
        def slow(c,model):
            result=shared.fake_writer(c,model)
            self.time+=timedelta(seconds=2)
            return result
        r=self.target(self.tick(slow))
        self.assertEqual(r['status'],'completed_late');self.assertTrue(r['intervention_required'])

    def test_blocked_contexts_stay_frozen_when_new_results_arrive(self):
        with closing(sqlite3.connect(self.source)) as db,db: db.execute('DELETE FROM results')
        writer=Mock()
        r=self.target(self.tick(writer))
        self.assertEqual(r['counts'],{'blocked':20})
        with patch('shared_brief_workflow.build_round',side_effect=AssertionError()):
            r=self.target(self.tick(writer))
        self.assertEqual(r['status'],'readiness_target_missed_recovering')
        self.assertTrue(r['intervention_required']);self.assertFalse(r['generation_allowed']);writer.assert_not_called()

    def test_readiness_deadline_alerts_immediately_but_allows_safe_recovery(self):
        self.time=self.now-timedelta(seconds=1);self.fresh()
        writer=Mock(side_effect=shared.fake_writer)
        r=self.target(self.tick(writer))
        self.assertEqual(r['status'],'generating_before_target')
        self.assertFalse(r['readiness_target_missed'])
        self.time=self.now;self.fresh()
        r=self.target(self.tick(writer))
        self.assertEqual(r['status'],'readiness_target_missed_recovering')
        self.assertTrue(r['readiness_target_missed']);self.assertTrue(r['intervention_required'])
        self.assertEqual(writer.call_count,2)
        self.time+=timedelta(seconds=1)
        for _ in range(18):r=self.target(self.tick(writer))
        self.assertEqual(r['status'],'completed_late')
        self.assertEqual(writer.call_count,20)
        self.tick(writer);self.assertEqual(writer.call_count,20)

    def test_ready_on_time_stays_ready_after_hard_stop(self):
        for _ in range(20):self.tick()
        self.time+=timedelta(hours=8)
        writer=Mock()
        r=self.target(self.tick(writer))
        self.assertEqual(r['status'],'ready_on_time')
        self.assertFalse(r['intervention_required']);writer.assert_not_called()

    def test_known_failure_recovers_after_readiness_target_without_retrying_uncertain(self):
        self.time=self.now-timedelta(minutes=1);self.fresh()
        self.tick(Mock(side_effect=CorrespondentError('invalid')))
        self.tick(Mock(side_effect=TimeoutError('unknown')))
        self.time=self.now+timedelta(minutes=15);self.fresh()
        writer=Mock(side_effect=shared.fake_writer)
        for _ in range(20):r=self.target(self.tick(writer))
        self.assertEqual(r['counts'],{'succeeded':19,'uncertain':1})
        self.assertEqual(writer.call_count,19)
        self.assertEqual(r['status'],'readiness_target_missed_recovering')

    def test_operator_store_refuses_game_database(self):
        before=self.source.read_bytes()
        with self.assertRaises(CorrespondentError):operations.initialize(self.source)
        self.assertEqual(before,self.source.read_bytes())
