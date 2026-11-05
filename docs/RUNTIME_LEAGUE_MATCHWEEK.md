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

## Task 28 pick interface (local review)

Open `/auth/leagues/<league_id>/seasons/<season_id>/matchweeks/<week_number>/view`
with the verified session. The page shows only the viewer's scheduled matchup(s),
opponent, ordered pick history, current turn, lock reason and eligible fixtures.
Each team button is an explicit fixture/team selection submitted through the
existing authenticated pick endpoint. No request player selector is used.

`draft` metadata on each Matchweek JSON matchup now includes `expected_count`,
`turn_player_id`, `locked`, `lock_reason`, `can_pick` and `available_fixtures`.
The server uses the real draft engine's snake-order function and shared read/write
phase, membership and kickoff eligibility checks. It excludes fixtures already
picked in that matchup, missing/elapsed kickoffs and recorded results. These are
snapshot hints; the write transaction always rechecks current authorization,
version, turn and eligibility. A lock or no scheduled matchup produces no forms.

The interface uses the existing Flask templates and native POST/redirect/GET
pattern, with no frontend framework or JavaScript requirement. Successful HTML
submissions redirect to the same scoped page with a fixed confirmation. A stale
or otherwise refused draft (400/409) redirects with a fixed refresh message and
fresh server state. Authentication, CSRF and foreign-scope refusals retain their
HTTP refusal. JSON clients retain their existing 201/error contract. No automatic
retry is introduced. Refresh also fetches the opponent's latest state; there is no
background polling. A concurrent change after rendering is handled by the write
guard. An uncertain network result must be checked with Refresh, not replayed.

Mobile layout uses a single bounded column, stacked team choices on narrow
screens, 48px buttons, visible keyboard focus, escaped labels and a status banner.
The exact HTML route permits only the local stylesheet in its CSP; scripts and
inline styles remain disallowed. Legacy templates/routes are unchanged with the
gate disabled. The central route inventory now enumerates 56 signatures.

Focused UI tests: `python -B -m unittest discover -s tests -p test_runtime_matchweek_ui.py`.
Tests submit the actual rendered forms, follow success/stale redirects, compare
server draft state, check lock/revocation/cross-league refusal, escaping and legacy
disabled-mode picks. All use synthetic data and blocked network transports.

The next small functional milestone is authenticated league/season/Matchweek
navigation so users can reach this scoped view directly after login. Scoped queue
editing, standings/scoring presentation, administration and background writers
remain unfinished. No production setup, account linking or deployment occurred.
