"""Stage 1 DDL only. No ORM registration, connections, imports of Flask, or seeding.

This is an expansion schema, NOT the League #2-ready contract. Legacy columns
and global uniqueness remain intact until separately reviewed hardening.
"""

VERSION = "private_beta_001_additive"

TABLE_DDL = (
    """CREATE TABLE schema_migrations (
        version TEXT PRIMARY KEY NOT NULL, checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL, before_schema_hash TEXT NOT NULL,
        after_schema_hash TEXT NOT NULL)""",
    """CREATE TABLE competitions (
        id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE CHECK(trim(code) <> ''),
        name TEXT NOT NULL CHECK(trim(name) <> ''), country_code TEXT)""",
    """CREATE TABLE competition_provider_links (
        competition_id INTEGER NOT NULL REFERENCES competitions(id) ON DELETE RESTRICT,
        provider TEXT NOT NULL, external_competition_id TEXT NOT NULL,
        PRIMARY KEY(provider, external_competition_id), UNIQUE(competition_id, provider))""",
    """CREATE TABLE football_seasons (
        id INTEGER PRIMARY KEY, competition_id INTEGER NOT NULL REFERENCES competitions(id) ON DELETE RESTRICT,
        season_key TEXT NOT NULL, name TEXT NOT NULL, start_year INTEGER,
        starts_on TEXT, ends_on TEXT, sync_enabled INTEGER NOT NULL DEFAULT 0 CHECK(sync_enabled IN (0,1)),
        UNIQUE(competition_id, season_key),
        CHECK(starts_on IS NULL OR ends_on IS NULL OR starts_on <= ends_on))""",
    """CREATE TABLE football_season_provider_links (
        football_season_id INTEGER NOT NULL REFERENCES football_seasons(id) ON DELETE RESTRICT,
        provider TEXT NOT NULL, external_season_id TEXT NOT NULL,
        PRIMARY KEY(provider, external_season_id), UNIQUE(football_season_id, provider))""",
    """CREATE TABLE football_matchweeks (
        id INTEGER PRIMARY KEY, football_season_id INTEGER NOT NULL REFERENCES football_seasons(id) ON DELETE RESTRICT,
        number INTEGER NOT NULL CHECK(number > 0), UNIQUE(football_season_id, number))""",
    """CREATE TABLE clubs (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL CHECK(trim(name) <> ''),
        short_name TEXT, country_code TEXT,
        is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)))""",
    """CREATE TABLE club_provider_links (
        club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE RESTRICT, provider TEXT NOT NULL,
        external_club_id TEXT NOT NULL,
        PRIMARY KEY(provider, external_club_id), UNIQUE(club_id, provider))""",
    """CREATE TABLE football_season_clubs (
        football_season_id INTEGER NOT NULL REFERENCES football_seasons(id) ON DELETE RESTRICT,
        club_id INTEGER NOT NULL REFERENCES clubs(id) ON DELETE RESTRICT, PRIMARY KEY(football_season_id, club_id))""",
    """CREATE TABLE club_aliases (
        football_season_id INTEGER NOT NULL, normalized_name TEXT NOT NULL, club_id INTEGER NOT NULL,
        PRIMARY KEY(football_season_id, normalized_name),
        FOREIGN KEY(football_season_id, club_id) REFERENCES football_season_clubs(football_season_id, club_id) ON DELETE RESTRICT)""",
    """CREATE TABLE users (
        id INTEGER PRIMARY KEY, display_name TEXT NOT NULL CHECK(trim(display_name) <> ''),
        email TEXT, email_normalized TEXT, email_verified_at TEXT,
        favorite_club_id INTEGER REFERENCES clubs(id) ON DELETE RESTRICT,
        status TEXT NOT NULL DEFAULT 'unclaimed' CHECK(status IN ('unclaimed','active','disabled')),
        is_platform_admin INTEGER NOT NULL DEFAULT 0 CHECK(is_platform_admin IN (0,1)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        CHECK((email IS NULL) = (email_normalized IS NULL)),
        CHECK(email_verified_at IS NULL OR email IS NOT NULL))""",
    """CREATE TABLE user_identities (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        issuer TEXT NOT NULL, subject TEXT NOT NULL, created_at TEXT NOT NULL,
        last_authenticated_at TEXT, UNIQUE(issuer, subject))""",
    """CREATE TABLE leagues (
        id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE CHECK(slug = lower(trim(slug)) AND slug <> ''),
        name TEXT NOT NULL CHECK(trim(name) <> ''),
        status TEXT NOT NULL DEFAULT 'setup' CHECK(status IN ('setup','active','archived')),
        timezone TEXT NOT NULL DEFAULT 'America/New_York',
        correspondent_enabled INTEGER NOT NULL DEFAULT 0 CHECK(correspondent_enabled IN (0,1)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE league_invitations (
        id INTEGER PRIMARY KEY, league_id INTEGER NOT NULL REFERENCES leagues(id) ON DELETE RESTRICT,
        membership_id INTEGER REFERENCES players(id) ON DELETE RESTRICT, email_normalized TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL,
        accepted_by_user_id INTEGER REFERENCES users(id) ON DELETE RESTRICT, accepted_at TEXT,
        revoked_at TEXT, created_at TEXT NOT NULL,
        CHECK((accepted_by_user_id IS NULL) = (accepted_at IS NULL)))""",
    """CREATE TABLE league_notification_recipients (
        league_id INTEGER NOT NULL REFERENCES leagues(id) ON DELETE RESTRICT,
        kind TEXT NOT NULL CHECK(kind IN ('recap','admin_alert')),
        email_normalized TEXT NOT NULL, user_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
        PRIMARY KEY(league_id, kind, email_normalized))""",
    """CREATE TABLE league_season_memberships (
        season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE RESTRICT,
        membership_id INTEGER NOT NULL REFERENCES players(id) ON DELETE RESTRICT, joined_at TEXT, left_at TEXT,
        PRIMARY KEY(season_id, membership_id),
        CHECK(joined_at IS NULL OR left_at IS NULL OR left_at >= joined_at))""",
    """CREATE TABLE league_week_fixtures (
        week_id INTEGER NOT NULL REFERENCES weeks(id) ON DELETE RESTRICT, fixture_id INTEGER NOT NULL REFERENCES fixtures(id) ON DELETE RESTRICT,
        display_order INTEGER NOT NULL CHECK(display_order > 0),
        PRIMARY KEY(week_id, fixture_id), UNIQUE(week_id, display_order))""",
    """CREATE TABLE football_refresh_jobs (
        football_matchweek_id INTEGER NOT NULL REFERENCES football_matchweeks(id) ON DELETE RESTRICT,
        job_kind TEXT NOT NULL CHECK(job_kind = 'club_enrichment'),
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed')),
        created_at TEXT NOT NULL, completed_at TEXT, claim_token TEXT, lease_until TEXT, last_error TEXT,
        PRIMARY KEY(football_matchweek_id, job_kind))""",
)

