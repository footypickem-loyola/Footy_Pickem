# League-scoped manual pick submission (Task 27)

Enabled only with the existing default-off `RUNTIME_AUTH0_ENABLED` gate in the
actual Flask runtime. No migration, new schema, provider operation, email or
background synchronization is introduced. This source remains local/uncommitted
for review. League B still cannot enter legacy global routes.

## Contract

`POST /auth/leagues/<league_id>/seasons/<season_id>/matchweeks/<week_number>/matchups/<matchup_id>/picks`

Use HTTPS at the configured origin and the existing opaque authenticated session.
Submit `application/x-www-form-urlencoded`, at most 4096 bytes, with exactly one
of each field: `fixture_id`, `team`, `selected_club_id`, `expected_count`, `csrf`.
The `csrf` value is the session token already returned as `logout_csrf` by
`GET /auth/leagues`; the exact configured Origin header is also mandatory. Do not
send any player/user override. Query selectors, extra fields and duplicates refuse.
`expected_count` is the number of persisted picks in this matchup shown by the
Matchweek GET; refresh after any conflict. The response is 201 with `submitted`,
`matchup_id` and the existing Auto-Draft summary; all responses are no-store.

The player is resolved from verified issuer/subject, active account and selected
league membership. The engine acquires `BEGIN IMMEDIATE` before revalidating that
identity, membership and CSRF, and before reading the draft version/turn. It
checks the same league/season/week/roster/slate graph as the read-only endpoint.
The matchup must belong to that week and contain the authenticated membership.
Both participants must have active, non-departed league and season memberships.
Only active leagues and active, unarchived seasons in a drafting week accept picks.
Setup, provisional, finalized, complete or inconsistent drafts refuse.

The existing `DraftService` still determines snake order, participant ownership,
ten-pick limit, per-matchup fixture uniqueness, stale-version refusal and bounded
SQLite serialization. Three small override points supply locked authorization,
league fixture lookup and the canonical `selected_club_id` in the same transaction.
Their defaults preserve the old legacy engine behavior. Shared fixtures are
resolved through `league_week_fixtures` rather than their legacy `week_id`; another
league's pick never consumes this matchup's fixture. Team and explicit club must
agree with the canonical fixture and football-season club relationships.

A fixture must have a known future UTC kickoff and no recorded result. Missing
kickoffs fail closed. This is enforced on manual and persisted Auto-Draft picks;
the old engine had no server-side kickoff check, so legacy defaults are untouched.
There is no inferred earlier matchweek-wide deadline. Existing enabled queues run
through the same engine after a successful manual pick, with scoped fixtures and
canonical club writes. A queued fixture with an uncertain/elapsed kickoff causes
the whole transaction to refuse and roll back. No new queue-management API exists.

Success is returned only after commit. Constraint/busy failures return 409 with no
automatic retry; other failures roll back. Refresh after any uncertain HTTP result
rather than blindly replaying it. Version checking is mandatory even where snake
order gives the same player consecutive turns.

## Focused synthetic validation

After installing repository requirements, from this worktree:

```powershell
python -B -m unittest discover -s tests -p test_runtime_league_draft.py
python -B -m unittest discover -s tests -p test_runtime_league_auth.py
python -B -m unittest discover -s tests -p test_runtime_legacy_boundary.py
python -B -m unittest discover -s tests -p test_runtime_matchweek.py
```

Coverage includes snake order, duplicate/stale submissions, truly concurrent HTTP
requests for distinct and identical fixtures, foreign selectors and participants,
CSRF/origin rejection, kickoff/result and draft locks, rollback of legacy plus
foundation fields, safe persisted queues, shared fixtures, complete drafts and
actual disabled-mode legacy HTTP picks. Databases and identities are synthetic;
the fixture forbids network connections. No retained real-data artifacts are used.

## Remaining integration

The next small slice is a scoped Matchweek pick control using this contract and
server-generated turn/availability state, with refresh on conflict. League-scoped
queue editing, scoring/standings views, administration and background writers
remain separate work. Production identity approvals, auth-store provisioning,
Auth0/HTTPS configuration, Stage 2 fencing and writer coordination remain unmet
production prerequisites. No deployment or feature enablement is authorized here.

## Task 28 focused security/concurrency review

No material finding in the scoped endpoint. Authorization resolves the exact verified
identity and active membership inside `BEGIN IMMEDIATE`; matchup, seasonal roster,
slate and canonical relationships are checked before insertion. No request player
selector is accepted. Revocation committed while a request waits for the SQLite
writer lock is observed after lock acquisition and refuses without a pick.
Session CSRF plus exact Origin, strict single-valued fields, mandatory draft count,
per-matchup uniqueness and rollback remain enforced. Concurrent identical or distinct
fixture submissions cannot consume the same turn, including consecutive snake turns.
Shared fixtures remain isolated by matchup; kickoff and result locks also apply to
persisted queues. Three additional synthetic review regressions cover within-league
foreign matchups, duplicate/missing form fields and revocation while waiting.
