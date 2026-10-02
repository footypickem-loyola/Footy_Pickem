# PR16 — cached Pick Insight season enrichment

Base: current `main` after PR15, `2460d5806b5593b9c28650a548bfd8b7b70f5e18`.
Branch: `v3/pr16-pick-insight-enrichment`.

Pick Insight now uses a centrally stored club crest URL and current PL club
goal leader(s), when a valid season cache exists. The original five performance
metrics still use only football-data.org / official `Result` records and their
existing pre-fixture cutoff. Scorer totals describe the current synchronized
season, not the pre-fixture historical cutoff or an immutable pick-time snapshot.
No `MatchEvent` data enters enrichment.

## Read-only verification before implementation

On 2026-10-02 at 14:36:24 UTC (10:36:24 AM EDT), the production home page identified
the active season as `2026–27`, code `year-2`. Read-only GETs of
`https://footypickem-production.up.railway.app/partials/fixtures/{week}?season=year-2`
for all 38 weeks returned 380 fixtures and exactly the 20 stored club strings below.
The inspected route renders `Fixture.home` / `Fixture.away` directly; these are
not reconstructed football-data.org display names. No login, POST, sync, database
write, configuration change, or production migration was performed.

Provider identity evidence is the user-supplied sanitized season export,
`tests/fixtures/sportmonks-season-28083-sanitized.json`, declaring league 8 and
season 28083. SHA-256 of the supplied file:
`2fdd34a396b414822dc6c4c9363d0700e4f94c9aa86f0c576dde99b7747be620`.
It contains 20 participant records with stable IDs and crest URLs plus one scorer
example. It is NOT a complete scorer snapshot and is never installed as cache data.

The committed mapping is `data/sportmonks_2026_27_mapping.json`. Each line below
is Sportmonks team ID → exact Footy stored club (provider name in parentheses
where different):

- 3 → Sunderland
- 6 → Tottenham (Tottenham Hotspur)
- 8 → Liverpool
- 9 → Man City (Manchester City)
- 11 → Fulham
- 13 → Everton
- 14 → Man United (Manchester United)
- 15 → Aston Villa
- 18 → Chelsea
- 19 → Arsenal
- 20 → Newcastle (Newcastle United)
- 22 → Hull City
- 51 → Crystal Palace
- 52 → Bournemouth (AFC Bournemouth)
- 63 → Nottingham (Nottingham Forest)
- 71 → Leeds United
- 78 → Brighton Hove (Brighton & Hove Albion)
- 116 → Ipswich Town
- 117 → Coventry City
- 236 → Brentford

All 20 IDs, all 20 stored strings, and all 20 participant entries are unique.
The stored-club set exactly equals the production set. Provider names are audit
labels only. Runtime scorer association uses `participant_id`; runtime cache
lookup uses the explicit reverse mapping. No fuzzy matching, aliases inferred
from memory, or matching through the included participant name is used.

## Storage and synchronization

One additive table, `club_season_enrichment`, uses composite primary key
`(season_id, sportmonks_team_id)` and uniqueness on `(season_id, footy_club)`.
`season_id` references the local season. Other columns are the exact stored club,
Sportmonks season ID, league ID, nullable validated crest URL, nullable integer
goal total, a JSON list of tied leaders (`player_id`, `name`), and UTC `synced_at`.
The existing schema initializer creates the new table; no existing table is
rebuilt or existing result/pick data migrated. No provider payloads or secrets
are stored. The supplied sample is test evidence only.

The supported production entry point is `POST /tasks/sync-pick-insight` inside
the Flask service that owns the SQLite `/data` volume. It uses the existing
`SYNC_SECRET` / `X-Sync-Secret` pattern and constant-time comparison; an unset
secret fails closed (503), a missing/wrong header returns 403, and GET is not
supported. Do not put secrets in query strings. The Flask process needs its
own `SPORTMONKS_API_TOKEN`; the worker's environment does not supply it.
No secrets or Railway settings have been set by this PR.

