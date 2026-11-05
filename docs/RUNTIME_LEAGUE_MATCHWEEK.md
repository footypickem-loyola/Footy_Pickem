# Read-only league Matchweek in the real Flask runtime

Feature-gated by the existing `RUNTIME_AUTH0_ENABLED` setting; disabled mode
registers no new route and leaves Loyola's unmigrated room-code behavior intact.

`GET /auth/leagues/<league_id>/seasons/<season_id>/matchweeks/<week_number>`
returns JSON with league, viewer membership, season, week, ordered fixture slate,
shared fixture results, matchups, roster player IDs/names and picks. This is a
read-only projection, without scoring changes or league switching UI. Task 27 adds
a separate authenticated POST; see `RUNTIME_LEAGUE_DRAFT.md`.

The existing Auth0 blueprint requires its configured HTTPS origin and server-side
opaque session. Exact verified issuer/subject must resolve to an active user and
active selected-league membership with no departure timestamp. Supplied IDs grant
no authority. Unauthorized league/season/week selectors return 404; missing auth
returns 401 and disabled/unknown accounts return 403. Extra query/body selectors
are rejected. Responses are private/no-store. POST is unsupported.

One SQLite read transaction covers authorization and projection. Season belongs
to the selected league; week belongs to that season. Canonical football season
and matchweek links must agree. Fixtures come through `league_week_fixtures`, not
the legacy `fixtures.week_id`: two leagues may share the same football fixture.
Matchup players must belong to the selected league's seasonal roster; picks must
belong to that matchup's players and that week's explicit fixture slate. Canonical
club/season relationships are checked. Inconsistent relationships return 409.
Historical roster participants remain visible even after leaving; current viewer
access still requires an active membership. Legacy null selected-club links are
preserved rather than inferred. Results are shared canonical fixture outcomes.

The central route inventory explicitly classifies this one handler as league-read.
Legacy globally scoped routes remain Loyola-only under their existing guard; B
membership does not open those routes. No schema, migration, provider, production
configuration or email behavior changes. Foundation-aware writes, scoped admin,
UI navigation and league-aware background work remain separate integration work.

## Operator-assisted verified identity evidence

For six existing players, use an authorized operator's existing Auth0 Dashboard,
User Management > Users > selected profile. Auth0 documents profile inspection at
https://auth0.com/docs/manage-users/user-accounts/user-profiles and confirms the
top-level profile `user_id` is the OIDC `sub` at
https://support.auth0.com/center/s/article/User-ID-listed-as-SUB .

In a separately authorized session, record the exact top-level primary `user_id`
including its prefix, not a secondary connection's identity ID. Obtain the exact
issuer from the reviewed runtime/OIDC tenant configuration, including any custom
domain and trailing slash; do not derive it from a dashboard hostname. Confirm
that the account is the intended primary account when identities are linked.
An administrative profile establishes the subject, but names/emails alone do not
establish who controls it or authorize linking. The owner must independently
confirm account control and explicitly approve its association with the existing
membership/user IDs. Retain that evidence and the private linking manifest outside
Git in approved encrypted, access-restricted storage. Never collect tokens, cookies
or client secrets for this purpose. No bulk profile export or new Management API
automation is necessary. If an account does not exist or control cannot be verified,
linking remains blocked pending separately authorized onboarding/verification.

Use the existing `LOYOLA_IDENTITY_LINKING.md` review/apply process only under
separate approval. This task neither obtained real identities nor linked accounts.
Verified account email, recap eligibility and notification opt-outs are separate
requirements; this evidence does not enable recap delivery.

## Focused verification

Run only these synthetic test modules after installing repository requirements:

```powershell
python -B -m unittest discover -s tests -p test_runtime_league_auth.py
python -B -m unittest discover -s tests -p test_runtime_legacy_boundary.py
python -B -m unittest discover -s tests -p test_runtime_matchweek.py
```

The Matchweek cases exercise both leagues, canonical fixture sharing, per-league
picks, active/left/disabled membership refusal, unverified legacy sessions, foreign
selectors and inconsistent edges, null historical club links, read-only behavior
and disabled Loyola compatibility. All fixtures/accounts are synthetic; network
access is blocked by the test harness.
