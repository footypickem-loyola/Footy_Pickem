# PR13 implementation and verification

Implemented on `v3/pr13-live-sync-bridge` against `v3_pr13_live_sync_bridge.md`.

## Transport boundary

`SportmonksClient` still normalizes provider responses. The dedicated worker now sends those normalized snapshots through `LiveIngestClient` to Flask's `POST /tasks/live-ingest`. The worker imports neither Flask nor database modules, requires no `DB_PATH`, and retains no durable state. Flask alone opens the ingest transaction and invokes the existing `live_sync.ingest()`.

The endpoint uses a dedicated `X-Live-Ingest-Secret` header and constant-time comparison with `LIVE_INGEST_SECRET`. It rejects missing configuration, bad authentication, non-JSON requests, malformed fields, unsupported providers/leagues, duplicate identities, and oversized snapshots before opening a database session. The wire envelope contains `provider`, `league_id`, and `fixtures`; fixture/event fields are the normalized PR12 fields only. This Premier League bridge accepts Sportmonks league 8. Limits are 1 MiB, 100 fixtures, and 500 events per fixture, with bounded strings and numbers.

Each accepted snapshot is one transaction, including rollback on failure. `events: null` preserves existing events; `events: []` reconciles withdrawals. Event revisions, idempotency, mapping, scorer/minute/score progression, and Correspondent factual context continue through the existing implementation. No schema, scoring, UI, official Result, Week finalization, or football-data.org sync code was changed.

Transport has a ten-second timeout, disables redirects to protect the shared secret, and validates the acknowledgement before reporting success. Errors omit credentials, URLs, and payloads. Live/halftime/break snapshots use the default 15-second cadence; empty/non-live snapshots use 60 seconds. Configured cadence and failure/rate-limit backoff are bounded to at most 3,600 seconds. Failed sends leave existing Flask data intact; later cycles send fresh snapshots.

## Configuration for future provisioning

- Worker: `LIVE_SYNC_ENABLED=1`, `LIVE_INGEST_URL`, `LIVE_INGEST_SECRET`, `SPORTMONKS_API_TOKEN`, and `SPORTMONKS_LEAGUE_ID=8`. Optional `LIVE_POLL_SECONDS` and `LIVE_NEAR_SECONDS` default to 15 and 60 (minimum 15).
- Flask: the same dedicated `LIVE_INGEST_SECRET`, plus its existing database configuration. The Sportmonks token belongs on the worker.
- Start one worker with `python live_sync_worker.py`. It remains disabled by default. Use a Railway private-network URL when provisioned later; authentication is required. The ingest URL must not contain credentials, query parameters, or fragments. No shared volume or worker database is needed; `LIVE_IDLE_SECONDS` no longer controls cadence.

No production variables, Railway services, deployment, or merge were performed. Unrelated untracked files were preserved.

## Verification

- Focused bridge/live suite: **28 tests passed** using `.venv/Scripts/python.exe -W ignore::DeprecationWarning -m unittest discover -s tests -p 'test_live*.py' -v`.
- Full Python suite: **214 tests passed** using `.venv/Scripts/python.exe -W ignore::DeprecationWarning -m unittest discover -s tests -p 'test_*.py'`.
- Coverage includes authenticated fake worker-to-Flask round trips, exact secret header, normalized-only payloads, rejection before database access, atomic rollback, repeated snapshots, corrections/withdrawals/reactivation, official/manual precedence, finalized weeks, failure backoff, safe errors, and enabled worker startup in a fresh interpreter that blocks database/application imports with no `DB_PATH` or created files.
- Tests used isolated temporary databases and fake HTTP transports; no real Sportmonks requests or production tokens/data were used.
- Browser and JavaScript suites were not rerun: no player-facing markup, styling, or JavaScript changed; the contract makes the PR12 browser matrix optional for this bridge-only change. Existing Python rendering/scoring coverage passed.
