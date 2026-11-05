# Opt-in Auth0 identity and read-only league context in the real Flask runtime

This slice runs in `pickem_flask_htmx_tabs.py`, not a replacement/synthetic app.
It adapts the private-beta RS256/OIDC transport, PKCE, state/nonce, atomic session
consumption and rotation patterns. No private-beta bootstrap, account records,
onboarding services or runtime imports are copied.

## Behavior

Default `RUNTIME_AUTH0_ENABLED=0` leaves the existing app unchanged: no Auth0
imports, routes, session-store access, discovery, migrations or account linking.
Legacy `/join`, `player_name`, room codes and admin sessions remain separate even
when this feature is enabled. Auth0 login never sets those legacy session fields.

Enabled routes (HTTPS at the exact configured callback origin only):

- `GET /auth/login`: small login form and separate anonymous auth cookie.
- `POST /auth/login`: same-origin CSRF check, state/nonce and S256 PKCE challenge.
- `GET /auth/callback`: atomic state consumption bound to that browser session,
  bounded token exchange, RS256 signature and pinned issuer/audience validation,
  required expiration/issued-at/nonce checks and authorized-party validation.
  Discovery/JWKS endpoints are pinned to the configured issuer. Token-supplied
  keys/URLs, unsupported algorithms, duplicate callback parameters and replays
  are refused. Successful login rotates the opaque session ID.
- `GET /auth/leagues`: only the authenticated user's active memberships in setup
  or active leagues; metadata only (ID, slug, name, status, membership ID, role),
  plus a session-bound logout CSRF token. No query-based identity overrides.
- `GET /auth/leagues/<id>`: same authorization query plus the selector; unknown
  and unauthorized leagues both return 404. The selector alone grants nothing.
- `POST /auth/logout`: same-origin CSRF check and atomic local session revocation.
  No Auth0/provider logout request is made; provider SSO and Loyola sessions are
  unaffected. Access/refresh/ID tokens are never retained or sent to the browser.

Each callback and membership request resolves the exact verified `(issuer, sub)`
against existing `user_identities`, joined to an active `users` record. Membership
requires `players.user_id`, `status='active'`, `left_at IS NULL` and the selected
league. Account/membership revocation is rechecked on requests. Missing sessions
return 401; unknown/unclaimed/disabled accounts return 403. Missing foundation
storage returns a fixed 503 without creating/upgrading tables.

Email/display name are neither identity keys nor account-claiming evidence.
OIDC identity verification here does not authorize recap delivery or establish a
verified destination email. The members-only verified-email/opt-out recap rule
remains a separate deliverable. Stage 2's unclaimed users gain no access until a
separately authorized identity-linking process establishes their associations.

## Configuration and storage prerequisites — not performed in this task

Install `requirements.txt` when preparing a future deployment. Its JWT/crypto
dependency is pinned to the version used by the private-beta implementation.
The disabled application path still does not import JWT or the Auth0 modules. No existing environment was
changed during development; test crypto dependencies were installed in a fresh
non-sensitive temporary directory.

All enabled configuration values must be owner-provisioned:

- `RUNTIME_AUTH0_ENABLED=1`
- `RUNTIME_AUTH0_ISSUER`: exact HTTPS issuer with trailing slash
- `RUNTIME_AUTH0_CLIENT_ID` and `RUNTIME_AUTH0_CLIENT_SECRET`
- `RUNTIME_AUTH0_CALLBACK_URL`: exact HTTPS `/auth/callback` URL
- `RUNTIME_AUTH0_SESSION_DB`: existing, dedicated absolute local SQLite file
- Existing `FLASK_SECRET` must be configured; development fallback is rejected

The authentication store is distinct from the game database. The installer
refuses the same game-file path, links/junctions and multiple-hard-link files.
Its `runtime_auth_schema` contract deliberately differs from the private-beta
store. Provisioning is explicit via `SessionStore.initialize()` and must receive
separate approval before use outside disposable test fixtures. Startup only
checks the existing store; it never initializes or migrates one. Reads of the
membership/game tables use the app's SQLAlchemy session and issue SELECT only.
The existing engine connection pragmas are unchanged.

Before any hosted enablement: independently prepare private encrypted persistent
session storage and operator-only/SYSTEM/admin permissions where applicable;
verify the HTTPS origin and trusted proxy handling; review approved Auth0 client
settings/callback allowlist and exact account associations. These prerequisites
are not verified against production here. Do not trust arbitrary forwarded headers
or relax the HTTPS/origin checks to make login work. Sessions last at most eight
hours, transactions five minutes; auth state is shared atomically across workers
on the one dedicated SQLite store. No auth state is stored in process memory.

## Legacy route boundary

Task 23 installs an exact-route, deny-by-default boundary whenever this Auth0
feature is enabled. Linked active Loyola members can use the audited legacy
browser views with centralized Loyola query scoping. Name-only sessions, foreign
league selectors, legacy admin and room-code join are refused. League B gameplay
through legacy routes is still unavailable. The separate read-only Task 26 endpoint
is documented in `RUNTIME_LEAGUE_MATCHWEEK.md`; Task 27 scoped pick submission is
documented in `RUNTIME_LEAGUE_DRAFT.md`. Disabled mode remains unchanged and is not multi-league
safe; do not populate/enable second-league gameplay through that path.

See `docs/RUNTIME_LEGACY_BOUNDARY.md` for all 55 route classifications, machine
credentials, scoping/CSRF rules, session verification and remaining prerequisites.

## Focused validation

`tests/test_runtime_league_auth.py` loads the actual Flask module, confines both
game and auth stores to synthetic temporary files, signs real RSA tokens locally,
mocks HTTP responses and forbids sockets. It covers successful membership lookup,
no game-DML/DDL during auth, unknown/unclaimed/disabled identities, exact issuer
links, cross-league refusal, revocation, state/browser binding/replay/expiration,
nonce/signature/key/audience/algorithm checks, PKCE, metadata/JWKS pinning,
rotation/logout/CSRF, storage/config refusal and disabled legacy `/join` behavior
on an unmigrated schema. It does not exercise live Auth0 or change configuration.

Protocol references reviewed: https://auth0.com/docs/secure/tokens/id-tokens/validate-id-tokens
and https://pyjwt.readthedocs.io/en/latest/api.html .

After installing requirements, reproduce the focused tests from this worktree:

```powershell
python -B -m unittest discover -s tests -p test_runtime_league_auth.py
```
