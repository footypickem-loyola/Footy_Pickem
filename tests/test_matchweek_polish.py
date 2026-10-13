"""Deterministic presentation/rollover regressions using isolated fixture data."""
import unittest
from datetime import timedelta
from html.parser import HTMLParser
from unittest.mock import patch

import test_live_matchweek as live_tests
from test_live_matchweek import d, NOW, payload


class DetailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.dialogs = []
        self.openers = []
        self.depth = 0
        self.max_depth = 0
        self.card_depth = 0
        self.trigger = None
        self.triggers = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'article':
            self.card_depth += 1
        if self.trigger is not None:
            self.trigger['children'].append(tag)
        if tag == 'dialog':
            self.dialogs.append(attrs['id'])
        if 'data-matchup-open' in attrs:
            self.openers.append(attrs['data-matchup-open'])
            self.trigger = dict(tag=tag, attrs=attrs, children=[], in_article=self.card_depth > 0)
            self.triggers.append(self.trigger)
        if tag == 'details':
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)

    def handle_endtag(self, tag):
        if tag == 'article':
            self.card_depth -= 1
        if tag == 'button':
            self.trigger = None
        if tag == 'details':
            self.depth -= 1


class PolishTests(unittest.TestCase):
    setUp = live_tests.LiveTests.setUp
    tearDown = live_tests.LiveTests.tearDown
    sync = live_tests.LiveTests.sync
    model = live_tests.LiveTests.model

    def next_week(self):
        week = d.Week(season_id=self.week.season_id, number=2, status='drafting', room_code='NEXT')
        self.db.add(week)
        self.db.commit()
        return week

    def test_training_stays_in_draft_preview_and_live_then_advances_without_grace(self):
        next_week = self.next_week()
        for status in ('drafting', 'provisional'):
            self.week.status = status
            self.db.commit()
            self.assertEqual(d.training_ground_week(self.db, self.week.season).id, self.week.id)
        self.sync(payload(self.fixtures[0]))
        self.assertEqual(d.training_ground_week(self.db, self.week.season).id, self.week.id)
        with d.app.test_request_context('/tab/stats?season=pr12-local'):
            with patch.object(d, 'training_ground_week', wraps=d.training_ground_week) as selector:
                d.insight_context()
                selector.assert_called_once()
        self.week.status = 'finalized'
        self.week.finalized_at = NOW
        self.db.add(self.week)
        self.db.commit()
        self.assertEqual(d.training_ground_week(self.db, self.week.season).id, next_week.id)
        self.assertEqual(d.select_default_matchweek([next_week, self.week], NOW).id, self.week.id)
        self.assertEqual(d.select_default_matchweek([next_week, self.week], NOW + timedelta(hours=12)).id, next_week.id)

    def test_training_waits_for_last_fixture_including_provisional_finals(self):
        next_week = self.next_week()
        self.sync(*(payload(f, state=5) for f in self.fixtures[:-1]))
        self.assertEqual(d.training_ground_week(self.db, self.week.season).id, self.week.id)
        self.sync(payload(self.fixtures[-1], state=18))
        self.assertEqual(d.training_ground_week(self.db, self.week.season).id, self.week.id)
        self.sync(payload(self.fixtures[-1], state=5))
        self.assertEqual(d.training_ground_week(self.db, self.week.season).id, next_week.id)
        self.assertEqual(d.select_default_matchweek([self.week, next_week], NOW).id, self.week.id)

    def test_training_official_finals_and_end_of_season(self):
        for f in self.fixtures:
            self.db.add(d.Result(fixture_id=f.id, outcome='Draw', home_score=0, away_score=0, source='manual'))
        self.db.commit()
        self.assertEqual(d.training_ground_week(self.db, self.week.season).id, self.week.id)
        next_week = self.next_week()
        self.assertEqual(d.training_ground_week(self.db, self.week.season).id, next_week.id)

    def test_settled_counts_exclude_live_upcoming_and_unknown_results(self):
        self.sync(payload(self.fixtures[0], state=5),
                  payload(self.fixtures[2], state=5, score=(0, 1)),
                  payload(self.fixtures[4], state=5, score=(0, 0)),
                  payload(self.fixtures[6], score=(1, 0)))
        player = self.model()['primary']['players'][0]
        self.assertEqual(player['pick_record'], {'correct': 1, 'incorrect': 1, 'draw': 1})
        self.assertEqual(player['for'], 1)  # Live score still contributes to projection.
        raw = payload(self.fixtures[8], state=5)
        raw['scores'] = []
        self.sync(raw)
        self.assertEqual(self.model()['primary']['players'][0]['pick_record'], player['pick_record'])
        self.assertEqual(self.model()['primary']['players'][0]['completed'], 4)
        self.db.add(d.Result(fixture_id=self.fixtures[0].id, outcome='Away', home_score=0, away_score=2, source='manual'))
        self.db.commit()
        self.assertEqual(self.model()['primary']['players'][0]['pick_record'], {'correct': 0, 'incorrect': 2, 'draw': 1})

    def test_other_dialogs_render_draft_preview_live_and_single_history_control(self):
        for state in ('ready', 'live', 'draft'):
            if state == 'live':
                self.sync(payload(self.fixtures[0]))
            if state == 'draft':
                self.db.query(d.Pick).filter(d.Pick.fixture_id != self.fixtures[0].id).delete(synchronize_session=False)
                self.db.commit()
            model = self.model()
            self.assertEqual(model['primary']['state'], state)
            with d.app.test_client() as client:
                html = client.get('/tab/current?season=pr12-local&force_week=1').get_data(as_text=True)
            parser = DetailParser()
            parser.feed(html)
            self.assertEqual(len(parser.openers), len(model['others']))
            for trigger in parser.triggers:
                self.assertTrue(trigger['in_article'])
                self.assertEqual(trigger['tag'], 'button')
                self.assertEqual(trigger['children'], [])
                self.assertEqual(trigger['attrs']['type'], 'button')
                self.assertEqual(trigger['attrs']['aria-haspopup'], 'dialog')
                self.assertEqual(trigger['attrs']['aria-controls'], trigger['attrs']['data-matchup-open'])
                self.assertIn('versus', trigger['attrs']['aria-label'])
                self.assertNotIn('tabindex', trigger['attrs'])  # Native keyboard focus.
            for other in model['others']:
                target = f'other-matchup-{other["id"]}'
                self.assertIn(target, parser.openers)
                self.assertIn(target, parser.dialogs)
                dialog = html.split(f'<dialog id="{target}"')[1].split('</dialog>')[0]
                for player in other['players']:
                    self.assertIn(player['name'], dialog)
                    for pick in player['owned']:
                        self.assertIn(f'data-fixture-id="{pick["fixture_id"]}"', dialog)
                        self.assertIn(pick['team'], dialog)
            self.assertEqual(parser.max_depth, 1)
            if state == 'live':
                self.assertIn('<dt>MP</dt>', html)
                self.assertIn('5 fixtures · Correct 0 · Incorrect 0 · Draws 0 · Net', html)
                self.assertNotIn('live-player-totals', html)
                self.assertIn('View Draft History</summary>  <ol', html)
