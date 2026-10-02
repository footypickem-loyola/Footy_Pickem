import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import Column, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from pick_insight_enrichment import (cached_enrichment, crest_url, register_models,
                                    validate_mapping, verified_mapping)
from sportmonks_season import SeasonClient, SeasonSyncError, select_scorers, sync_season

NOW = datetime(2026, 10, 2, 15, tzinfo=timezone.utc)
MAPPING = verified_mapping()
PARTICIPANTS = [dict(id=i, name='Names are not identity',
                    image_path=f'https://cdn.sportmonks.com/images/soccer/teams/0/{i}.png') for i in MAPPING]


def scorer(team=19, player=100, total=5, name='Example Player', **extra):
    return dict(season_id=28083, type_id=208, participant_id=team, player_id=player,
                total=total, player=dict(id=player, display_name=name), participant=dict(id=team), **extra)


class ProviderTests(unittest.TestCase):
    def client(self, payloads):
        self.requests = []
        def opener(request, timeout):
            self.requests.append(request)
            return io.BytesIO(json.dumps(payloads[len(self.requests)-1]).encode())
        return SeasonClient('secret-test-token', opener)

    def page(self, rows, number=1, more=False):
        return dict(data=rows, pagination=dict(current_page=number, has_more=more))

    def test_pagination_includes_filter_and_raw_auth_on_every_page(self):
        first_page = [scorer(player=player) for player in range(100, 150)]
        last_page = [scorer(player=150)]
        client = self.client([self.page(first_page, more=True), self.page(last_page, 2)])
        self.assertEqual(client.scorers(), first_page + last_page)
        self.assertEqual(len(self.requests), 2)
        for n, request in enumerate(self.requests, 1):
            url = urlsplit(request.full_url)
            self.assertEqual(url.path, '/v3/football/topscorers/seasons/28083')
            self.assertEqual(parse_qs(url.query), dict(page=[str(n)], per_page=['50'],
                include=['player;participant;type'], filters=['seasonTopscorerTypes:208']))
            self.assertEqual(request.get_header('Authorization'), 'secret-test-token')
            self.assertNotIn('secret-test-token', request.full_url)

    def test_participant_pagination(self):
        client = self.client([self.page(PARTICIPANTS[:10], more=True), self.page(PARTICIPANTS[10:], 2)])
        self.assertEqual(client.participants(), PARTICIPANTS)
        self.assertIn('/teams/seasons/28083?', self.requests[0].full_url)

    def test_non_paginated_teams_are_complete_without_second_request(self):
        client = self.client([dict(data=PARTICIPANTS)])
        self.assertEqual(client.participants(), PARTICIPANTS)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(urlsplit(self.requests[0].full_url).path, '/v3/football/teams/seasons/28083')

    def test_absent_pagination_returns_accumulated_rows(self):
        client = self.client([self.page(PARTICIPANTS[:10], more=True), dict(data=PARTICIPANTS[10:])])
        self.assertEqual(client.participants(), PARTICIPANTS)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.client([dict(data=[])]).participants(), [])
        self.assertEqual(len(self.requests), 1)

    def test_malformed_data_fails_with_or_without_pagination(self):
        for data in (None, {}, 'teams', [None], [1], [dict(id=19), 'invalid']):
            for payload in (dict(data=data), self.page(data)):
                with self.subTest(payload=payload), self.assertRaises(SeasonSyncError):
                    self.client([payload]).participants()
        with self.assertRaises(SeasonSyncError):
            self.client([{}]).participants()

    def test_present_malformed_pagination_fails_closed(self):
        for pagination in (None, [], 'invalid', {},
                           dict(has_more=1, current_page=1),
                           dict(has_more=False, current_page=True),
                           dict(has_more=False, current_page='1'),
                           dict(has_more=False, current_page=2)):
            with self.subTest(pagination=pagination), self.assertRaises(SeasonSyncError):
                self.client([dict(data=PARTICIPANTS, pagination=pagination)]).participants()

    def test_pagination_still_stops_at_100_page_limit(self):
        client = self.client([self.page([dict(id=i)], i, more=True) for i in range(1, 101)])
        with self.assertRaisesRegex(SeasonSyncError, 'pagination limit exceeded'):
            client.participants()
        self.assertEqual(len(self.requests), 100)

    def test_malformed_looping_and_empty_incomplete_pages_fail_closed(self):
        for payloads in ([self.page([], more=True)],
                         [self.page([], number=2)], [dict(data=[], pagination={'has_more': 'false'})],
                         [self.page([scorer()], more=True), self.page([scorer()], 2)],
                         [self.page([scorer()], more=True), dict(data=[scorer()])],
                         [self.page([scorer()], more=True), self.page([], 1)]):
            with self.subTest(payloads=payloads), self.assertRaises(SeasonSyncError):
                self.client(payloads).scorers()

    def test_error_messages_do_not_expose_credentials_or_body(self):
        for code in (401, 403, 429, 500):
            def fail(request, timeout):
                raise HTTPError('secret-url', code, 'secret-body', {}, None)
            with self.assertRaisesRegex(SeasonSyncError, f'HTTP {code}') as caught:
                SeasonClient('secret-token', fail).scorers()
            self.assertNotIn('secret', str(caught.exception))
        with self.assertRaises(SeasonSyncError):
            SeasonClient('').scorers()

    def test_mapping_exact_production_names_and_completeness(self):
        evidence = json.loads((Path(__file__).parent / 'fixtures' / 'pr16_production_clubs.json').read_text())
        self.assertEqual(set(MAPPING.values()), set(evidence['clubs']))
        validate_mapping(MAPPING, evidence['clubs'], PARTICIPANTS)
        for mapping, clubs, participants in (
                (dict(MAPPING, **{'999': 'Unknown'}), MAPPING.values(), PARTICIPANTS),
                ({**MAPPING, 19: MAPPING[9]}, MAPPING.values(), PARTICIPANTS),
                (MAPPING, set(MAPPING.values()) - {'Arsenal'}, PARTICIPANTS),
                (MAPPING, MAPPING.values(), PARTICIPANTS[:-1]),
                (MAPPING, MAPPING.values(), PARTICIPANTS[:-1] + [PARTICIPANTS[0]]),
                (MAPPING, MAPPING.values(), PARTICIPANTS[:-1] + [dict(id=999)])):
            with self.assertRaises(ValueError):
                validate_mapping(mapping, clubs, participants)

    def test_user_supplied_participants_and_scorer_example(self):
        export = json.loads((Path(__file__).parent / 'fixtures' / 'sportmonks-season-28083-sanitized.json').read_text(encoding='utf-8-sig'))
        validate_mapping(MAPPING, MAPPING.values(), export['teams'])
        result = select_scorers([export['goal_topscorer_example']], MAPPING)
        self.assertEqual(result[9], (5, [dict(player_id=154421, name='Erling Haaland')]))

    def test_scorers_choose_max_not_sum_and_stable_ties(self):
        rows = [scorer(player=102, total=2), scorer(player=101, name='Second'), scorer()]
        expected = (5, [dict(player_id=100, name='Example Player'), dict(player_id=101, name='Second')])
        self.assertEqual(select_scorers(rows + [rows[2]], MAPPING)[19], expected)
        self.assertEqual(select_scorers(list(reversed(rows)), MAPPING)[19], expected)

    def test_unknown_id_never_maps_using_included_name(self):
        row = scorer(team=999)
        row['participant']['name'] = 'Arsenal'
        self.assertTrue(all(value == (None, []) for value in select_scorers([row], MAPPING).values()))

    def test_wrong_season_type_missing_and_zero_totals(self):
        rows = [dict(scorer(), season_id=1), dict(scorer(), type_id=209), scorer(total=0)]
        self.assertEqual(select_scorers(rows, MAPPING)[19], (None, []))
        self.assertEqual(select_scorers([], MAPPING)[19], (None, []))

    def test_invalid_candidate_prevents_publishing_understated_leader(self):
        for change in (dict(total=None), dict(total=-1), dict(total=True), dict(total='9'),
                       dict(player=None), dict(player=[]), dict(player_id=True),
                       dict(participant=dict(id=9)), dict(player=dict(id=100, name=''))):
            with self.subTest(change=change):
                rows = [scorer(player=101, total=2), dict(scorer(), **change)]
                self.assertEqual(select_scorers(rows, MAPPING)[19], (None, []))

    def test_conflicting_duplicates_and_display_name_preference(self):
        self.assertEqual(select_scorers([scorer(), scorer(total=6)], MAPPING)[19], (None, []))
        row = scorer()
        row['player'] = dict(id=100, display_name='Preferred', name='Other')
        self.assertEqual(select_scorers([row], MAPPING)[19][1][0]['name'], 'Preferred')

    def test_crest_url_rejects_bad_hosts_schemes_credentials_and_wrong_club(self):
        valid = PARTICIPANTS[0]['image_path']
        self.assertEqual(crest_url(valid, PARTICIPANTS[0]['id']), valid)
        for value in (None, '', 'javascript:alert(1)', valid.replace('https:', 'http:'),
                      valid.replace('cdn.sportmonks.com', 'cdn.sportmonks.com.evil.test'), valid+'?token=secret'):
            self.assertIsNone(crest_url(value, PARTICIPANTS[0]['id']))
        self.assertIsNone(crest_url(valid, 999))


