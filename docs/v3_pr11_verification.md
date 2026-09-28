# PR 11 implementation and verification

Implemented locally on `v3/pr11-auto-draft`, using `v3_pr11_auto_draft.md` as the contract.

## Persistence and migration

- `AutoDraftSetting` / `auto_draft_settings`: matchup, player, explicit enabled flag; unique matchup/player pair. No row means off.
- `AutoDraftPreference` / `auto_draft_preferences`: matchup, player, fixture, team, integer priority; unique matchup/player/fixture. Ordering uses priority then primary key.
- The existing `ensure_database_schema()` calls `Base.metadata.create_all()`, which adds the two tables and their constraints. The bulk-picks follow-up also adds nullable `auto_draft_settings.confirmed_at` and `fixtures.venue` columns to existing SQLite tables, with backups before each alteration and column checks to make reruns idempotent. Existing queues remain unconfirmed. There is no backfill or table rebuild; fixtures without venue data display `Venue TBD`.
- Tests start from the previous schema with existing picks, create the new tables, rerun migration, and verify preserved data. A separate Python process verifies that preferences are stored on disk.

## Authoritative writes

`auto_draft.DraftService.command()` is the shared entry point for manual picks and queue mutations. It starts a fresh SQLAlchemy session and executes SQLite `BEGIN IMMEDIATE` before loading matchup, participant, season, week, picks, settings, or preferences.

Within that transaction it validates read-only state, participant identity, current snake turn, fixture availability, and selected team. Both automatic and manual picks use `_pick()`, which reuses `compute_next_turn()`. The existing unique matchup/fixture constraint remains a second safeguard. Queue edits and all picks caused by the command commit together or roll back together.

Manual POSTs now require `expected_count`, provided by both existing manual UIs. The count is checked under the write lock, rejecting stale submissions even during B,B / A,A turns. An old open page or external caller missing this field must refresh/update before submitting.

SQLite's existing default five-second busy timeout bounds lock waiting. Lock/constraint conflicts roll back and return HTTP 409 with a refresh instruction. There is no blind retry after a possible commit, background worker, or new polling. Automatic execution stops on a disabled/empty/invalid queue or ten picks; it reevaluates each next turn, including another enabled player's queue. It never chooses a fallback.

Only successful manual POSTs and Auto-Draft edit/toggle POSTs invoke execution. GETs, refreshes, and history restoration are read-only. HTMX replaces Matchweek with the resulting state; normal successful POSTs use a 303 redirect. Manual Arsenal banter remains restricted to successful manual HTMX responses.

## Verification — September 27, 2026

- Full Python suite: **185 passed**, using `python -m unittest discover -s tests -v`.
- Focused run: **27 passed**, using `python -m unittest discover -s tests -p test_app.py -k test_auto`.
- JavaScript suites: **10 passed**, using `node --test tests/pick_confirm.test.cjs tests/bulk_picks.test.cjs`.
- Concurrency coverage uses independent connections and synchronized threads: competing manual requests for a consecutive snake slot; manual versus duplicate automatic execution; two players requesting the same fixture. A held write lock verifies bounded conflict handling with no partial preference write.
- Coverage also includes CRUD/reorder, invalid teams/Draw, duplicate preferences, ownership, archived/finalized/completed state, persistence, migration, skipped preferences, no fallback, consecutive turns, ten-pick completion, stale versions, GET safety, HTMX responses, and POST/redirect/GET.
- `git diff --check` passes.

Browser verification used the Codex browser against `127.0.0.1:5111`, backed by a newly generated temporary SQLite database. `python scripts/review_auto_draft.py` reproduces the synthetic review setup; it never opens or copies an existing application database.

- **1440px:** partial queue, reorder, explicit on/off, highest-priority automatic pick, manual confirmation and pick with Auto-Draft off, unavailable display after the opponent takes a queued fixture, skipped first preference followed by B,B picks, exhausted queue, and completed Matchup Set layout.
- **390px:** remove/add/reorder, toggle, waiting state, collapsible section, persisted exhausted state, and no horizontal overflow.
- **360px:** manual confirmation/cancel controls, reorder, enable, automatic picks nine and ten, five fixtures per player, refreshed result, and no horizontal overflow.
- Direct Matchweek URLs, HTMX updates, refresh, back/forward without replayed picks, and archived read-only controls were checked.

No production database was used or migrated. No merge or deployment was performed. Pre-existing untracked files were preserved.

## Bulk picks follow-up

The Matchweek draft now exposes `Set Bulk Picks` in the right side of the `YOUR TURN` banner. The modal uses a two-column desktop editor (fixtures and numbered priorities), collapses into a vertically stacked editor on mobile, and keeps all choices client-side until the player submits the list for review. The review screen shows every fixture and selected team before the final confirmation. Confirmation atomically replaces the player's priorities, enables Auto-Draft, and records `confirmed_at`; subsequent add/remove/reorder/replace actions are rejected while the list remains viewable. Turning Auto-Draft off pauses execution without unlocking the confirmed list. A stale draft version returns HTTP 409 before anything is saved.

The bulk editor requires every remaining fixture exactly once and rejects Draw, invalid teams, duplicate fixtures, and incomplete lists. If drafting has started, it ranks every remaining fixture. The browser review covered desktop placement and confirmation, team selection, moving/clearing slots, canceling unsaved work, locked-list viewing, Auto-Draft pause, and stale confirmation after another request advanced the draft at 390px. The final filter follow-up was checked at desktop, 390px, and 360px against a fresh temporary database on port 5115: placing a fixture immediately hides it under Show Available Fixtures even before team selection; Show All Fixtures crosses out assigned fixtures and displays their priority; clearing a slot restores availability. Cancel/reopen restores the unsaved editor and the default filter. No picks were confirmed in Week 1 of that clean review app.

Fixture cards, priorities, and confirmation show home/away and venue. Bulk action labels use title case. Drafted priority status distinguishes the player's picked team from Opponent Picked. JavaScript regressions cover immediate filtering, restoring displaced/cleared fixtures, and requiring only undrafted fixtures mid-draft. Python regressions cover atomic confirmation, locked-list enforcement with on/off still allowed, stale/concurrent confirmations, invalid payloads, read-only states, and migration preserving existing unconfirmed settings.

## Season-reset follow-up

`_delete_season_weeks()` now deletes `AutoDraftPreference`, then `AutoDraftSetting` rows scoped to the season's matchup IDs before deleting matchups or fixtures. The existing explicit `allow_reset=True` requirement is unchanged.

The regression test persists enabled settings and preferences for all six participants, runs the allowed reset-and-recreate entry point, and verifies that both tables are cleared for the reset season even when SQLite reuses the same matchup IDs and player scopes. It also verifies that another season's state remains intact and that automatic evaluation of a recreated matchup makes no inherited pick. Before the fix, the test failed with six stale preferences remaining. The test counts above include this follow-up; browser verification remains the original UI verification because this change only affects lifecycle cleanup. All follow-up execution used isolated temporary databases.
