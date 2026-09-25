import unittest
from types import SimpleNamespace

from v3_analytics import matchup_matrix, performance_chart, position_chart, weekly_results


class AnalyticsPresentationTests(unittest.TestCase):
    def test_weekly_results_uses_matchup_net_and_reverses_perspective(self):
        players = [SimpleNamespace(id=1, name='Steve'), SimpleNamespace(id=2, name='Scott')]
        meetings = [dict(week=1, a=1, b=2, net=6), dict(week=2, a=1, b=2, net=-2),
                    dict(week=3, a=1, b=2, net=0)]
        own = dict(correct=3, incorrect=1, draws=1)
        other = dict(correct=1, incorrect=2, draws=2)
        records = {1: {1: own, 2: other}, 2: {1: own, 2: other}, 3: {1: other, 2: own}}
        rows = weekly_results(players, meetings, records, 1)
        self.assertEqual([r['week'] for r in rows], [3, 2, 1])
        self.assertEqual([r['total_net'] for r in rows], [0, -2, 6])
        reversed_rows = weekly_results(players, meetings, records, 2)
        self.assertEqual([r['total_net'] for r in reversed_rows], [0, 2, -6])
        self.assertEqual(weekly_results(players, [], {}, 1), [])

    def test_matrix_draws_and_reversed_player_perspective(self):
        players = [SimpleNamespace(id=1, name='Steve'), SimpleNamespace(id=2, name='Scott')]
        meetings = [dict(week=1, a=1, b=2, net=2), dict(week=2, a=2, b=1, net=0)]
        matrix = matchup_matrix(players, meetings)
        self.assertTrue(matrix[0]['cells'][0]['self'])
        cell = matrix[0]['cells'][1]
        self.assertEqual((cell['wins'], cell['losses'], cell['draws']), (1, 0, 1))
        self.assertEqual(matrix[1]['cells'][0]['losses'], 1)

    def test_performance_uses_official_net_and_shared_rank_labels_stay_separate(self):
        players = [SimpleNamespace(id=1, name='Steve'), SimpleNamespace(id=2, name='Scott')]
        snapshots = {1: [dict(player_id=1, net_points=-2, rank=1), dict(player_id=2, net_points=-2, rank=1)],
                     3: [dict(player_id=1, net_points=4, rank=1), dict(player_id=2, net_points=4, rank=1)]}
        chart = performance_chart(snapshots, 1)
        self.assertEqual([(p['week'], p['net']) for p in chart['points']], [(1, -2), (3, 4)])
        self.assertGreater(chart['points'][0]['y'], chart['points'][1]['y'])
        positions = position_chart(players, snapshots)
        self.assertNotEqual(positions['series'][0]['label_x'], positions['series'][1]['label_x'])
        self.assertEqual(performance_chart({}, 1)['points'], [])
