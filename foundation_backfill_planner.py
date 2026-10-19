"""Private, in-memory Stage 2 proposals; no persistence or execution path.

V2 reviewed assertions are bound to exact database fingerprints. Review hashes
identify human-approved artifacts; this tool cannot independently authenticate
that review. Full plans contain private values and are library-only. CLI output
is a redacted summary. A blocked plan is never a partially executable plan.
"""
from collections import defaultdict
from contextlib import closing
import sqlite3
import unicodedata

from foundation_backfill_manifest import canonical_json, validate_planning_manifest
from foundation_backfill_validation import (
    connect_readonly, inspect_connection, InspectionError, FOUNDATION,
)
from foundation_migrations import digest, quoted
from foundation_schema import ADDITIONS, REQUIRED_COLUMNS

LEAGUE_NAME = 'Loyola Pick \u2019Em'
REVIEW_GATES = {'SEASON_MAPPING_REVIEW_REQUIRED', 'SEASON_EVIDENCE_MISSING',
                'CLUB_AND_ROUND_MANIFEST_REQUIRED', 'RECIPIENT_PERMISSION_MANIFEST_REQUIRED',
                'REVIEWED_MAPPING_MANIFEST_REQUIRED'}


def name_key(name):
    return unicodedata.normalize('NFKC', name.strip()).casefold()


def plan_database(path, manifest, inspection):
    """Explicit source + manifest + prior inspector report; one fresh RO snapshot.

    Never accepts injected raw rows as authoritative. Reject stale fingerprints,
    including concurrent changes since inspection, before producing proposals.
    """
    validation = validate_planning_manifest(manifest)
    try:
        with closing(connect_readonly(path)) as db:
            db.execute('BEGIN')
            current = inspect_connection(db)
            if inspection != current or manifest['source'] != current['source']:
                raise InspectionError('SOURCE_FINGERPRINT_MISMATCH')
            tables = {}
            for table in sorted(set(REQUIRED_COLUMNS) | FOUNDATION):
                cursor = db.execute('SELECT * FROM ' + quoted(table))
                names = [c[0] for c in cursor.description]
                tables[table] = sorted((dict(zip(names, r)) for r in cursor), key=lambda row: digest(row))
            return _build_plan(tables, manifest, current, validation)
    except InspectionError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        raise InspectionError('DATABASE_PLANNING_REFUSED') from None