class CacheTests(unittest.TestCase):
    def setUp(self):
        Base = declarative_base()
        class Season(Base):
            __tablename__ = 'seasons'
            id = Column(Integer, primary_key=True)
            code = Column(String)
            api_season_year = Column(Integer)
            api_competition_code = Column(String)
            is_active = Column(Integer)
            is_archived = Column(Integer)
        class Week(Base):
            __tablename__ = 'weeks'
            id = Column(Integer, primary_key=True)
            season_id = Column(Integer, ForeignKey('seasons.id'))
        class Fixture(Base):
            __tablename__ = 'fixtures'
            id = Column(Integer, primary_key=True)
            week_id = Column(Integer, ForeignKey('weeks.id'))
            home = Column(String)
            away = Column(String)
        self.models = Obj(ClubSeasonEnrichment=register_models(Base), Fixture=Fixture, Week=Week)
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.season = Season(id=1, code='year-2', api_season_year=2026, api_competition_code='PL',
                             is_active=1, is_archived=0)
        self.db.add_all([self.season, Week(id=1, season_id=1)])
        clubs = list(MAPPING.values())
        self.db.add_all(Fixture(week_id=1, home=clubs[i], away=clubs[i+1]) for i in range(0,20,2))
        self.db.commit()
        self.client = Obj(participants=lambda: copy.deepcopy(PARTICIPANTS), scorers=lambda: [scorer()])

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def sync(self):
        return sync_season(self.db, self.models, self.season, self.client, NOW)

    def view(self, team='Arsenal', now=NOW):
        return cached_enrichment(self.db, self.models, self.season, team, now)

    def test_sync_stores_20_and_reads_cache_after_provider_goes_offline(self):
        self.assertEqual(self.sync(), dict(clubs=20, clubs_with_scorers=1))
        with patch.object(SeasonClient, 'pages', side_effect=AssertionError('Provider must not run')):
            self.assertEqual(self.view()['top_scorer'], 'Example Player · 5 goals')
            self.assertTrue(self.view()['crest'].endswith('/19.png'))
            self.assertIsNone(self.view('Man City')['top_scorer'])

    def test_missing_stale_future_wrong_season_unknown_club_fallback(self):
        fallback = dict(crest=None, top_scorer=None)
        self.assertEqual(self.view(), fallback)
        self.sync()
        self.assertIsNotNone(self.view(now=NOW+timedelta(days=14))['top_scorer'])
        for team, now in [('Arsenal', NOW+timedelta(days=35, seconds=1)),
                          ('Arsenal', NOW-timedelta(seconds=1)), ('Unknown', NOW)]:
            self.assertEqual(self.view(team, now), fallback)
        self.season.api_season_year = 2025
        self.assertEqual(self.view(), fallback)

    def test_failure_retains_prior_cache_and_timestamp(self):
        self.sync()
        before = self.view()
        self.client.scorers = lambda: (_ for _ in ()).throw(SeasonSyncError('second page failed'))
        with self.assertRaises(SeasonSyncError):
            self.sync()
        self.assertEqual(self.view(), before)
        row = self.db.get(self.models.ClubSeasonEnrichment, (1, 19))
        self.assertEqual(row.synced_at, NOW.replace(tzinfo=None))

    def test_empty_success_clears_old_scorer_missing_crest_independent(self):
        self.sync()
        self.client.scorers = lambda: []
        self.client.participants = lambda: [dict(p, image_path=None) for p in PARTICIPANTS]
        self.sync()
        self.assertEqual(self.view(), dict(crest=None, top_scorer=None))
        self.client.scorers = lambda: [scorer()]
        self.sync()
        self.assertEqual(self.view(), dict(crest=None, top_scorer='Example Player · 5 goals'))

    def test_cache_corruption_and_mapping_unavailable_fallback(self):
        self.sync()
        row = self.db.get(self.models.ClubSeasonEnrichment, (1,19))
        row.top_scorers = [dict(player_id=100, name='')]
        self.assertIsNone(self.view()['top_scorer'])
        row.footy_club = 'Other'
        self.assertEqual(self.view(), dict(crest=None, top_scorer=None))
        with patch('pick_insight_enrichment.verified_mapping', side_effect=ValueError()):
            self.assertEqual(self.view(), dict(crest=None, top_scorer=None))

    def test_sync_rejects_wrong_scope_and_mapping_without_writes(self):
        self.client.participants = lambda: PARTICIPANTS[:-1]
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(self.db.query(self.models.ClubSeasonEnrichment).count(), 0)
        self.season.api_season_year = 2025
        with self.assertRaises(SeasonSyncError):
            self.sync()

    def test_ties_render_each_and_singular_goal(self):
        self.client.scorers = lambda: [scorer(total=1), scorer(player=101, total=1, name='Other')]
        self.sync()
        self.assertEqual(self.view()['top_scorer'], 'Example Player / Other · 1 goal each')