An empty body or `{}` consumes the oldest pending task for the active season.
For manual/admin retry, send `{"week_id": <local Week.id>}`. This retries a
pending/failed task or recovers a missed enqueue for an already fully finalized
week. Completed tasks are no-ops even with an explicit ID; no force-refresh
flag is exposed. A successful response contains only `ok`, `status`, `clubs`,
and `clubs_with_scorers`. Status is `completed`, `already_completed`, or
`no_work`. Busy/invalid targets return 409; provider failures return a generic
502. No exception text, provider payload, DB path, or credential is returned.

The endpoint delegates to the existing `sync_season(...)`. It refuses
non-active/archived seasons and any season other than `year-2` / `PL` / 2026.
It requires exact agreement between
the database club set, the verified mapping, and the full participant ID set.
It fetches and validates both full collections before atomically updating all
20 rows. Missing scorer/crest data clears that field on a successful snapshot;
HTTP errors, incomplete pages, and mapping failures retain the prior cache and
its original timestamp. Unknown scorer IDs are ignored; participant-set drift
rejects the entire synchronization.

**Primary: once after each fully finalized matchweek.** Optional manual/admin
retry covers missed or failed refreshes. There is no hourly Sportmonks refresh.

The clean transition point is `_set_week_status_without_commit`, shared by
`sync_results_from_api` (official API import) and `update_week_status` (manual
result entry/corrections). When status changes to finalized, it records one
`pick_insight_refreshes` row keyed by local `week_id`, in the same database
transaction. A rollback therefore rolls back the task too. Repeated status
updates and re-finalizing a corrected week cannot create duplicate tasks.
No provider client, network operation, or background thread runs here.

The additive task table stores season ID, pending/running/completed status,
created/completed timestamps, and a claim token/lease. A short atomic claim
serializes all tasks in the season, then releases the write transaction before
provider I/O. Completion checks the claim token and unexpired ten-minute lease.
Cache writes and completion share one transaction; a late/expired worker cannot
publish its snapshot. Failure retains the old cache and releases the task for
retry. A killed process is recoverable after its lease expires. Provider reads
may repeat after a crash/failure, but there is one successful completion per
week. Finalization has already committed and never depends on Sportmonks.

The existing stateless `trigger_score_sync.py` now supports dispatch through
explicit optional `PICK_INSIGHT_SYNC_URL`. When unset/blank, existing score-only
behavior is unchanged. After `/tasks/sync-results` succeeds, it POSTs the
configured Flask enrichment endpoint using the same `SYNC_SECRET` and timeout.
Score-sync failure skips dispatch. `completed`, `no_work`, and
`already_completed` are successful outcomes. Dispatch failure logs a generic
warning without payloads, URLs, or secrets and preserves score-sync exit code
zero. Pending work can retry on the next invocation; finalization is unaffected.

The cron can check every five minutes; only a claimed pending finalized-week
task causes Flask to fetch Sportmonks. The cron never contacts Sportmonks and
holds no database. Browser/page requests never consume tasks. The new URL has
not been configured: wire it separately after merge/deployment. No Railway
configuration, cron, secret, deployment, or production execution was performed.

`python scripts/sync_pick_insight.py` remains a direct manual recovery utility
requiring `DB_PATH` and `SPORTMONKS_API_TOKEN` on the volume-owning service.
It does not mark a week task complete; prefer the endpoint for normal operation
and task retries. Do not run a separate worker against an ephemeral SQLite DB.

## Provider contract and entitlement

League `8`, season `28083`, `2026/2027`, goal-topscorer type `208`:

- `GET https://api.sportmonks.com/v3/football/teams/seasons/28083`
- `GET https://api.sportmonks.com/v3/football/topscorers/seasons/28083?include=player;participant;type&filters=seasonTopscorerTypes:208`

Both requests add `page=N&per_page=50`, validate `pagination.current_page` and
boolean `has_more`, and continue until `has_more=false`. An absent `pagination`
key indicates a complete response (as observed for the 20-team endpoint).
When pagination is present, malformed pagination,
repeated/non-progressing pages, empty intermediate pages, oversized responses,
or the 100-page bound fail closed. Page URLs are constructed locally; supplied
`next_page` URLs are never followed. Authentication is the raw token in the
`Authorization` header, never a query parameter. Redirects are rejected.
Errors do not echo provider bodies, URLs, or credentials.

