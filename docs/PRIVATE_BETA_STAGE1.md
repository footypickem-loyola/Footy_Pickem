# Private Beta Foundation — Stage 1 additive PR

Authorization: **prepare the PR and tests only**. No merge, deployment, backfill,
League #2 activation, or production migration is authorized. All migration
execution during preparation used disposable synthetic databases.

## What this changes

The existing app migrated its database during module import. That is incompatible
with reviewing an additive foundation independently of executing it. This PR
removes that call and separates maintenance from startup.

- `foundation_schema.py`: pure DDL declarations outside the Flask ORM metadata.
  18 new tables (17 domain/operations tables plus `schema_migrations`), 24 nullable
  additions to nine existing tables, 17 indexes. No result-snapshot table.
- `foundation_migrations.py`: stdlib-only read-only inspection and explicit
  transactional application. It never imports Flask, reads DB_PATH, calls a
  provider, creates accounts/leagues, or backfills rows.
- Web import/Gunicorn entry/main startup no longer calls `ensure_database_schema`
  or `create_all`. Legacy initialization is available only through the explicit
  `--initialize-legacy-schema` CLI operation with an explicit DB_PATH.
- The shared-brief worker validates its existing operations database read-only
  at startup. Missing/invalid store means configuration_error, not schema creation.
  Its existing valid store continues to work. Explicit operations initialization
  is provided by `scripts/generate_shared_briefs.py --init-operations-store`.

No runtime foundation ORM fields are registered. Deploying this code does not
activate multi-league behavior, seed League #1, create Users, change login,
rewrite IDs, alter game scoring, or change Correspondent ownership. Current
initialized databases retain their application behavior. A fresh/outdated DB now
requires explicitly authorized initialization; schema-dependent requests will
fail until that maintenance is completed. No environment toggle enables schema
migration on startup.

## Approved design corrections

1. Shared official `results` remain canonical for historical and active scoring.
   `league_week_result_snapshots` is excluded. A settlement-freeze product rule
   would require a later explicit decision. Historical recap text/context remains
   an immutable record of what was written then.
2. Membership display names must be unique **within a league** for beta. User
   display names are globally non-unique. Stage 1 adds nullable `players.name_key`
   and a partial unique `(league_id, name_key)` index. The future service will
   produce the key with Unicode NFKC normalization, trimming, and casefolding.
   The existing global `players.name` unique constraint is intentionally retained
   until a separately reviewed hardening migration; duplicate names across leagues
   are not yet enabled. No old names/keys are rewritten.
3. Deployment-inertness applies to old startup migration paths too, not just the
   new tables. No PR start command or pre-deploy hook invokes maintenance.
4. **Before League #2 Correspondent activation**, evaluate sharing the public X
   source corpus. Keep each league's classifications, Pick 'Em context, private
   sources and submission notes isolated. Preserve all existing source IDs,
   classification history, paid-page receipts and delivery markers. This PR
   neither deduplicates sources nor changes X collection.

Full source audit and corrected target contract:
[PRIVATE_BETA_FOUNDATION_REVIEW.md](PRIVATE_BETA_FOUNDATION_REVIEW.md).

## Expansion inventory and limits

New tables: schema_migrations, competitions, competition_provider_links,
football_seasons, football_season_provider_links, football_matchweeks, clubs,
club_provider_links, football_season_clubs, club_aliases, users, user_identities,
leagues, league_invitations, league_notification_recipients,
league_season_memberships, league_week_fixtures, football_refresh_jobs.

Additions affect players, seasons, weeks, fixtures, picks,
auto_draft_preferences, match_events, api_sync_states, club_season_enrichment.
All added columns are nullable with **no defaults**. Existing rows remain null;
the migration inserts exactly one ledger record and zero domain records. Its
DDL is additive only: no DROP, table rebuild, rename, UPDATE, DELETE, REPLACE, or
historical ID allocation. Existing columns/constraints/PKs/indexes/triggers/views
remain in place, including the old global name/season-code uniqueness.