# Every addition is nullable, with NO default: never silently assign League #1
# or mutate historical values. Existing application models ignore these columns.
ADDITIONS = {
    "players": {
        "league_id": "INTEGER REFERENCES leagues(id) ON DELETE RESTRICT",
        "user_id": "INTEGER REFERENCES users(id) ON DELETE RESTRICT",
        "name_key": "TEXT",  # Future service: NFKC(strip(name)).casefold().
        "role": "TEXT CHECK(role IN ('member','commissioner'))",
        "status": "TEXT CHECK(status IN ('active','inactive'))",
        "joined_at": "TEXT", "left_at": "TEXT",
        "recap_opt_in": "INTEGER CHECK(recap_opt_in IN (0,1))",
    },
    "seasons": {
        "league_id": "INTEGER REFERENCES leagues(id) ON DELETE RESTRICT",
        "football_season_id": "INTEGER REFERENCES football_seasons(id) ON DELETE RESTRICT",
        "rules_version": "TEXT", "stake_minor_units": "INTEGER CHECK(stake_minor_units >= 0)",
        "currency": "TEXT",
    },
    "weeks": {"football_matchweek_id": "INTEGER REFERENCES football_matchweeks(id) ON DELETE RESTRICT"},
    "fixtures": {
        "football_season_id": "INTEGER REFERENCES football_seasons(id) ON DELETE RESTRICT",
        "football_matchweek_id": "INTEGER REFERENCES football_matchweeks(id) ON DELETE RESTRICT",
        "home_club_id": "INTEGER REFERENCES clubs(id) ON DELETE RESTRICT",
        "away_club_id": "INTEGER REFERENCES clubs(id) ON DELETE RESTRICT",
    },
    "picks": {"selected_club_id": "INTEGER REFERENCES clubs(id) ON DELETE RESTRICT"},
    "auto_draft_preferences": {"selected_club_id": "INTEGER REFERENCES clubs(id) ON DELETE RESTRICT"},
    "match_events": {"club_id": "INTEGER REFERENCES clubs(id) ON DELETE RESTRICT"},
    "api_sync_states": {"football_season_id": "INTEGER REFERENCES football_seasons(id) ON DELETE RESTRICT"},
    "club_season_enrichment": {
        "football_season_id": "INTEGER REFERENCES football_seasons(id) ON DELETE RESTRICT",
        "club_id": "INTEGER REFERENCES clubs(id) ON DELETE RESTRICT",
    },
}

