# PR20 — Football reference data foundation

This is a separately maintained Sportmonks research dataset in the same SQLite
file as Footy. It is not official Pick 'Em state. No existing application module,
detector, route, startup routine, migration, or live normalization is changed.
There is no automatic sync or backfill. All new schema and writes happen only
through the explicit maintenance command.

## Schema and isolation

`football_reference_schema.py` owns four additive tables, created with
`CREATE TABLE IF NOT EXISTS` inside the import transaction:

- `football_reference_seasons`: provider/season identity (unique), league ID,
  provider season name, nullable finished/current flags, successful sync status,
  fixture count and last successful sync time.
- `football_reference_fixtures`: stable internal ID, unique provider/fixture ID,
  reference-season FK, league, UTC kickoff, provider team IDs/names, nullable
  CURRENT scores, raw state ID and normalized state, provider update time
  (`updated_at` or `last_processed_at`) and local creation/update times.
- `football_reference_events`: stable internal ID, reference-fixture FK, unique
  provider/event ID, raw type/subtype IDs, normalized type, timing, sort order,
  team/player/related-player IDs and names, detail/info/addition/result,
  nullable rescinded flag, presence/active flags, provider and local timestamps.
- `football_reference_syncs`: successful season refresh start/completion times
  and fetched snapshot coverage counts. Failures deliberately add no audit row
  and do not change a prior success status or timestamp; the CLI returns a
  nonzero exit and a sanitized error instead.

All FKs stay inside this reference namespace. No optional Footy fixture link is
created: PR20 does not establish a deterministic cross-provider mapping. There
is no write path to seasons, weeks, fixtures, results, picks, matchups, standings,
payouts, finalization data, archived Year 1 data, or any other existing game table.

The application currently combines additive table creation with legacy game
migrations at Flask import. This maintenance path deliberately does not import
Flask or register its models with game metadata. It reuses the additive pattern
but creates only its own schema, avoiding those unrelated migration side effects.
It needs no new dependency. Future schema changes must remain explicit and scoped
to these reference tables; `IF NOT EXISTS` is not a column migration mechanism.

SQLite `mode=rw` requires an existing, explicitly supplied local file. Foreign
keys are enabled. A dedicated connection owns `BEGIN IMMEDIATE`; schema creation,
upserts, event reconciliation, season metadata and success audit commit together.
Every failure rolls back the transaction, including newly created tables. After
schema creation, a SQLite authorizer denies INSERT/UPDATE/DELETE against any
non-reference table, including writes attempted by database triggers. There is
no shared ORM session that could flush unrelated pending game changes.

## Fetch and validation

The importer uses the audited fixtures filter pattern:

```text
GET /v3/football/fixtures
  ?filters=fixtureSeasons:23614
  &include=participants;scores;events
  &page=1&per_page=50
GET /v3/football/seasons/23614
```

All pages and season metadata are fetched and normalized before opening the
write connection. Pages must identify their expected page number and a boolean
`has_more`; repeated pages, missing pagination, empty intermediate pages,
excess fixture count, truncated JSON and failed HTTP requests fail closed.
The client never follows a provider `next_page` URL. Authorization stays in an
HTTP header; redirects are disabled using the existing season client's handler.
Requests have a timeout and an 8 MiB per-response cap. Error messages exclude
URLs, response bodies and tokens. Rate-limit metadata is retained on the client;
HTTP failures expose a bounded retry delay as in the live client, with no
automatic retry loop. A failed run can safely be retried later.

Only audited season IDs 23614 (2024/25), 25583 (2025/26), and 28083 (2026/27)
are accepted. No access to 2023/24 is assumed. Every snapshot requires:

- Exactly 380 distinct fixture IDs, all in the requested season and PL league 8.
- Exactly two distinct participants with one home and one away designation,
  positive provider IDs, nonempty names, and parseable UTC kickoff.
- Explicit array-valued scores and events includes. Empty events are valid;
  absent or null events cannot be mistaken for a complete empty snapshot.
- Nonnegative integer scores, correct participant attribution, no duplicate
  CURRENT side scores and no partial CURRENT score pair.
- Unique event IDs with the expected fixture ID, valid nullable field types,
  and participant attribution to a fixture team when supplied.

