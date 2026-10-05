"""Explicit, additive SQLite schema. Never registered with Flask/game metadata."""

TABLES = frozenset({"football_reference_seasons", "football_reference_fixtures",
                    "football_reference_events", "football_reference_syncs"})

DDL = (
    """CREATE TABLE IF NOT EXISTS football_reference_seasons (
        id INTEGER PRIMARY KEY, provider TEXT NOT NULL, external_season_id INTEGER NOT NULL,
        league_id INTEGER NOT NULL, name TEXT NOT NULL, finished INTEGER, is_current INTEGER,
        sync_status TEXT NOT NULL, fixture_count INTEGER NOT NULL,
        last_successful_sync_at TEXT NOT NULL,
        UNIQUE(provider, external_season_id))""",
    """CREATE TABLE IF NOT EXISTS football_reference_fixtures (
        id INTEGER PRIMARY KEY, provider TEXT NOT NULL, external_fixture_id INTEGER NOT NULL,
        reference_season_id INTEGER NOT NULL REFERENCES football_reference_seasons(id),
        league_id INTEGER NOT NULL, kickoff_utc TEXT NOT NULL,
        home_team_id INTEGER NOT NULL, home_team_name TEXT NOT NULL,
        away_team_id INTEGER NOT NULL, away_team_name TEXT NOT NULL,
        home_score INTEGER CHECK(home_score >= 0), away_score INTEGER CHECK(away_score >= 0),
        state_id INTEGER NOT NULL, state TEXT NOT NULL, provider_updated_at TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(provider, external_fixture_id))""",
    """CREATE TABLE IF NOT EXISTS football_reference_events (
        id INTEGER PRIMARY KEY,
        reference_fixture_id INTEGER NOT NULL REFERENCES football_reference_fixtures(id),
        provider TEXT NOT NULL, external_event_id INTEGER NOT NULL,
        type_id INTEGER NOT NULL, event_type TEXT NOT NULL, minute INTEGER, extra_minute INTEGER,
        team_provider_id INTEGER, team_name TEXT, player_provider_id INTEGER, player_name TEXT,
        related_player_provider_id INTEGER, related_player_name TEXT,
        sub_type_id INTEGER, detail TEXT, info TEXT, addition TEXT, running_score TEXT, sort_order INTEGER,
        rescinded INTEGER, is_active INTEGER NOT NULL, is_present INTEGER NOT NULL, provider_updated_at TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(provider, external_event_id))""",
    """CREATE INDEX IF NOT EXISTS football_reference_fixture_season
        ON football_reference_fixtures(reference_season_id, kickoff_utc)""",
    """CREATE INDEX IF NOT EXISTS football_reference_event_fixture
        ON football_reference_events(reference_fixture_id, is_active)""",
    """CREATE TABLE IF NOT EXISTS football_reference_syncs (
        id INTEGER PRIMARY KEY, reference_season_id INTEGER NOT NULL REFERENCES football_reference_seasons(id),
        status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
        counts_json TEXT NOT NULL)""",
)


def create_schema(connection):
    """Caller owns transaction; no executescript (which could implicitly commit)."""
    for statement in DDL:
        connection.execute(statement)