INDEX_DDL = (
    "CREATE UNIQUE INDEX uix_foundation_football_year ON football_seasons(competition_id,start_year) WHERE start_year IS NOT NULL",
    "CREATE UNIQUE INDEX uix_foundation_user_email ON users(email_normalized) WHERE email_normalized IS NOT NULL",
    "CREATE UNIQUE INDEX uix_foundation_membership_user ON players(league_id,user_id) WHERE league_id IS NOT NULL AND user_id IS NOT NULL",
    "CREATE UNIQUE INDEX uix_foundation_membership_name ON players(league_id,name_key) WHERE league_id IS NOT NULL AND name_key IS NOT NULL",
    "CREATE INDEX ix_foundation_players_user ON players(user_id,status)",
    "CREATE INDEX ix_foundation_players_league ON players(league_id,status)",
    "CREATE INDEX ix_foundation_seasons_league ON seasons(league_id)",
    "CREATE INDEX ix_foundation_seasons_football ON seasons(football_season_id)",
    "CREATE INDEX ix_foundation_weeks_football ON weeks(football_matchweek_id)",
    "CREATE INDEX ix_foundation_fixtures_season ON fixtures(football_season_id,kickoff_utc)",
    "CREATE INDEX ix_foundation_fixtures_round ON fixtures(football_matchweek_id,kickoff_utc)",
    "CREATE INDEX ix_foundation_slate_fixture ON league_week_fixtures(fixture_id,week_id)",
    "CREATE INDEX ix_foundation_roster_member ON league_season_memberships(membership_id,season_id)",
    "CREATE INDEX ix_foundation_refresh_claim ON football_refresh_jobs(status,lease_until)",
    "CREATE INDEX ix_foundation_identities_user ON user_identities(user_id)",
    "CREATE INDEX ix_foundation_favorite_club ON users(favorite_club_id)",
    "CREATE INDEX ix_foundation_invitation_league ON league_invitations(league_id)",
)

# Final ownership/roster/slate cross-table triggers and NOT NULL hardening are
# deliberately deferred: no league or user is populated or enabled in Stage 1.