New-table FKs/CHECKs and partial unique indexes are declared now. Final NOT NULL
ownership, canonical cache rekeying, removal of obsolete global uniqueness,
cross-row roster/slate enforcement, authentication, query scoping, and workflow
changes remain later stages. These empty tables are not a safe invitation to
populate League #2 manually. Existing app connections' FK behavior is unchanged;
the explicit migration and its inspection/verification connections enforce FKs.

The migration ledger stores version, DDL/contract checksum, UTC applied time,
before-schema hash and after-schema hash. These last two fields support explicit
target review and safe idempotent retry without accepting schema drift.

## Explicit command contract

Examples below are for an **approved disposable copy** only. Flags express
deliberate invocation; they do not grant production permission. Do not add these
commands to web/worker startup or deployment hooks.

```bash
python -m foundation_migrations inspect --database /absolute/path/to/disposable-copy.db
```

Inspection opens an existing file with SQLite `mode=ro` and `query_only`, starts
a consistent read transaction, verifies integrity/FKs and the required 26-table
legacy schema, and returns schema objects/columns, a schema hash, per-table row
counts and hashes of original values. It prints no data rows, recipient lists,
or secrets. An older/malformed/partially migrated schema is refused, not repaired.
An absent path is not created. Extra unrelated/reference tables are included in
preservation checks; no assumption that the database contains only ORM tables.

After reviewing that copy's report, explicitly apply:

```bash
python -m foundation_migrations apply \
  --database /absolute/path/to/disposable-copy.db \
  --expect-schema <schema_sha256-from-inspect> \
  --backup /absolute/path/to/new-unique-backup.db \
  --authorize-additive-schema
```

The command opens `mode=rw` (never creates the source), enables FKs before BEGIN,
and acquires BEGIN IMMEDIATE with a bounded two-second lock timeout. It rechecks
the expected schema while holding that reservation, rejects collisions/partial
adoption, and creates a non-overwriting backup using SQLite's backup API from a
second read connection. That avoids backing up a connection blocked by its own
write transaction. It verifies the backup's schema and every legacy value hash
before executing DDL.

DDL runs statement-by-statement in one transaction, not executescript. Original
values, keys and allocator state are checked again; integrity/FK checks must
pass. The ledger row and DDL commit atomically. Failed application rolls back
schema and ledger; the verified pre-migration backup remains for inspection.
An existing backup path is never overwritten. A matching completed migration
returns `already_applied` without creating a second backup or ledger record.
Unknown ledger history, checksum mismatch and post-apply schema drift are refused.

Actual production schema, deploy SHA, data baselines and workflow versions have
not been verified. This PR does not constitute production migration readiness.

## Test evidence

- Full regression suite: `python -m unittest discover -s tests -v` — **518 passed**
  on the final code and tests (36.7 seconds).
- Final targeted foundation suite:
  `python -m unittest discover -s tests -p test_foundation_migrations.py -v` —
  **19 passed**. Covers all 26 legacy tables with synthetic rows plus extra
  BLOB/allocator/index/trigger data; exact old-value and ID preservation; all new
  domain tables empty/all new columns null; WAL backup; retry; wrong hash; partial
  schema; foreign-key violations; unknown/checksum/drifted ledger; lock contention;
  mid-DDL failure; failure at commit; CLI explicitness; and separate legacy init.
- Startup checks cover current, older, missing and expanded databases in fresh
  subprocesses. `MetaData.create_all` and legacy migration execution are trapped
  during import/main; before/after schema/data dumps match, and the missing file
  stays absent. Existing legacy ORM reads/scoring/payouts still work after expansion.
- Brief supervisor tests include enabled-worker startup with missing, invalid,
  and valid operations stores; startup initialization is trapped and file bytes
  remain unchanged. These tests were included in the final 518-test full run.

The 40 complete multi-league isolation cases in the architecture review remain
later-stage acceptance criteria. This PR implements the Stage 1 safety subset;
it does not claim authentication or League #2 isolation is implemented.

## Rollback boundary

Until the explicit command runs, there is no database change to undo. A failed
command rolls back its transaction. After successful additive application but
before any foundation data population, the legacy-compatible app still reads
the unchanged historical columns; leave harmless additions in place rather than
running a destructive down migration. Restoring an older backup after new writes
would discard those writes and is not an automatic rollback strategy.

No production operation or full multi-league cutover has been performed.
