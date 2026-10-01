# V3 PR13 — Live Sync Railway Bridge

## Purpose

PR12 added Sportmonks live ingestion, normalized live/event storage, provisional Pick ’Em scoring, and the Live Matchweek UI. Its dedicated worker was originally designed to open the same SQLite database as the Flask service.

Production Railway uses SQLite on the Flask service volume mounted at `/data`. Railway volumes are service-bound, so a separate worker service cannot directly open the Flask service’s SQLite file.

PR13 changes only the transport boundary:

**Sportmonks → stateless Live Sync Worker → authenticated Flask ingest endpoint → existing SQLite database**

Flask remains the sole service that reads/writes the production SQLite file. The live worker does not own persistent storage.

## Goals

- Preserve all PR12 live data models, scoring, UI, event reconciliation, Correspondent context, and official-result precedence.
- Make the live worker runnable as a separate Railway service without access to the Flask volume.
- Keep the Sportmonks API token server-side on the worker.
- Keep SQLite as the production database for now.
- Do not introduce Postgres in this PR.
- Do not change the player-facing Live Matchweek UI.

## Required architecture

### 1. Stateless live worker

Refactor `live_sync_worker.py` so it no longer:
- imports the Flask application to access `SessionLocal`
- opens SQLite
- requires `DB_PATH`
- writes `LiveFixtureState`, `MatchEvent`, or `FixtureProviderLink` directly

The worker should:
1. call Sportmonks through the existing `SportmonksClient`
2. normalize the response using the existing provider boundary
3. serialize the normalized fixture snapshot
4. POST it to the Flask bridge endpoint
5. sleep according to configured cadence/backoff
6. retain no persistent local state

In-memory values for the current loop/cadence are fine; “stateless” means no durable local database or filesystem state.

### 2. Flask ingest endpoint

Add a server-only POST endpoint, suggested path:

`POST /tasks/live-ingest`

Requirements:
- authenticate with a dedicated secret header, suggested `X-Live-Ingest-Secret`
- secret comes from `LIVE_INGEST_SECRET`
- reject missing/incorrect secret
- accept only normalized PR12 fixture data; do not accept arbitrary/raw Sportmonks payloads
- validate provider/league/required field types and bound the payload size
- reconstruct normalized live fixture objects or an equivalent validated internal structure
- call the existing `live_sync.ingest()` inside one database transaction
- return a small JSON acknowledgement such as stored fixture count
- never write official `Result` or `Week` state
- preserve manual/official result authority
- never log the shared secret or full incoming payload

The endpoint is an internal task endpoint, not a player/browser API.

### 3. Worker → Flask bridge

Add configuration:
- `LIVE_INGEST_URL` — required by the worker
- `LIVE_INGEST_SECRET` — required by both worker and Flask
- keep `SPORTMONKS_API_TOKEN` on the worker
- keep `SPORTMONKS_LEAGUE_ID` on the worker

Use Railway private networking for the production URL when provisioned later. Authentication is still required even on private networking.

The worker should use bounded request timeouts and safe errors. A failed POST must not be treated as a successful ingest.

### 4. Polling behavior

Do not require direct DB access merely to decide whether to poll.

Keep cadence configuration-driven. A simple acceptable policy is:
- live fixtures returned → `LIVE_POLL_SECONDS` (default 15)
- non-live/empty response → `LIVE_NEAR_SECONDS` or an equivalent modest interval (default 60)
- provider/bridge error → bounded backoff

Do not create per-user polling and do not put Sportmonks calls in browser requests.

If the implementation can preserve the prior near/idle distinction without adding unnecessary complexity, that is fine, but DB sharing must not return.

### 5. Serialization boundary

The bridge payload must contain normalized internal fields only, sufficient for the existing `live_sync.ingest()` behavior:
- external fixture ID
- league ID
- home/away team
- kickoff
- normalized state
- home/away score
- minute/extra minute
- provider updated timestamp
- normalized events and their existing fields

Do not forward raw Sportmonks response bodies.

### 6. Existing PR12 behavior that must remain unchanged

- `fixture_provider_links`
- `live_fixture_states`
- `match_events`
- deterministic fixture mapping
- event idempotency/corrections/withdrawals
- score never inferred solely by counting events
- official Result/manual result precedence
- provisional For/Against/Net and payout calculations
- Matchweek Draft → Pre-Match → Live → Final behavior
- stale-state handling
- player-facing Live Matchweek UI
- no player-facing timeline
- `factual_context()` / Correspondent event-query layer
- football-data.org final-result/finalization path

## Security / safety

- Dedicated live-ingest secret, separate from existing sync/correspondent secrets.
- Constant-time secret comparison if consistent with existing task endpoint conventions.
- No secret in URL/query string.
- No raw provider payloads or secrets in logs/errors.
- Reject malformed or oversized snapshots before DB mutation.
- One transaction per accepted snapshot; malformed data must not partially ingest.
- No production configuration or Railway service creation in this PR.

## Tests

Add focused regression coverage for at least:

1. valid authenticated bridge request persists normalized live state/events
2. missing/wrong secret is rejected and writes nothing
3. malformed fixture/event payload is rejected atomically
4. repeated identical bridge request remains idempotent
5. event correction/withdrawal still works through the bridge
6. official/manual Result remains authoritative
7. worker requires no `DB_PATH` and does not open/import the app database path
8. worker sends exact live-ingest secret header
9. worker sends normalized data rather than raw provider response
10. provider failure and bridge failure back off safely
11. no real Sportmonks or production network calls in tests
12. existing PR12 and full Python suites continue to pass

Browser/UI changes should not be necessary. If no player-facing markup changes, rerunning the full PR12 browser matrix is optional; explain what was run.

## Non-goals

- PostgreSQL migration
- shared Railway volume hacks
- WebSockets/SSE
- player/browser calls to Sportmonks
- changes to Live Matchweek design
- changes to official result/finalization semantics
- changes to Correspondent prompts
- What to Look For / H2H analytics
- production Railway service creation
- production token/secret configuration

## Delivery

Implement on branch `v3/pr13-live-sync-bridge`.

Keep the diff narrow. Update the implementation/report documentation as needed.

Run focused bridge/live tests and the full Python suite.

Do not merge, deploy, create Railway services, or change production variables. Commit and push the implementation for review.
