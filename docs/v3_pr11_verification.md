# PR 11 implementation and verification

Implemented locally on `v3/pr11-auto-draft`, using `v3_pr11_auto_draft.md` as the contract.

## Persistence and migration

- `AutoDraftSetting` / `auto_draft_settings`: matchup, player, explicit enabled flag; unique matchup/player pair. No row means off.
- `AutoDraftPreference` / `auto_draft_preferences`: matchup, player, fixture, team, integer priority; unique matchup/player/fixture. Ordering uses priority then primary key.
- The existing `ensure_database_schema()` calls `Base.metadata.create_all()`, which adds the two tables and their constraints. No new alteration, backfill, table rebuild, or data rewrite is needed. Existing migration behavior is unchanged.
- Tests start from the previous schema with existing picks, create the new tables, rerun migration, and verify preserved data. A separate Python process verifies that preferences are stored on disk.

## Authoritative writes

`auto_draft.DraftService.command()` is the shared entry point for manual picks and queue mutations. It starts a fresh SQLAlchemy session and executes SQLite `BEGIN IMMEDIATE` before loading matchup, participant, season, week, picks, settings, or preferences.

Within that transaction it validates read-only state, participant identity, current snake turn, fixture availability, and selected team. Both automatic and manual picks use `_pick()`, which reuses `compute_next_turn()`. The existing unique matchup/fixture constraint remains a second safeguard. Queue edits and all picks caused by the command commit together or roll back together.

Manual POSTs now require `expected_count`, provided by both existing manual UIs. The count is checked under the write lock, rejecting stale submissions even during B,B / A,A turns. An old open page or external caller missing this field must refresh/update before submitting.

SQLite's existing default five-second busy timeout bounds lock waiting. Lock/constraint conflicts roll back and return HTTP 409 with a refresh instruction. There is no blind retry after a possible commit, background worker, or new polling. Automatic execution stops on a disabled/empty/invalid queue or ten picks; it reevaluates each next turn, including another enabled player's queue. It never chooses a fallback.

Only successful manual POSTs and Auto-Draft edit/toggle POSTs invoke execution. GETs, refreshes, and history restoration are read-only. HTMX replaces Matchweek with the resulting state; normal successful POSTs use a 303 redirect. Manual Arsenal banter remains restricted to successful manual HTMX responses.

## Verification — September 27, 2026

- Full Python suite: **177 passed**, using `python -m unittest discover -s tests -v`.
- Focused run: **19 passed**, using `python -m unittest discover -s tests -p test_app.py -k test_auto` (15 new Auto-Draft cases plus four existing matching cases).
- JavaScript confirmation suite: **3 passed**, using `node --test tests/pick_confirm.test.cjs`.
- Concurrency coverage uses independent connections and synchronized threads: competing manual requests for a consecutive snake slot; manual versus duplicate automatic execution; two players requesting the same fixture. A held write lock verifies bounded conflict handling with no partial preference write.
- Coverage also includes CRUD/reorder, invalid teams/Draw, duplicate preferences, ownership, archived/finalized/completed state, persistence, migration, skipped preferences, no fallback, consecutive turns, ten-pick completion, stale versions, GET safety, HTMX responses, and POST/redirect/GET.
- `git diff --check` passes.

Browser verification used the Codex browser against `127.0.0.1:5111`, backed by a newly generated temporary SQLite database. `python scripts/review_auto_draft.py` reproduces the synthetic review setup; it never opens or copies an existing application database.

- **1440px:** partial queue, reorder, explicit on/off, highest-priority automatic pick, manual confirmation and pick with Auto-Draft off, unavailable display after the opponent takes a queued fixture, skipped first preference followed by B,B picks, exhausted queue, and completed Matchup Set layout.
- **390px:** remove/add/reorder, toggle, waiting state, collapsible section, persisted exhausted state, and no horizontal overflow.
- **360px:** manual confirmation/cancel controls, reorder, enable, automatic picks nine and ten, five fixtures per player, refreshed result, and no horizontal overflow.
- Direct Matchweek URLs, HTMX updates, refresh, back/forward without replayed picks, and archived read-only controls were checked.

No production database was used or migrated. No merge, push, or deployment was performed. Pre-existing untracked files were preserved.