def _build_plan(tables, manifest, inspection, validation):
    issues = []
    proposed = defaultdict(list)
    updates = defaultdict(list)
    nulls = []
    mappings = []

    def block(code, table=None, row_id=None, classification='unresolved / blocker'):
        issues.append(dict(code=code, table=table, row_id=row_id, classification=classification))

    def provenance(table, key, reviewed=()):
        return dict(source_table=table, source_key=key, evidence=sorted(reviewed),
                    source_fingerprint=manifest['source']['data_sha256'])

    def insert(table, values, proof):
        record = dict(values=values, provenance=proof,
                      classification='deterministic / fully supported')
        proposed[table].append(record)

    def update(table, row, values, proof, key=None):
        key = key or {'id': row['id']}
        assert set(values) <= set(ADDITIONS[table])
        if any(row[c] is not None and row[c] != v for c, v in values.items()):
            block('CONFLICTING_POPULATED_VALUE', table, key)
            return
        updates[table].append(dict(key=key, values={c: v for c, v in values.items() if row[c] is None},
                                   provenance=proof, classification='deterministic / fully supported'))

    def missing(code, table, key):
        block(code, table, key, 'requires reviewed manifest evidence')

    def left_null(table, key, fields):
        nulls.append(dict(table=table, key=key, fields=sorted(fields), classification='intentionally left null'))

    for issue in validation['blockers']:
        block(issue['code'])
    for issue in inspection['blockers']:
        if issue['code'] not in REVIEW_GATES:
            block(issue['code'], issue.get('table'), issue.get('row_id'))
    groups = defaultdict(list)
    for row in sorted(manifest['mappings'], key=canonical_json):
        groups[row['kind']].append(row)

    def index(kind, field):
        result = {}
        for row in groups[kind]:
            key = row[field]
            if key in result:
                block('DUPLICATE_CANONICAL_MAPPING', kind, key)
            else:
                result[key] = row
        return result

    competitions = index('competition', 'key')
    seasons = index('football_season', 'key')
    clubs = index('club', 'key')
    season_bindings = index('season', 'id')
    week_bindings = index('week', 'id')
    fixture_bindings = index('fixture', 'id')
    # Empty-foundation-only slice: existing population is a blocker, never adopted.
    # IDs start after observed maxima, using numeric Player IDs or lexical keys.
    ids = {}
    for table, keys in [('users', sorted(r['id'] for r in tables['players'])),
                        ('competitions', sorted(competitions)), ('football_seasons', sorted(seasons)),
                        ('clubs', sorted(clubs))]:
        base = max([r['id'] for r in tables[table]] + [0])
        ids[table] = {key: base + i for i, key in enumerate(keys, 1)}
        if base + len(keys) > 9223372036854775807:
            block('PROPOSED_ID_RANGE_EXCEEDED', table)
    round_keys = sorted({(r['football_season_key'], r['number']) for r in groups['week']})
    base = max([r['id'] for r in tables['football_matchweeks']] + [0])
    if base + len(round_keys) > 9223372036854775807:
        block('PROPOSED_ID_RANGE_EXCEEDED', 'football_matchweeks')
    rounds = {key: base + i for i, key in enumerate(round_keys, 1)}
    ids['football_matchweeks'] = {canonical_json(list(k)): v for k, v in rounds.items()}

    def provider_map(row):
        result = {}
        for p in sorted(row['providers'], key=canonical_json):
            if p['provider'] in result:
                block('CONFLICTING_PROVIDER_IDS')
            result[p['provider']] = p['external_id']
        return result

    provider_ids = {}
    for kind, rows in [('competition', competitions), ('football_season', seasons), ('club', clubs)]:
        seen = {}
        for key, row in rows.items():
            provider_ids[kind, key] = provider_map(row)
            for provider, external in provider_ids[kind, key].items():
                if (provider, external) in seen:
                    block('CONFLICTING_PROVIDER_IDS', kind, key)
                seen[provider, external] = key

    policy = groups['policy'][0] if len(groups['policy']) == 1 else None
    if policy:
        timestamp = policy['timestamp']
        league = dict(id=1, name=LEAGUE_NAME, slug=policy['slug'], status=policy['status'],
                      timezone=policy['timezone'], correspondent_enabled=policy['correspondent_enabled'],
                      created_at=timestamp, updated_at=timestamp)
        for old in tables['leagues']:
            if old['id'] == 1 and any(old[k] != league[k] for k in ('name', 'slug', 'status', 'timezone', 'correspondent_enabled')):
                block('CONFLICTING_LEAGUE1_IDENTITY', 'leagues', 1)
            if old['slug'] == league['slug'] and old['id'] != 1:
                block('CONFLICTING_LEAGUE1_IDENTITY', 'leagues', old['id'])
        if not any(r['id'] == 1 for r in tables['leagues']):
            insert('leagues', league, provenance('manifest_policy', 1, policy['evidence']))
    else:
        timestamp = None

    normalized_players = defaultdict(list)
    for player in sorted(tables['players'], key=lambda r: r['id']):
        pid = player['id']
        uid = ids['users'][pid]
        proof = provenance('players', pid)
        normalized_players[name_key(player['name'])].append(pid)
        if not player['name'].strip(): block('INVALID_PLAYER_NAME', 'players', pid)
        if policy:
            insert('users', dict(id=uid, display_name=player['name'], status='unclaimed',
                                is_platform_admin=policy['user_is_platform_admin'], email=None,
                                email_normalized=None, email_verified_at=None, favorite_club_id=None,
                                created_at=timestamp, updated_at=timestamp),
                   provenance('players', pid, policy['evidence']))
            update('players', player, dict(league_id=1, user_id=uid), proof)
        left_null('users', {'id': uid}, ['email', 'email_normalized', 'email_verified_at', 'favorite_club_id'])
        left_null('players', {'id': pid}, ['role', 'status', 'joined_at', 'left_at', 'recap_opt_in'])
    for normalized, players in normalized_players.items():
        if len(players) > 1:
            block('MEMBERSHIP_NAME_COLLISION', 'players', sorted(players))
            for pid in players: left_null('players', {'id': pid}, ['name_key'])
        else:
            player = next(p for p in tables['players'] if p['id'] == players[0])
            update('players', player, {'name_key': normalized}, provenance('players', player['id']))

    unique_comp_codes = set()
    for key, row in competitions.items():
        if row['code'] in unique_comp_codes: block('DUPLICATE_CANONICAL_MAPPING', 'competitions', key)
        unique_comp_codes.add(row['code'])
        cid = ids['competitions'][key]
        proof = provenance('manifest_competition', key, row['evidence'])
        insert('competitions', dict(id=cid, code=row['code'], name=row['name'], country_code=None), proof)
        for provider, external in provider_ids['competition', key].items():
            insert('competition_provider_links', dict(competition_id=cid, provider=provider, external_competition_id=external), proof)
    years = set()
    for key, row in seasons.items():
        comp = row['competition_key']
        if comp not in competitions:
            missing('COMPETITION_MAPPING_REQUIRED', 'football_seasons', key)
            continue
        year_key = (comp, row['start_year'])
        if row['start_year'] is not None and year_key in years:
            block('DUPLICATE_CANONICAL_MAPPING', 'football_seasons', key)
        years.add(year_key)
        fid = ids['football_seasons'][key]
        proof = provenance('manifest_football_season', key, row['evidence'])
        insert('football_seasons', dict(id=fid, competition_id=ids['competitions'][comp], season_key=key,
                                       name=row['name'], start_year=row['start_year'], starts_on=None,
                                       ends_on=None, sync_enabled=policy['sync_enabled'] if policy else None), proof)
        for provider, external in provider_ids['football_season', key].items():
            if provider not in provider_ids['competition', comp]:
                block('CONFLICTING_PROVIDER_IDS', 'football_seasons', key)
            if provider == 'football-data' and external == str(row['start_year']):
                block('YEAR_IS_NOT_PROVIDER_SEASON_ID', 'football_seasons', key)
            insert('football_season_provider_links', dict(football_season_id=fid, provider=provider, external_season_id=external), proof)
    for key, row in clubs.items():
        cid = ids['clubs'][key]
        proof = provenance('manifest_club', key, row['evidence'])
        insert('clubs', dict(id=cid, name=row['name'], short_name=None, country_code=None, is_active=policy['club_is_active'] if policy else None), proof)
        for provider, external in provider_ids['club', key].items():
            insert('club_provider_links', dict(club_id=cid, provider=provider, external_club_id=external), proof)

    actual_seasons = {r['id']: r for r in tables['seasons']}
    actual_weeks = {r['id']: r for r in tables['weeks']}
    actual_fixtures = {r['id']: r for r in tables['fixtures']}
    actual_players = {r['id']: r for r in tables['players']}
    resolved_seasons, resolved_weeks, resolved_fixtures = {}, {}, {}
    for kind, bindings, actual in [('season', season_bindings, actual_seasons),
                                    ('week', week_bindings, actual_weeks),
                                    ('fixture', fixture_bindings, actual_fixtures)]:
        for rid in sorted(set(bindings) - set(actual)):
            block('UNKNOWN_MANIFEST_SOURCE_ROW', kind, rid)
    canonical_legacy_seasons = {}
    for sid, row in actual_seasons.items():
        binding = season_bindings.get(sid)
        if binding is None or binding['football_season_key'] not in seasons:
            missing('SEASON_MAPPING_REQUIRED', 'seasons', sid)
            continue
        key = binding['football_season_key']
        target = seasons[key]
        comp = competitions.get(target['competition_key'])
        if not comp or any(row[f] != binding[f] for f in ('api_competition_code', 'api_season_year')):
            block('INCONSISTENT_SEASON_EVIDENCE', 'seasons', sid)
            continue
        if ((row['api_season_year'] is not None and row['api_season_year'] != target['start_year']) or
            (row['api_competition_code'] is not None and
             row['api_competition_code'] != provider_ids['competition', target['competition_key']].get('football-data'))):
            block('INCONSISTENT_SEASON_EVIDENCE', 'seasons', sid)
            continue
        if key in canonical_legacy_seasons:
            block('DUPLICATE_CANONICAL_MAPPING', 'seasons', sid)
        canonical_legacy_seasons[key] = sid
        resolved_seasons[sid] = key
        update('seasons', row, dict(league_id=1, football_season_id=ids['football_seasons'][key]),
               provenance('seasons', sid, binding['evidence'] + target['evidence']))
        left_null('seasons', {'id': sid}, ['rules_version', 'stake_minor_units', 'currency'])
    for wid, row in actual_weeks.items():
        binding = week_bindings.get(wid)
        if binding is None:
            missing('WEEK_MAPPING_REQUIRED', 'weeks', wid)
            continue
        key = resolved_seasons.get(row['season_id'])
        if (key is None or binding['season_id'] != row['season_id'] or
            binding['football_season_key'] != key or binding['number'] != row['number']):
            block('INCONSISTENT_SEASON_ROUND_EVIDENCE', 'weeks', wid)
            continue
        resolved_weeks[wid] = (key, row['number'])
        mid = rounds[key, row['number']]
        proof = provenance('weeks', wid, binding['evidence'])
        if not any(r['values']['id'] == mid for r in proposed['football_matchweeks']):
            insert('football_matchweeks', dict(id=mid, football_season_id=ids['football_seasons'][key], number=row['number']), proof)
        update('weeks', row, dict(football_matchweek_id=mid), proof)

    aliases = {}
    for row in groups['alias']:
        key = (row['football_season_key'], name_key(row['name']))
        if key in aliases:
            block('AMBIGUOUS_CLUB_ALIAS')
        aliases[key] = row
        if row['club_key'] not in clubs or row['football_season_key'] not in seasons:
            missing('CLUB_ALIAS_MAPPING_REQUIRED', 'club_aliases', None)
    fixture_provider_seen = {}
    occupied_orders = set()
    for fid, row in actual_fixtures.items():
        order_key = (row['week_id'], row['match_number'])
        if type(row['match_number']) is not int or row['match_number'] <= 0 or order_key in occupied_orders:
            block('INVALID_OR_DUPLICATE_SLATE_ORDER', 'fixtures', fid)
        occupied_orders.add(order_key)
        insert('league_week_fixtures', dict(week_id=row['week_id'], fixture_id=fid, display_order=row['match_number']),
               provenance('fixtures', fid))
        binding = fixture_bindings.get(fid)
        if binding is None:
            missing('FIXTURE_MAPPING_REQUIRED', 'fixtures', fid)
            continue
        target = resolved_weeks.get(row['week_id'])
        if target is None or (binding['football_season_key'], binding['round']) != target or binding['week_id'] != row['week_id']:
            block('INCONSISTENT_SEASON_ROUND_EVIDENCE', 'fixtures', fid)
            continue
        if (binding['home'], binding['away']) != (row['home'], row['away']):
            block('FIXTURE_ORIENTATION_CONFLICT', 'fixtures', fid)
            continue
        home, away = binding['home_club_key'], binding['away_club_key']
        if home == away or home not in clubs or away not in clubs:
            block('CONFLICTING_FIXTURE_PARTICIPANTS', 'fixtures', fid)
            continue
        valid = True
        for side, club in [('home', home), ('away', away)]:
            alias = aliases.get((target[0], name_key(row[side])))
            if alias is None or alias['club_key'] != club:
                missing('CLUB_ALIAS_MAPPING_REQUIRED', 'fixtures', fid)
                valid = False
        observed = {r['provider']: str(r['external_fixture_id']) for r in tables['fixture_provider_links'] if r['fixture_id'] == fid}
        if row['external_match_id'] is not None:
            if 'football-data' in observed and observed['football-data'] != str(row['external_match_id']):
                block('CONFLICTING_PROVIDER_IDS', 'fixtures', fid)
                valid = False
            observed['football-data'] = str(row['external_match_id'])
        reviewed = provider_map(binding)
        # external_match_id itself is preserved football-data fixture evidence;
        # canonical season/round/clubs were validated above. No season-object
        # assertion is implied by the legacy fixture ID. Explicit assertions
        # (when supplied) must still agree, and existing links remain required.
        if row['external_match_id'] is not None:
            reviewed.setdefault('football-data', str(binding['external_match_id']))
        if binding['external_match_id'] != row['external_match_id'] or observed != reviewed:
            block('CONFLICTING_PROVIDER_IDS', 'fixtures', fid)
            valid = False
        for provider, external_id in observed.items():
            identity = (provider, external_id)
            if identity in fixture_provider_seen:
                block('CONFLICTING_PROVIDER_IDS', 'fixtures', fid)
                valid = False
            fixture_provider_seen[identity] = fid
        for p in binding['providers']:
            provider = p['provider']
            comp = seasons[target[0]]['competition_key']
            minimal_legacy = provider == 'football-data' and set(p) == {'provider', 'external_id'}
            if not minimal_legacy and (p['home_external_id'] != provider_ids['club', home].get(provider) or
                p['away_external_id'] != provider_ids['club', away].get(provider) or
                p['external_season_id'] != provider_ids['football_season', target[0]].get(provider) or
                p['external_competition_id'] != provider_ids['competition', comp].get(provider)):
                block('CONFLICTING_PROVIDER_PARTICIPANT_OR_SEASON', 'fixtures', fid)
                valid = False
        if not valid: continue
        resolved_fixtures[fid] = (target[0], home, away)
        update('fixtures', row, dict(football_season_id=ids['football_seasons'][target[0]],
                                    football_matchweek_id=rounds[target], home_club_id=ids['clubs'][home],
                                    away_club_id=ids['clubs'][away]), provenance('fixtures', fid, binding['evidence']))
    season_clubs = set()
    for row in groups['alias']:
        sk, ck = row['football_season_key'], row['club_key']
        if sk not in seasons or ck not in clubs: continue
        pair = (ids['football_seasons'][sk], ids['clubs'][ck])
        proof = provenance('manifest_alias', digest([sk, row['name']]), row['evidence'])
        if pair not in season_clubs:
            insert('football_season_clubs', dict(football_season_id=pair[0], club_id=pair[1]), proof)
            season_clubs.add(pair)
        insert('club_aliases', dict(football_season_id=pair[0], normalized_name=name_key(row['name']), club_id=pair[1]), proof)

    roster = defaultdict(list)
    matchups = {r['id']: r for r in tables['matchups']}
    good_matchups = set()
    for mid, row in matchups.items():
        players = [row['player_a_id'], row['player_b_id']]
        if (row['week_id'] not in actual_weeks or len(set(players)) != 2 or
            any(pid not in actual_players for pid in players) or row['first_picker_id'] not in players):
            block('INVALID_MATCHUP_RELATIONSHIP', 'matchups', mid)
            continue
        good_matchups.add(mid)
        sid = actual_weeks[row['week_id']]['season_id']
        for pid in players: roster[sid, pid].append(provenance('matchups', mid))
    for table in ('picks', 'auto_draft_preferences', 'auto_draft_settings'):
        for row in tables[table]:
            match = matchups.get(row['matchup_id'])
            fixture = actual_fixtures.get(row.get('fixture_id'))
            if (match is None or match['id'] not in good_matchups or
                row['player_id'] not in (match['player_a_id'], match['player_b_id']) or
                (table != 'auto_draft_settings' and (fixture is None or fixture['week_id'] != match['week_id']))):
                block('CONFLICTING_PICK_RELATIONSHIP', table, row['id'])
                continue
            roster[actual_weeks[match['week_id']]['season_id'], row['player_id']].append(provenance(table, row['id']))
            if table == 'auto_draft_settings': continue
            if (row['team'] == fixture['home']) + (row['team'] == fixture['away']) != 1:
                block('UNRESOLVED_PICK_PARTICIPANT', table, row['id'])
                continue
            resolved = resolved_fixtures.get(fixture['id'])
            if resolved is None:
                missing('FIXTURE_MAPPING_REQUIRED', table, row['id'])
                continue
            club = resolved[1 if row['team'] == fixture['home'] else 2]
            update(table, row, dict(selected_club_id=ids['clubs'][club]),
                   provenance(table, row['id'], fixture_bindings[fixture['id']]['evidence']))
    for row in tables['correspondent_sources']:
        week = actual_weeks.get(row['week_id'])
        fixture = actual_fixtures.get(row['fixture_id'])
        pid = row['submitted_by_player_id']
        if (week is None or (row['fixture_id'] is not None and (fixture is None or fixture['week_id'] != row['week_id'])) or
            (pid is not None and pid not in actual_players)):
            block('CORRESPONDENT_RELATIONSHIP_CONFLICT', 'correspondent_sources', row['id'])
        elif pid is not None:
            roster[week['season_id'], pid].append(provenance('correspondent_sources', row['id']))
    for (sid, pid), proof in sorted(roster.items()):
        insert('league_season_memberships', dict(season_id=sid, membership_id=pid, joined_at=None, left_at=None), proof)
    for sid in actual_seasons:
        if not any(key[0] == sid for key in roster):
            missing('EMPTY_SEASON_ROSTER_UNRESOLVED', 'seasons', sid)

    for row in tables['api_sync_states']:
        key = resolved_seasons.get(row['season_id'])
        # Preserve the legacy namespace. A reviewed Season mapping, not an
        # unpersisted provider season-object ID, establishes the new FK.
        if key is None or row['provider'] not in {'football-data.org', 'football-data'}:
            missing('SYNC_PROVIDER_SEASON_UNRESOLVED', 'api_sync_states', row['id'])
        else:
            update('api_sync_states', row, dict(football_season_id=ids['football_seasons'][key]), provenance('api_sync_states', row['id']))
    for row in tables['club_season_enrichment']:
        pk = dict(season_id=row['season_id'], sportmonks_team_id=row['sportmonks_team_id'])
        sk = resolved_seasons.get(row['season_id'])
        alias = aliases.get((sk, name_key(row['footy_club'])))
        if sk is None or alias is None:
            missing('CACHE_IDENTITY_UNRESOLVED', 'club_season_enrichment', pk)
            continue
        ck = alias['club_key']
        if (ck not in clubs or str(row['sportmonks_team_id']) != provider_ids['club', ck].get('sportmonks') or
            str(row['sportmonks_season_id']) != provider_ids['football_season', sk].get('sportmonks') or
            str(row['league_id']) != provider_ids['competition', seasons[sk]['competition_key']].get('sportmonks')):
            block('CONFLICTING_PROVIDER_IDS', 'club_season_enrichment', pk)
            continue
        update('club_season_enrichment', row, dict(football_season_id=ids['football_seasons'][sk], club_id=ids['clubs'][ck]),
               provenance('club_season_enrichment', pk, alias['evidence']), key=pk)
    jobs = {}
    for row in tables['pick_insight_refreshes']:
        wid = row['week_id']
        target = resolved_weeks.get(wid)
        if target is None or row['season_id'] != actual_weeks[wid]['season_id']:
            block('TASK_SEASON_WEEK_CONFLICT', 'pick_insight_refreshes', wid)
            continue
        if row['status'] not in {'pending', 'completed'} or row['claim_token'] is not None or row['lease_until'] is not None:
            block('UNSUPPORTED_OR_IN_FLIGHT_TASK', 'pick_insight_refreshes', wid)
            continue
        if not row['created_at'] or (row['status'] == 'completed') != (row['completed_at'] is not None):
            block('TASK_RECEIPT_CONFLICT', 'pick_insight_refreshes', wid)
            continue
        job = dict(football_matchweek_id=rounds[target], job_kind='club_enrichment', status=row['status'],
                   created_at=row['created_at'], completed_at=row['completed_at'], claim_token=None, lease_until=None, last_error=None)
        if rounds[target] in jobs:
            block('CANONICAL_JOB_COLLISION', 'pick_insight_refreshes', wid)
            continue
        jobs[rounds[target]] = job
        insert('football_refresh_jobs', job, provenance('pick_insight_refreshes', {'week_id': wid}))
        mappings.append(dict(table='pick_insight_refreshes', key={'week_id': wid}, target=rounds[target],
                             classification='deterministic / fully supported', provenance=provenance('pick_insight_refreshes', wid)))
    for row in tables['match_events']:
        fixture = actual_fixtures.get(row['fixture_id'])
        resolved = resolved_fixtures.get(row['fixture_id'])
        if resolved is None:
            missing('EVENT_FIXTURE_UNRESOLVED', 'match_events', row['id'])
            continue
        sk, home, away = resolved
        candidates = []
        if row['side'] is not None:
            if row['side'] not in {'home', 'away'}:
                block('EVENT_CLUB_CONFLICT', 'match_events', row['id'])
                continue
            candidates.append(home if row['side'] == 'home' else away)
        if row['team_provider_id'] is not None:
            matches = [ck for ck in (home, away) if provider_ids['club', ck].get(row['provider']) == str(row['team_provider_id'])]
            if len(matches) != 1:
                block('EVENT_CLUB_CONFLICT', 'match_events', row['id'])
                continue
            candidates.extend(matches)
        if row['team_name'] is not None:
            alias = aliases.get((sk, name_key(row['team_name'])))
            if alias is None or alias['club_key'] not in {home, away}:
                block('EVENT_CLUB_CONFLICT', 'match_events', row['id'])
                continue
            candidates.append(alias['club_key'])
        if not candidates:
            left_null('match_events', {'id': row['id']}, ['club_id'])
        elif len(set(candidates)) != 1:
            block('EVENT_CLUB_CONFLICT', 'match_events', row['id'])
        else:
            update('match_events', row, dict(club_id=ids['clubs'][candidates[0]]),
                   provenance('match_events', row['id'], fixture_bindings[row['fixture_id']]['evidence']))
    recipients = set()
    for row in groups['recipient']:
        key = (row['recipient_kind'], row['email_normalized'])
        if key in recipients: block('DUPLICATE_RECIPIENT')
        recipients.add(key)
        insert('league_notification_recipients', dict(league_id=1, kind=key[0], email_normalized=key[1],
                                                     enabled=row['enabled'], user_id=None),
               provenance('manifest_recipient', digest(key), row['evidence']))
        left_null('league_notification_recipients', {'kind': key[0], 'email_sha256': digest(key[1])}, ['user_id'])

    # Validate preserved Correspondent edges without reading content into output.
    sources = {r['id']: r for r in tables['correspondent_sources']}
    recaps = {r['id']: r for r in tables['weekly_recaps']}
    classification_jobs = {r['id']: r for r in tables['correspondent_classification_jobs']}
    for row in classification_jobs.values():
        week = actual_weeks.get(row['week_id'])
        if week is None or week['season_id'] != row['season_id']:
            block('CORRESPONDENT_RELATIONSHIP_CONFLICT', 'correspondent_classification_jobs', row['id'])
    for row in tables['correspondent_classification_job_items']:
        if classification_jobs[row['job_id']]['week_id'] != sources[row['source_id']]['week_id']:
            block('CORRESPONDENT_RELATIONSHIP_CONFLICT', 'correspondent_classification_job_items', row['id'])
    for row in tables['recap_source_usages']:
        if recaps[row['recap_id']]['week_id'] != sources[row['source_id']]['week_id']:
            block('CORRESPONDENT_RELATIONSHIP_CONFLICT', 'recap_source_usages', row['id'])
    for row in tables['weekly_recap_selections']:
        if recaps[row['recap_id']]['week_id'] != row['week_id']:
            block('CORRESPONDENT_RELATIONSHIP_CONFLICT', 'weekly_recap_selections', row['id'])

    # Consolidate per-row nullable updates so counts mean rows, not field writes.
    merged = defaultdict(dict)
    for table, records in updates.items():
        for record in records:
            if not record['values']: continue
            key = canonical_json(record['key'])
            if key not in merged[table]:
                merged[table][key] = record
            else:
                merged[table][key]['values'].update(record['values'])
    updates = {t: list(rows.values()) for t, rows in merged.items()}
    blockers = sorted({canonical_json(b): b for b in issues}.values(), key=canonical_json)
    # A global blocker invalidates the proposal as a whole. Keep candidates
    # inspectable without labeling dependent/partial rows fully supported.
    if blockers:
        for collection in (proposed, updates):
            for records in collection.values():
                for record in records:
                    record['classification'] = 'unresolved / blocker'
        for record in mappings:
            record['classification'] = 'unresolved / blocker'
    counts = {'inserts': {t: len(rows) for t, rows in sorted(proposed.items()) if rows},
              'updates': {t: len(rows) for t, rows in sorted(updates.items()) if rows}}
    # IDs and proposal counts remain diagnostic on blocked plans; never readiness.
    gates = inspection.get('execution_gates', [])
    result = dict(plan_version=2, status='blocked' if blockers else ('gated' if gates else 'planned'), execution_authorized=False,
                  historical_findings=inspection.get('historical_findings', []), execution_gates=gates,
                  manifest_checksum=manifest['checksum'], source=manifest['source'],
                  id_rule='Per table: observed max(id) + 1..N; Players numeric ID order; entity SHA256 keys lexical; matchweeks (season key, number) order. No persistence.',
                  proposed_ids=ids, new_rows=dict(proposed), nullable_updates=updates,
                  mappings=mappings, intentionally_null=nulls, blockers=blockers,
                  expected_write_counts=counts, counts_are_complete=not blockers,
                  preservation=dict(schema_sha256=manifest['source']['schema_sha256'],
                                    protected_fingerprints=inspection['protected_fingerprints'],
                                    stage1_receipt='unchanged; no Stage 2 receipt',
                                    legacy='All legacy values, IDs, strings, results, scores, receipts, claims and delivery history unchanged',
                                    execution_preconditions=['future trigger-effect review', 'future scoring/preservation equality verification']))
    # Roundtrip guarantees canonical list/dict order independent of SQL row order.
    import json
    return json.loads(canonical_json(result))


def plan_summary(plan):
    """Only fixed codes, IDs, hashes and counts leave the private planning API."""
    return dict(status=plan['status'], execution_authorized=False, plan_sha256=digest(canonical_json(plan)),
                source=plan['source'], manifest_checksum=plan['manifest_checksum'],
                expected_write_counts=plan['expected_write_counts'], counts_are_complete=plan['counts_are_complete'],
                blocker_codes=sorted({b['code'] for b in plan['blockers']}),
                execution_gates=plan.get('execution_gates', []),
                historical_findings=plan.get('historical_findings', []))