# Reviewed pre-foundation model columns at adf2117; no app import needed.
REQUIRED_COLUMNS = {
    'players': ('id', 'name'),
    'seasons': ('id', 'code', 'name', 'is_active', 'is_archived', 'api_competition_code', 'api_season_year', 'created_at'),
    'weeks': ('id', 'season_id', 'number', 'room_code', 'status', 'finalized_at'),
    'fixtures': ('id', 'week_id', 'match_number', 'home', 'away', 'venue', 'external_match_id', 'kickoff_utc', 'api_status'),
    'matchups': ('id', 'week_id', 'player_a_id', 'player_b_id', 'first_picker_id'),
    'picks': ('id', 'matchup_id', 'player_id', 'fixture_id', 'team', 'created_at'),
    'auto_draft_settings': ('id', 'matchup_id', 'player_id', 'enabled', 'confirmed_at'),
    'auto_draft_preferences': ('id', 'matchup_id', 'player_id', 'fixture_id', 'team', 'priority'),
    'results': ('id', 'fixture_id', 'outcome', 'home_score', 'away_score', 'source', 'updated_at'),
    'api_sync_states': ('id', 'season_id', 'provider', 'last_attempt_at', 'last_success_at', 'last_error', 'requests_remaining', 'reset_seconds', 'fixtures_imported', 'results_imported', 'pending_matches', 'unmatched_matches'),
    'weekly_recaps': ('id', 'week_id', 'revision', 'status', 'title', 'body_markdown', 'context_json', 'context_hash', 'prompt_version', 'model', 'provider_response_id', 'error_message', 'correspondent_version', 'source_count', 'external_context_hash', 'automation_key', 'created_at', 'completed_at'),
    'correspondent_sources': ('id', 'week_id', 'fixture_id', 'provider', 'source_type', 'external_id', 'canonical_url', 'author_name', 'body_text', 'published_at', 'submitted_by_player_id', 'submission_note', 'metadata_json', 'content_hash', 'status', 'created_at'),
    'correspondent_source_classifications': ('id', 'source_id', 'prompt_version', 'pass_number', 'pickem_impact', 'editorial_functions_json', 'article_use', 'confidence', 'route', 'reason_codes_json', 'reason', 'model', 'provider_response_id', 'created_at'),
    'correspondent_classification_jobs': ('id', 'season_id', 'week_id', 'prompt_version', 'pass_number', 'model', 'openai_batch_id', 'input_file_id', 'output_file_id', 'error_file_id', 'status', 'total_count', 'completed_count', 'failed_count', 'error_message', 'created_at', 'submitted_at', 'last_synced_at', 'completed_at'),
    'correspondent_classification_job_items': ('id', 'job_id', 'source_id', 'custom_id', 'active_reservation_key', 'status', 'provider_response_id', 'error_message', 'imported_at'),
    'correspondent_x_collection_states': ('id', 'week_id', 'status', 'window_start', 'window_end', 'next_token', 'page_count', 'retrieved_count', 'persisted_count', 'lease_token', 'lease_expires_at', 'last_error', 'cap_reached_at', 'completed_at', 'created_at', 'updated_at'),
    'correspondent_x_page_receipts': ('id', 'collection_id', 'claim_token', 'request_token', 'next_token', 'received_count', 'created_count', 'created_at'),
    'correspondent_email_deliveries': ('id', 'recap_id', 'delivery_marker', 'recipients_json', 'status', 'claim_token', 'claim_expires_at', 'gmail_message_id', 'error_message', 'created_at', 'updated_at', 'sent_at'),
    'correspondent_notifications': ('id', 'week_id', 'event_type', 'delivery_marker', 'recipients_json', 'payload_json', 'status', 'claim_token', 'claim_expires_at', 'gmail_message_id', 'error_message', 'created_at', 'updated_at', 'sent_at'),
    'recap_source_usages': ('id', 'recap_id', 'source_id'),
    'weekly_recap_selections': ('id', 'week_id', 'recap_id', 'selected_at'),
    'fixture_provider_links': ('id', 'fixture_id', 'provider', 'external_fixture_id'),
    'live_fixture_states': ('fixture_id', 'provider', 'state', 'is_live', 'home_score', 'away_score', 'minute', 'extra_minute', 'provider_updated_at', 'last_synced_at'),
    'match_events': ('id', 'fixture_id', 'provider', 'external_event_id', 'event_type', 'minute', 'extra_minute', 'team_provider_id', 'team_name', 'side', 'player_provider_id', 'player_name', 'related_player_provider_id', 'related_player_name', 'detail', 'running_score', 'sort_order', 'is_active', 'provider_updated_at', 'created_at', 'updated_at', 'revisions'),
    'club_season_enrichment': ('season_id', 'sportmonks_team_id', 'sportmonks_season_id', 'league_id', 'footy_club', 'crest_url', 'top_scorers', 'goals', 'synced_at'),
    'pick_insight_refreshes': ('week_id', 'season_id', 'status', 'created_at', 'completed_at', 'claim_token', 'lease_until'),
}
