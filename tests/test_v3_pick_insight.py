import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as Obj

from v3_pick_insight import build_pick_insight


class PickInsightMetricsTests(unittest.TestCase):
    def fixture(self, id=99, home='Arsenal', away='Leeds', day=20):
        return Obj(id=id, home=home, away=away, match_number=id,
                   kickoff_utc=datetime(2026, 9, day) if day else None)

    def row(self, id, outcome, home_score, away_score, home='Arsenal', day=None):
        return (self.fixture(id, home, 'Arsenal' if home != 'Arsenal' else 'Other', day or id),
                Obj(outcome=outcome, home_score=home_score, away_score=away_score), id)

    def build(self, rows=(), fixture=None, **kwargs):
        return build_pick_insight(fixture=fixture or self.fixture(), team='Arsenal', week_number=10,
                                  results=rows, now=datetime(2026, 9, 25, tzinfo=timezone.utc), **kwargs)

    def test_six_metrics_venue_form_and_averages(self):
        rows = [self.row(1, 'Home', 3, 1), self.row(2, 'Draw', 2, 2, home='Chelsea'),
                self.row(3, 'Away', 0, 1), self.row(4, 'Away', 0, 2, home='Everton')]
        view = self.build(reversed(rows))
        self.assertEqual([b['value'] for b in view['bands']], ['2W 1D 1L', '1W 0D 1L', 'W D L W', '1.8', '1.0', '—'])
        self.assertEqual(view['opponent'], 'Leeds')
        self.assertEqual(view['venue'], 'Home vs')
        away = self.build(rows, fixture=self.fixture(home='Leeds', away='Arsenal'))
        self.assertEqual(away['bands'][1], dict(label='Away Record', value='1W 1D 0L'))
        self.assertEqual(away['venue'], 'Away at')

    def test_empty_history_and_no_venue_games(self):
        self.assertEqual([b['value'] for b in self.build()['bands']], ['0W 0D 0L', '0W 0D 0L', '—', '—', '—', '—'])
        view = self.build([self.row(1, 'Away', 0, 2, home='Everton')])
        self.assertEqual(view['bands'][1]['value'], '0W 0D 0L')
        self.assertEqual(view['bands'][2]['value'], 'W')
        self.assertIsNone(view['crest'])

    def test_outcome_only_manual_override_does_not_invent_zero_goals(self):
        view = self.build([self.row(1, 'Home', None, None), self.row(2, 'Away', 0, 2)])
        self.assertEqual(view['bands'][0]['value'], '1W 0D 1L')
        self.assertEqual(view['bands'][3]['value'], '—')
        self.assertEqual(view['bands'][4]['value'], '—')

    def test_last_five_excludes_selected_fixture_and_later_results(self):
        rows = [self.row(i, 'Home' if i % 2 else 'Draw', 1, 0) for i in range(1, 8)]
        rows += [self.row(99, 'Away', 0, 10, day=20), self.row(8, 'Away', 0, 10, day=21)]
        view = self.build(rows)
        self.assertEqual(view['sample'], 7)
        self.assertEqual(view['bands'][2]['value'], 'W D W D W')

    def test_missing_kickoff_uses_prior_weeks_and_stable_order(self):
        rows = [(self.fixture(2, day=None), Obj(outcome='Away', home_score=0, away_score=1), 2),
                (self.fixture(1, day=None), Obj(outcome='Home', home_score=1, away_score=0), 1),
                (self.fixture(3, day=None), Obj(outcome='Draw', home_score=0, away_score=0), 10)]
        self.assertEqual(self.build(rows, fixture=self.fixture(day=None))['bands'][2]['value'], 'W L')

    def test_result_in_future_is_not_counted_and_crest_can_be_supplied(self):
        view = self.build([self.row(1, 'Home', 5, 0, day=28)], fixture=self.fixture(day=30), crest='/static/club.svg')
        self.assertEqual(view['sample'], 0)
        self.assertEqual(view['crest'], '/static/club.svg')
