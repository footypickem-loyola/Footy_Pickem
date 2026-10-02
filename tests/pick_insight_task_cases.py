"""Authenticated finalization-task regressions in the isolated app DB."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from unittest.mock import patch

from pick_insight_enrichment import verified_mapping
from pick_insight_tasks import run_task, TaskBusy
from sportmonks_season import SeasonClient, SeasonSyncError


class PickInsightTaskCases:
    def insight_task_context(self, enqueue=True):
        import pickem_flask_htmx_tabs as d
        db = d.SessionLocal()
        week = db.query(d.Week).filter_by(number=1).one()
        week.season.api_competition_code = 'PL'
        week.season.api_season_year = 2026
        clubs = list(verified_mapping().values())
        fixtures = db.query(d.Fixture).filter_by(week_id=week.id).order_by(d.Fixture.id).all()
        for i, fixture in enumerate(fixtures):
            fixture.home, fixture.away = clubs[2*i:2*i+2]
            db.add(d.Result(fixture_id=fixture.id, outcome='Home', source='manual'))
        db.flush()
        week.status = 'provisional'
        if enqueue:
            d.update_week_status(db, week)
        else:
            week.status = 'finalized'
            db.commit()
        week_id = week.id
        db.close()
        return d, week_id

    def insight_task_provider(self):
        return json.loads((Path(__file__).parent / 'fixtures' / 'sportmonks-season-28083-sanitized.json').read_text(encoding='utf-8-sig'))

    def test_insight_task_auth_and_request_validation(self):
        import pickem_flask_htmx_tabs as d
        with d.app.test_client() as client, patch.object(SeasonClient, 'participants') as provider:
            with patch.dict(os.environ, {'SYNC_SECRET': ''}):
                self.assertEqual(client.post('/tasks/sync-pick-insight').status_code, 503)
            with patch.dict(os.environ, {'SYNC_SECRET': 'task-test'}):
                self.assertEqual(client.post('/tasks/sync-pick-insight').status_code, 403)
                self.assertEqual(client.post('/tasks/sync-pick-insight', headers={'X-Sync-Secret':'wrong'}).status_code, 403)
                self.assertEqual(client.get('/tasks/sync-pick-insight').status_code, 405)
                for payload in ({'week_id': True}, {'week_id': -1}, {'force': True}, [], {'week_id': None}):
                    self.assertEqual(client.post('/tasks/sync-pick-insight', json=payload,
                        headers={'X-Sync-Secret':'task-test'}).status_code, 400)
                self.assertEqual(client.post('/tasks/sync-pick-insight', data='x'*1025,
                    headers={'X-Sync-Secret':'task-test'}).status_code, 400)
            provider.assert_not_called()

    def test_insight_task_success_repeat_and_no_work_are_idempotent(self):
        d, wid = self.insight_task_context()
        export = self.insight_task_provider()
        with d.app.test_client() as client, patch.dict(os.environ, {'SYNC_SECRET':'task-test'}), \
             patch.object(SeasonClient, 'participants', return_value=export['teams']) as participants, \
             patch.object(SeasonClient, 'scorers', return_value=[export['goal_topscorer_example']]) as scorers:
            def call(body):
                return client.post('/tasks/sync-pick-insight', json=body, headers={'X-Sync-Secret':'task-test'})
            response = call({})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, dict(ok=True,status='completed',clubs=20,clubs_with_scorers=1))
            self.assertEqual(call({'week_id':wid}).json['status'], 'already_completed')
            self.assertEqual(call({}).json['status'], 'no_work')
            self.assertEqual(participants.call_count,1)
            self.assertEqual(scorers.call_count,1)
        with d.SessionLocal() as db:
            self.assertEqual(db.query(d.ClubSeasonEnrichment).count(),20)
            self.assertEqual(db.get(d.PickInsightRefresh,wid).status,'completed')

    def test_insight_task_failure_preserves_cache_and_finalization_then_retry(self):
        d, wid = self.insight_task_context()
        export = self.insight_task_provider()
        with d.SessionLocal() as db:
            sid = db.get(d.Week,wid).season_id
            old = datetime(2026,9,1)
            db.add(d.ClubSeasonEnrichment(season_id=sid,sportmonks_team_id=9,
                sportmonks_season_id=28083,league_id=8,footy_club='Man City',goals=2,
                top_scorers=[dict(player_id=1,name='Previous')],synced_at=old))
            db.commit()
        with d.app.test_client() as client, patch.dict(os.environ, {'SYNC_SECRET':'task-test'}), \
             patch.object(SeasonClient,'participants',return_value=export['teams']), \
             patch.object(SeasonClient,'scorers',side_effect=SeasonSyncError('secret provider payload /data/private.db')) as scorer:
            response = client.post('/tasks/sync-pick-insight',headers={'X-Sync-Secret':'task-test'})
            self.assertEqual(response.status_code,502)
            self.assertNotIn('secret',response.get_data(as_text=True))
            self.assertNotIn('/data',response.get_data(as_text=True))
            with d.SessionLocal() as db:
                row = db.get(d.ClubSeasonEnrichment,(sid,9))
                self.assertEqual((row.goals,row.synced_at),(2,old))
                self.assertEqual(db.get(d.Week,wid).status,'finalized')
                self.assertEqual(db.get(d.PickInsightRefresh,wid).status,'pending')
            scorer.side_effect=None
            scorer.return_value=[export['goal_topscorer_example']]
            self.assertEqual(client.post('/tasks/sync-pick-insight',json={'week_id':wid},
                headers={'X-Sync-Secret':'task-test'}).status_code,200)

    def test_insight_finalization_enqueues_once_without_provider_and_rolls_back(self):
        d, wid = self.insight_task_context(enqueue=False)
        with d.SessionLocal() as db, patch.object(SeasonClient,'participants',side_effect=AssertionError('No provider')):
            week=db.get(d.Week,wid)
            week.status='provisional'
            db.commit()
            d._set_week_status_without_commit(db,week)
            db.flush()
            self.assertEqual(db.query(d.PickInsightRefresh).count(),1)
            db.rollback()
            self.assertEqual(db.get(d.Week,wid).status,'provisional')
            self.assertEqual(db.query(d.PickInsightRefresh).count(),0)
            d.update_week_status(db,week)
            d.update_week_status(db,week)
            self.assertEqual(db.query(d.PickInsightRefresh).count(),1)
            week.status='provisional'
            db.commit()
            d.update_week_status(db,week)
            self.assertEqual(db.query(d.PickInsightRefresh).count(),1)

    def test_insight_manual_recovery_of_missed_transition(self):
        d,wid=self.insight_task_context(enqueue=False)
        export=self.insight_task_provider()
        with d.app.test_client() as client, patch.dict(os.environ,{'SYNC_SECRET':'task-test'}), \
             patch.object(SeasonClient,'participants',return_value=export['teams']), \
             patch.object(SeasonClient,'scorers',return_value=[]):
            self.assertEqual(client.post('/tasks/sync-pick-insight',json={'week_id':wid},
                headers={'X-Sync-Secret':'task-test'}).json['status'],'completed')

    def test_insight_task_busy_and_expired_claim_recovery(self):
        d,wid=self.insight_task_context()
        export=self.insight_task_provider()
        with d.SessionLocal() as db:
            task=db.get(d.PickInsightRefresh,wid)
            task.status='running'
            task.claim_token='other-worker'
            task.lease_until=datetime.utcnow()+timedelta(minutes=10)
            db.commit()
        with d.app.test_client() as client, patch.dict(os.environ,{'SYNC_SECRET':'task-test'}), \
             patch.object(SeasonClient,'participants',return_value=export['teams']) as provider, \
             patch.object(SeasonClient,'scorers',return_value=[]):
            self.assertEqual(client.post('/tasks/sync-pick-insight',headers={'X-Sync-Secret':'task-test'}).status_code,409)
            provider.assert_not_called()
            with d.SessionLocal() as db:
                db.get(d.PickInsightRefresh,wid).lease_until=datetime.utcnow()-timedelta(seconds=1)
                db.commit()
            self.assertEqual(client.post('/tasks/sync-pick-insight',headers={'X-Sync-Secret':'task-test'}).status_code,200)

    def test_insight_expired_worker_cannot_publish_cache_or_complete_task(self):
        d,wid=self.insight_task_context()
        export=self.insight_task_provider()
        now=[datetime.utcnow()]
        def slow_fetch():
            now[0]+=timedelta(minutes=11)
            return []
        with d.SessionLocal() as db, patch.object(SeasonClient,'participants',return_value=export['teams']), \
             patch.object(SeasonClient,'scorers',side_effect=slow_fetch):
            with self.assertRaises(TaskBusy):
                run_task(db,d,SeasonClient(),wid,clock=lambda:now[0])
            self.assertEqual(db.query(d.ClubSeasonEnrichment).count(),0)
            self.assertEqual(db.get(d.PickInsightRefresh,wid).status,'pending')

    def test_insight_claim_serializes_different_weeks_in_same_season(self):
        d,wid=self.insight_task_context()
        with d.SessionLocal() as db:
            sid=db.get(d.Week,wid).season_id
            other=d.Week(season_id=sid,number=2,room_code='LOCALTEST',status='finalized')
            db.add(other)
            db.flush()
            db.add(d.PickInsightRefresh(week_id=other.id,season_id=sid,status='running',
                created_at=datetime.utcnow(),claim_token='another-week',
                lease_until=datetime.utcnow()+timedelta(minutes=10)))
            db.commit()
        with d.app.test_client() as client, patch.dict(os.environ,{'SYNC_SECRET':'task-test'}), \
             patch.object(SeasonClient,'participants') as provider:
            self.assertEqual(client.post('/tasks/sync-pick-insight',json={'week_id':wid},
                headers={'X-Sync-Secret':'task-test'}).status_code,409)
            provider.assert_not_called()

    def test_insight_task_rejects_incomplete_or_wrong_season_without_provider(self):
        d,wid=self.insight_task_context(enqueue=False)
        with d.SessionLocal() as db:
            fixture=db.query(d.Fixture).filter_by(week_id=wid).first()
            db.query(d.Result).filter_by(fixture_id=fixture.id).delete()
            db.commit()
        with d.app.test_client() as client, patch.dict(os.environ,{'SYNC_SECRET':'task-test'}), \
             patch.object(SeasonClient,'participants') as provider:
            self.assertEqual(client.post('/tasks/sync-pick-insight',json={'week_id':wid},
                headers={'X-Sync-Secret':'task-test'}).status_code,409)
            with d.SessionLocal() as db:
                db.get(d.Week,wid).season.api_season_year=2025
                db.commit()
            self.assertEqual(client.post('/tasks/sync-pick-insight',headers={'X-Sync-Secret':'task-test'}).status_code,409)
            provider.assert_not_called()
