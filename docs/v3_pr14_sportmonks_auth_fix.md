# V3 PR14 — Sportmonks Authorization Header Fix

## Purpose

The first real Railway Live Sync Worker run reached the live-sync cycle but failed before a successful ingest. Review of current Sportmonks Football API v3 authentication documentation shows that the API expects the raw API token in the `Authorization` header, not a Bearer scheme.

Current code in `sportmonks_live.py` sends:

`Authorization: Bearer <token>`

PR14 changes this to:

`Authorization: <token>`

This is a narrow production-blocking compatibility fix only.

## Required changes

1. In `sportmonks_live.py`, change the Sportmonks request header so `Authorization` contains the raw configured token value exactly, with no `Bearer ` prefix.
2. Update the corresponding fake/mocked test expectation in `tests/test_live_matchweek.py` (and any other affected tests) from `Bearer FAKE_SECRET` to `FAKE_SECRET`.
3. Preserve the existing guarantee that the token is not placed in the URL or logged in errors.
4. Do not change endpoint paths, query parameters, normalization, bridge behavior, polling behavior, database logic, UI, or any other PR12/PR13 behavior.
5. Add a short implementation note if useful, but keep the diff minimal.

## Verification

Run:
- focused Sportmonks/live tests
- full Python test suite

No real Sportmonks requests in tests.
No production token in code or tests.

## Delivery

Implement on branch `v3/pr14-sportmonks-auth-fix`.

Commit and push for review.

Do not merge, deploy, enable the Railway worker, or change production variables.
