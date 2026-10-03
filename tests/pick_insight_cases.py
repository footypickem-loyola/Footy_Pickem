"""HTTP regressions mixed into the app's existing isolated database harness."""
import json
import re
from datetime import datetime, timedelta
from unittest.mock import patch


class PickInsightCases:
    def test_insight_cached_enrichment_no_provider_and_stale_fallback(self):
        from pick_insight_enrichment import verified_mapping
        self.auto_context()
        self.service.command(self.mid, self.a, manual=self.fx[0][:2])
        team = self.fx[0][1]
        mapping = verified_mapping()
        team_id = next(key for key, name in mapping.items() if name == team)
        season = self.db.get(self.d.Week, self.week_id).season
        season.api_competition_code, season.api_season_year = 'PL', 2026
        now = self.d.utcnow()
        row = self.d.ClubSeasonEnrichment(season_id=season.id, sportmonks_team_id=team_id,
            sportmonks_season_id=28083, league_id=8, footy_club=team,
            crest_url=f'https://cdn.sportmonks.com/images/soccer/teams/0/{team_id}.png',
            top_scorers=[dict(player_id=1, name='Cached Player')], goals=7, synced_at=now)
        self.db.add(row)
        self.db.commit()
        with self.insight_client() as client, patch('sportmonks_season.SeasonClient.pages', side_effect=AssertionError('No season provider calls')), patch.object(self.d, 'football_data_client', side_effect=AssertionError('No provider calls')), patch('sportmonks_live.SportmonksClient.livescores', side_effect=AssertionError('No live calls')):
            response = client.get(f'/pick-insight/{self.mid}')
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True)
            self.assertIn('Cached Player · 7 goals', body)
            self.assertIn('class="insight-crest"', body)
            row.synced_at = now - timedelta(days=36)
            self.db.merge(row)  # Request teardown detaches the earlier fixture object.
            self.db.commit()
            stale = client.get(f'/pick-insight/{self.mid}').get_data(as_text=True)
            self.assertNotIn('Cached Player', stale)
            self.assertNotIn('class="insight-crest"', stale)
            self.assertIn('<dt>Top Scorer</dt><dd>—</dd>', stale)

    def test_insight_single_cta_switches_when_player_owns_five_before_draft_finishes(self):
        self.auto_context()
        self.bulk_edit(self.a)
        self.auto_edit(self.a, 'toggle', enabled='0')
        with self.insight_client() as client:
            def assert_cta(mode):
                body = client.get('/partials/matchweek/1').get_data(as_text=True)
                links = re.findall(r'data-insight-open="([^"]+)"', body)
                self.assertEqual(len(links), 1)
                self.assertIn('mode=' + mode, links[0])
                return client.get(links[0].replace('&amp;', '&')).get_data(as_text=True)
            self.assertIn('1 of 1', assert_cta('recap'))
            for index in range(1, 9):
                self.service.command(self.mid, self.d.draft_turn_at(self.a, self.b, index), manual=self.fx[index][:2])
            self.assertEqual(len(self.auto_picks()), 9)
            self.assertIn('1 of 5', assert_cta('recap'))
            self.service.command(self.mid, self.b, manual=self.fx[9][:2])
            body = assert_cta('recap')
            self.assertIn('Based on Premier League results this season', body)
            self.assertNotIn('Goal averages require', body)
            self.assertNotIn('Top scorer unavailable', body)
            self.assertNotIn('recorded results', body)
            self.assertIn('<dd>—</dd>', body)

    def insight_client(self, player=None):
        client = self.d.app.test_client()
        with client.session_transaction() as session:
            session['player_name'] = self.db.get(self.d.Player, player or self.a).name
        self.db.rollback()
        return client

    def insight_pick(self, client, count, fixture_index):
        return client.post('/pick', headers={'HX-Request': 'true'}, data=dict(
            presentation='v3', season='year-2', week=1, matchup_id=self.mid,
            expected_count=count, fixture_id=self.fx[fixture_index][0], team=self.fx[fixture_index][1]))

    def test_insight_success_only_and_consecutive_snake_turns(self):
        self.auto_context()
        self.service.command(self.mid, self.a, manual=self.fx[0][:2])
        with self.insight_client(self.b) as client:
            for count, index in [(1, 1), (2, 2)]:
                response = self.insight_pick(client, count, index)
                self.assertEqual(response.status_code, 200)
                event = json.loads(response.headers['HX-Trigger-After-Swap'])['pickInsight']
                body = client.get(event['url']).get_data(as_text=True)
                self.assertIn('Your Pick: ' + self.fx[index][1], body)
                self.assertEqual(body.count('class="insight-band"'), 6)
                self.assertNotIn('arsenalBanter', response.headers.get('HX-Trigger', ''))
                self.assertNotIn('HX-Trigger-After-Swap', client.get('/partials/matchweek/1').headers)
            rejected = self.insight_pick(client, 2, 3)
            self.assertGreaterEqual(rejected.status_code, 400)
            self.assertNotIn('HX-Trigger-After-Swap', rejected.headers)
        self.assertEqual(len(self.auto_picks()), 3)

    def test_insight_authorization_manual_id_and_navigation_bounds(self):
        self.auto_context()
        self.service.command(self.mid, self.a, manual=self.fx[0][:2])
        pick_id = self.auto_picks()[0].id
        url = f'/pick-insight/{self.mid}'
        self.assertEqual(self.d.app.test_client().get(url).status_code, 403)
        with self.insight_client(self.b) as client:
            self.assertEqual(client.get(f'{url}?mode=manual&pick_id={pick_id}').status_code, 404)
            self.assertEqual(client.get(f'{url}?mode=bulk').status_code, 404)
        other = self.db.query(self.d.Player).filter(self.d.Player.id.notin_([self.a, self.b])).first().id
        with self.insight_client(other) as client:
            self.assertEqual(client.get(url).status_code, 403)
        with self.insight_client() as client:
            for suffix in ('?index=-1', '?index=1', '?mode=unknown'):
                self.assertGreaterEqual(client.get(url + suffix).status_code, 400)
            self.assertEqual(client.get(url).headers['Cache-Control'], 'private, no-store')

    def test_insight_bulk_confirmation_hides_recap_until_committed(self):
        self.auto_context()
        with self.insight_client(self.b) as client:
            response = client.post(f'/auto-draft/{self.mid}', headers={'HX-Request': 'true'}, data=dict(
                action='confirm', expected_count=0,
                preferences=json.dumps([dict(fixture_id=f[0], team=f[1]) for f in self.fx])))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b'data-insight-open=', response.data)
            self.assertNotIn('HX-Trigger-After-Swap', response.headers)
            for mode in ('bulk', 'recap'):
                self.assertEqual(client.get(f'/pick-insight/{self.mid}?mode={mode}').status_code, 404)
            self.assertEqual(len(self.auto_picks()), 0)
            self.service.command(self.mid, self.a, manual=self.fx[0][:2])
            # The snake draft commits two bulk picks after the opponent's first.
            for mode in ('bulk', 'recap'):
                body = client.get(f'/pick-insight/{self.mid}?mode={mode}').get_data(as_text=True)
                self.assertIn('1 of 2', body)
                self.assertIn('Your Pick: ' + self.fx[1][1], body)
                self.assertNotIn('Awaiting your turn', body)
                self.assertEqual(client.get(f'/pick-insight/{self.mid}?mode={mode}&index=2').status_code, 404)

    def test_insight_recap_tracks_only_owned_picks_at_every_draft_stage(self):
        from html import unescape
        self.auto_context()
        self.bulk_edit(self.b)
        self.auto_edit(self.b, 'toggle', enabled='0')
        with self.insight_client(self.b) as client:
            for index in range(10):
                player = self.d.draft_turn_at(self.a, self.b, index)
                # Opposite team to the stored preference: show the actual pick.
                self.service.command(self.mid, player, manual=(self.fx[index][0], self.fx[index][2]))
                owned = [p for p in self.auto_picks() if p.player_id == self.b]
                page = client.get('/partials/matchweek/1').get_data(as_text=True)
                self.assertEqual(page.count('data-insight-open='), int(bool(owned)))
                for mode in ('recap', 'bulk'):
                    url = f'/pick-insight/{self.mid}?mode={mode}'
                    for position, pick in enumerate(owned):
                        body = unescape(client.get(f'{url}&index={position}').get_data(as_text=True))
                        self.assertIn(f'{position + 1} of {len(owned)}', body)
                        self.assertIn('Your Pick: ' + pick.team, body)
                    self.assertEqual(client.get(f'{url}&index={len(owned)}').status_code, 404)

    def test_insight_complete_and_archived_recap_contains_only_owned_picks(self):
        self.auto_context()
        for index in range(10):
            self.service.command(self.mid, self.d.draft_turn_at(self.a, self.b, index), manual=self.fx[index][:2])
        with self.insight_client() as client:
            self.assertIn(b'Pick Recap', client.get('/partials/matchweek/1').data)
            body = client.get(f'/pick-insight/{self.mid}?index=4').get_data(as_text=True)
            self.assertIn('5 of 5', body)
            self.assertIn('disabled>Next', body)
            self.assertEqual(client.get(f'/pick-insight/{self.mid}?index=5').status_code, 404)
            self.db.get(self.d.Week, self.week_id).season.is_archived = 1
            self.db.commit()
            self.assertEqual(client.get(f'/pick-insight/{self.mid}').status_code, 200)

    def test_insight_scopes_official_results_to_season_and_excludes_live(self):
        self.auto_context()
        team = self.fx[0][1]
        self.service.command(self.mid, self.a, manual=self.fx[0][:2])
        season_id = self.db.get(self.d.Week, self.week_id).season_id
        prior = self.d.Week(season_id=season_id, number=0, room_code='TEST')
        other_season = self.d.Season(code='other-insight', name='Other season')
        self.db.add_all([prior, other_season])
        self.db.flush()
        other = self.d.Week(season_id=other_season.id, number=0, room_code='TEST')
        self.db.add(other)
        self.db.flush()
        for week, score in [(prior, 2), (other, 99)]:
            fixture = self.d.Fixture(week_id=week.id, match_number=1, home=team, away='Opponent')
            self.db.add(fixture)
            self.db.flush()
            self.db.add(self.d.Result(fixture_id=fixture.id, outcome='Home', home_score=score, away_score=0, source='manual'))
            self.db.add(self.d.LiveFixtureState(fixture_id=fixture.id, provider='sportmonks',
                state='finished', is_live=False, home_score=99, away_score=99,
                last_synced_at=datetime(2026, 9, 1)))
        self.db.commit()
        with self.insight_client() as client, patch.object(self.d, 'football_data_client', side_effect=AssertionError('No provider calls')), patch('sportmonks_live.SportmonksClient.livescores', side_effect=AssertionError('No live calls')):
            body = client.get(f'/pick-insight/{self.mid}').get_data(as_text=True)
            self.assertIn('1W 0D 0L', body)
            self.assertIn('<dd>2.0</dd>', body)
            self.assertNotIn('<dd>99.0</dd>', body)