Historical seasons require finished fixture states (5, 7, 8) and a usable CURRENT
score pair for every fixture. Current-season final fixtures have the same score
requirement; future fixtures can have empty scores/events. A provider-finished
current season is validated like a completed season. Available non-final CURRENT
scores may be stored, with their provider state retained; research consumers must
filter finished states when they need final results.

Provider IDs are the upsert keys, never names. Changed values update in place,
preserving internal IDs and creation times. Repeated runs cannot add duplicate
fixtures/events. Fixture-ID sets cannot silently change on refresh, and existing
fixture/event IDs cannot move to another parent. Such changes require manual
investigation rather than erasing prior history.

## Events, corrections and type 17

Sportmonks' authoritative [event type definitions](https://docs.sportmonks.com/v3/definitions/types/events)
identify **17 as missed-penalty / MISSED_PENALTY**. Reference normalization uses
`missed_penalty`; it is not counted as a goal. Types 14/15/16 are goal/own-goal/
scored-penalty. The existing live type map remains unchanged. Type IDs
10, 14, 15, 16, 17, 18, 19, 20, 21 and 1697 are retained; unknown positive IDs
are stored with `event_type=unknown`, never dropped.

The [provider changelog, 2025-06-30](https://docs.sportmonks.com/v3/changelog/changelog)
explains that rescinded is nullable for non-card and older events. Store that
null rather than inventing a false provider assertion. Only explicit true marks
an event rescinded. A row in the latest complete provider snapshot has
`is_present=1`; its `is_active` is false only when explicitly rescinded. A formerly
stored event omitted by a later complete snapshot is retained with both flags
false. A subsequent reappearance updates/reactivates that same row. Corrections
to existing IDs update names, type, score and other fields in place. This is a
latest-snapshot store with withdrawn-event retention, not a revision archive.

## Commands

Set `SPORTMONKS_API_TOKEN` securely in the environment. The commands never read
`DB_PATH` or default to `/data/pickem.db`. Use an existing local SQLite snapshot
of pickem.db for validation. No live API import or production backfill was run as
part of this PR; automated tests use generated mock responses only.

```powershell
# Fetch, validate, and report; does not open or create any database.
.\.venv\Scripts\python.exe scripts/sync_football_reference.py --season 23614 --dry-run

# Explicit local file; repeat --season to import additional audited seasons.
.\.venv\Scripts\python.exe scripts/sync_football_reference.py --db local_snapshot.db --season 23614 --season 25583 --season 28083

# Read-only inspection; no Flask import, initialization, migration or network.
.\.venv\Scripts\python.exe scripts/inspect_football_reference.py --db local_snapshot.db
```

Each season is an independent atomic refresh. If a later season fails, earlier
successful seasons from that invocation remain committed. Keep a local backup
before maintenance; do not invoke these commands against production as part of
PR20. Inspection uses `mode=ro`, `query_only`, and one read transaction. It returns
an empty list if the reference schema has not been installed.

## Audit interpretation

Per-season JSON reports include fixture count, scored fixture count, fixtures
with events, total events, goal events, goals with both player ID and nonempty
name, earliest/latest kickoff, raw type distribution, sync status and last
successful timestamp. Total events and distributions describe **present** events,
including rescinded cards, so they can be compared to an unfiltered provider
audit. Active, inactive, rescinded, withdrawn and retained counts are additional
fields. Withdrawn rows never inflate current snapshot totals. Sync output and
audit rows describe the fetched snapshot; inspection additionally sees retained
rows withdrawn in previous refreshes.

The user's read-only subscription audit established 380 fixtures/380 scored/380
with events and 6,185 events for 23614, and 380/380/380 with 5,885 events for 25583.
Season 28083 has 380 scheduled fixtures with evolving scores/events. These audit
counts are review baselines, not hard-coded event-count constraints: legitimate
provider corrections or a zero-event fixture must not be rejected. Goal identity
coverage is reported instead of assumed. Stored research data must never be
promoted into official game results by this importer.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_football_reference.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Mock tests cover validation, current/final data, full pagination and late failures,
duplicate IDs, malformed scores/participants, idempotence, nullable/unknown event
types, correction/withdrawal/reactivation, transaction rollback, game-table
sentinels and trigger denial, explicit DB selection, dry-run, read-only inspection
and deterministic audit counts. PR19 detectors and Pick Insight remain untouched.
