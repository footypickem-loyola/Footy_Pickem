# PR22 — Current-season reference synchronization

`POST /tasks/sync-football-reference` runs the existing PR20 importer for Premier
League season **28083 (2026/27)** in Flask's SQLite database. Send the existing
`X-Sync-Secret` credential and an empty body or `{}`. No season override is
accepted. GET is unavailable; a browser session grants no access. The service
uses its existing `SPORTMONKS_API_TOKEN` environment variable.

The optional cron entry point is `python trigger_football_reference_sync.py`.
It reads `FOOTBALL_REFERENCE_SYNC_URL` (the full HTTPS task endpoint) and
`SYNC_SECRET`, makes one POST with redirects disabled, and prints a sanitized
status. Failures exit 1. It neither opens the database nor calls Sportmonks.
No scheduler, Railway configuration, service, or production bootstrap is added.
The actual daily/matchday schedule remains an operational decision after review.

## Ownership, freshness, and failure behavior

An explicit invocation creates `football_reference_tasks`, keyed by provider and
season. A short `BEGIN IMMEDIATE` transaction claims a random token with a
10-minute lease. Another invocation returns `already_running` without fetching.
A crashed worker's lease can be reclaimed after expiry. No network calls occur
inside a database transaction. The existing importer fetches and validates the
entire 380-fixture snapshot, then atomically publishes it. Ownership and expiry
are checked both before writes and before commit; an expired worker rolls back.
Success releases the lease in that same transaction. Token-guarded failure
updates cannot change a replacement worker's status.

A successful task establishes a one-hour minimum refresh interval (`fresh`). A
failed attempt establishes a 15-minute cooldown, extended to a longer bounded
provider Retry-After value (maximum one hour). Repeated requests during that
interval return `cooldown`; they make no provider calls. There are no in-request
retries. These intervals and the lease duration are named constants in
`football_reference_tasks.py`.

Success and skips return HTTP 200 and machine-readable `status`. Missing token
returns 503; provider rate limiting 429; provider HTTP, malformed responses,
pagination and validation failures 502; SQLite busy/write failures 503; lost
ownership 409. Failures include an allowlisted `error_code` and `retry_after`,
also exposed as the Retry-After header. Secrets, provider bodies, URLs and raw
exceptions are never returned. Authentication failures return 403, missing task
configuration 503, and invalid request bodies 400. All POST responses are
`no-store`.

If SQLite is busy before a claim, no provider fetch occurs and the attempt
cannot be recorded. If failure recording itself is unavailable, the response
reports `failure_recorded: false`; the durable lease eventually expires.
Inspection can therefore show an expired `running` attempt after a crash.
Compare `lease_until` with current UTC time to distinguish that from a live lease.

The PR20 maintenance CLI remains an explicit operator tool without task freshness
gating. Scheduled current-season work must use this task endpoint; do not run
manual imports concurrently with automated refreshes.

## Dataset and inspection

Future, live and final fixtures use PR20's unchanged validation, normalization,
provider-ID upserts, correction and withdrawn/rescinded event reconciliation.
The importer additionally rejects a previously final fixture regressing to a
non-final state. Missing fixtures, scores on final fixtures, or event includes
also reject the entire refresh. Legitimate score/event corrections remain valid.

`python scripts/inspect_football_reference.py --db <local-copy.db>` now includes
`final_fixture_count` and `latest_attempt` (status, start/completion, lease expiry,
next attempt, last success, error code). The top-level `sync_status` and
`last_successful_sync_at` still describe the usable dataset. A failed refresh
does not mark that dataset failed. Failed first imports appear as `not_imported`
with zero counts and no successful dataset timestamp. Claim tokens are omitted.
Existing scored/event coverage counts retain their PR20 semantics; present
rescinded events are counted separately from active and withdrawn events.

## Validation recorded for this PR

- Full Python suite: **348 tests passed**; JavaScript suite: **10 tests passed**.
- Mocked tests cover bootstrap, freshness, progression, idempotency, corrections,
  rescinds/withdrawals, truncated/malformed/failed pages, 429/HTTP failures,
  missing token, busy SQLite, rollback, overlap, expiry, stale worker fencing,
  endpoint authentication, sanitized cron output, and unchanged PR21 retrieval.
- `SPORTMONKS_API_TOKEN` was unavailable. No live dry-run or live current-season
  counts are claimed. When configured, the existing read-only provider check is
  `python scripts/sync_football_reference.py --season 28083 --dry-run`.
- SQLite's backup API copied the verified local snapshot, opened with `mode=ro`
  and `query_only=ON`, to a disposable database. A **mocked** 28083 response was
  imported through the task: **380 fixtures, 1 scored, 1 final, 1 with events,
  1 current event**. These are test coverage counts, not current provider totals.
  All rows in **26 non-reference tables** were unchanged. The copy was discarded.
- Original snapshot SHA-256 before and after:
  `73c37c2cceff71f360dbf36f55ecadc1a9ad05143ed0cbf93c369794df35bef4`.

For a Chelsea–Arsenal target on 2027-02-01, unchanged PR21 retrieval initially
returned the real stored 2026-03-01 Arsenal 2–1 Chelsea meeting (season 25583).
After the disposable mocked sync it returned a **synthetic** 2026-10-01 Arsenal
1–0 Chelsea meeting (season 28083, team IDs 19/18) with one goal event. The
regression also verifies that future fixtures are excluded. This demonstrates
same-season visibility without adding detectors or presenting synthetic facts
as real football history.

No production data, original local snapshot, official game data, live-match
worker, PR21 detectors, or UI was changed.