References: [teams by season](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/teams/get-teams-by-season-id),
[topscorers by season](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/topscorers/get-topscorers-by-season-id),
and [Sportmonks participant/player include example](https://www.sportmonks.com/blogs/exploring-topscorers-with-sportmonks-football-api-and-crystal/).

Sportmonks access was verified during the current Starter trial through
**October 16, 2026**, as confirmed by the user. **Post-trial entitlement remains
unverified.** The token needs EPL 2026/27, both endpoints, and the
player/participant/type includes. No real provider request was made in this
implementation session. Confirm continued access and quota during the
separately approved activation and after the trial; HTTP 401/403/429 and other
failures retain the prior cache and allow retry without affecting finalization.

## Selection and fallbacks

For each mapped ID, choose the highest nonnegative integer `total` among type
208 records for season 28083, with matching included player/participant IDs and
a usable player name. Prefer display name, then common name, then name. Do not
sum records or use global leaderboard position. Exact duplicate player entries
deduplicate; conflicting duplicates or invalid candidates invalidate that
club's scorer field rather than publishing a potentially understated leader.
All equal positive leaders are displayed in stable player-ID order with “each.”
No goals or no valid records produces `—`, not an invented scorer or zero.

Read-time data must match the local season, provider season, league, team ID,
and stored string. Missing, older-than-35-day, future-dated, or unmapped rows
return the original name/empty-crest and scorer `—` fallbacks. Invalid scorer
data can still leave a valid crest, and vice versa. Crest URLs must be HTTPS
Sportmonks static team CDN PNGs for that exact ID, without query credentials.
The browser loads only that cached static image URL; it never calls the
Sportmonks API. A broken image hides while the club name remains visible.
The small `[hidden]` CSS rule fixes the existing `display:block` override so
the already-existing image-error handler actually hides the broken crest.
The 35-day safety limit replaces the original 24-hour limit so a weekly snapshot
remains usable during the next draft cycle and international breaks. Failed
refreshes retain the prior snapshot, subject to this limit; no automatic refresh
is triggered by age or a page read.

Opening any manual/bulk/owned-pick recap reads local data only; no provider
client is imported on this path. Five existing metric calculations, draft
logic, scoring, Auto-Draft, Bulk Picks, Live Matchweek, and Correspondent remain
unchanged. The original checkout and its unrelated untracked files (including
`codebase_overview.txt`) were preserved by using a separate clone.

## Validation

- Full Python suite: 262 passed, including seven final trigger-script cases
  for no-work/already-completed, completed, non-fatal dispatch failure, score
  failure skipping dispatch, unset URL, authenticated Flask transport/safe
  output, and malformed summaries. Also includes nine task cases covering
  auth/validation, success/idempotency, failure/cache retention/retry, atomic
  enqueue/rollback, missed-transition recovery, lease recovery, late-worker
  fencing, season-wide serialization, and incomplete/wrong-season rejection.
  Includes 19 isolated enrichment tests and an authorized HTTP test with
  provider methods patched to fail if called. A 14-day cache remains readable;
  data older than 35 days safely falls back.
- Bulk Picks Node suite: 7 passed.
- Enrichment browser: 12 cases across desktop, 390px/360px mobile, and landscape;
  fresh tied leaders, rendered crest, broken-image fallback, stale fallback,
  no horizontal overflow, reachable scorer band, unchanged official record,
  and no browser provider API requests. CDN images were intercepted with
  synthetic success/failure responses; no real provider assets were requested.
- Existing PR15 browser: all 12 layout checks and manual persistence,
  consecutive turns, bulk confirmation/recap, navigation, focus, Escape,
  error recovery, no replay, and no browser provider calls passed.
- Existing Live Matchweek browser: all 30 viewport/scenario checks plus HTMX
  refresh, reload, Back, scroll, and automatic pre-match/live/final transitions
  passed. All browser data used isolated temporary synthetic databases.
- Initial new endpoint test failed because request teardown detached its
  fixture object; reattaching the test object fixed the stale-cache test.
- Initial browser rerun could not load the existing HTMX CDN dependency after
  the session network grant expired; rerun with network access restored.

No production sync, merge, deployment, or production configuration change.
