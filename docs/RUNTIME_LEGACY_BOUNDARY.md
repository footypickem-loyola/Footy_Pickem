# Verified-mode legacy Flask boundary

Task 23 adds no schema or routes. The existing `RUNTIME_AUTH0_ENABLED` gate now
installs the boundary alongside verified authentication in the actual Flask app.
When disabled, no boundary listener or new-schema lookup is installed and the
legacy room-code experience remains unchanged on unmigrated databases.

## Policy

All 54 registered route signatures (endpoint, URL rule, declared methods) are
explicitly classified below. This is an exact allowlist, not a URL-prefix bypass.
New or changed signatures are denied by default. The focused route-coverage test
requires the registered inventory to equal the reviewed policy. No private browser
route remains outside the guard.

Private browser routes require a valid server-side Auth0 session, exact existing
issuer/subject association with an active account, and an active linked `players`
row for league ID 1 / slug `loyola-pick-em`, with no `left_at`. Missing foundation
storage fails closed; there is no name/email/room-code fallback or account claim.
A user who belongs only to League B cannot enter legacy gameplay. Auth0 membership
metadata endpoints may still list that user's B memberships. Task 26 adds one
separately authorized read-only Matchweek projection; see `RUNTIME_LEAGUE_MATCHWEEK.md`.

`current_player()` uses the verified membership's player ID for the current request.
Legacy `player_name` and `is_admin` session fields never authorize verified-mode
access. Room-code join, all legacy admin routes, and result-edit operations remain
blocked even for a commissioner until separately scoped admin authorization exists.
Legacy templates consequently do not advertise result-edit rights in this mode.

## Preventing global and indirect query leakage

Login checks alone would leave global player/season lists exposed. Request-local
SQLAlchemy loader criteria constrain every mapped legacy model via the actual
foreign-key graph to Loyola players/seasons. This includes indirect records,
relationships, ORM aliases, joins, composite-key caches/tasks, and the fresh session
used by the existing DraftService. Foundation columns are referenced through Core
expressions; they are not added to the legacy ORM metadata and no DDL runs.

Supplied season codes, fixture/matchup/pick/recap/source/player selectors and league
selectors are validated within that existing authorized scope. Invalid/foreign IDs
return 404; a selector cannot create identity or membership. Nested Auto-Draft
choices are revalidated by the unchanged draft service's now-scoped ORM lookups.
Raw Core/text statements from a private view are refused except the existing draft
transaction's exact `BEGIN IMMEDIATE` opener. SQL criteria are not installed for
public auth or credential-authenticated machine handlers.

State-changing legacy gameplay POSTs require the exact configured HTTPS Origin;
missing/foreign origins are refused. Existing draft ownership/version/fixture checks
remain unchanged. Private responses are `private, no-store`; Auth0 routes retain
their existing `no-store` and secure-origin/CSRF/state controls.

This is a boundary for the audited existing views, not permission for new raw
connection SQL or arbitrary new handlers. Any route/model/access-path changes need
policy tests and review; do not introduce engine-level queries that bypass the ORM
in protected views.

## Machine authentication and public endpoints

Each enumerated machine route requires its existing named service header and a
nonempty configured secret before dispatch; the original handler authentication,
limits and validation are also retained. Auth0/legacy browser cookies never replace
service credentials, and a service credential never authorizes browser gameplay.
Their current target/global business logic is unchanged. League-aware writer
coordination and provider operations remain separate workstreams.

Public endpoints are only static assets and the reviewed login/callback flow.
Account-context/logout routes retain their own authentication. There is no dedicated
health route today; `/` is private in verified mode. Any deployment probing `/`
needs a separately reviewed health-check plan before enabling the feature.

## Session and scope limits

No ID/access/refresh token or verified identity is placed in a client-readable
cookie. `__Host-footy_runtime` is a random opaque Secure/HttpOnly/SameSite=Lax cookie;
the dedicated SQLite auth store retains its hash and server-side identity/state.
Tests inspect both cookie attributes and the store. No additional session-storage
fix was required. Private, encrypted, persistent auth-store provisioning, HTTPS
proxy/origin verification and approved account links remain prerequisites for any
hosted enablement. No such provisioning/configuration or live login occurred here.

League switching, writable League B gameplay, foundation-aware new-write completeness,
scoped admin capabilities, automatic account claiming and league-aware background
writers are not implemented. The feature is off by default. No production or
preserved rehearsal data was accessed; tests use signed synthetic OIDC and temporary
synthetic two-league databases with sockets forbidden.

## Focused verification

29 tests passed: the 16 Task 22 authentication tests plus 13 route-boundary tests.
Coverage includes anonymous/name-only refusal, all registered signatures and new
route default-denial, ordinary Loyola tabs/polling/manual picks, league B selectors,
indirect/nested IDs, ORM aliases/fresh sessions, admin refusal, service credentials,
server-side cookie identity and disabled-mode legacy `/join` without foundation DDL.

After installing requirements, from this worktree:

```powershell
python -B -m unittest discover -s tests -p 'test_runtime_*.py'
```

The scoping pattern follows SQLAlchemy's `with_loader_criteria` /
`SessionEvents.do_orm_execute` guidance:
https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#sqlalchemy.orm.with_loader_criteria

## Exact registered route classification


### Public static assets (1)

- `GET /static/<path:filename>` → `static`

### Public authentication flow (state/CSRF controls apply) (3)

- `GET /auth/callback` → `runtime_auth.callback`
- `GET /auth/login` → `runtime_auth.login_form`
- `POST /auth/login` → `runtime_auth.login`

### Authenticated account context / logout (3)

- `GET /auth/leagues` → `runtime_auth.leagues`
- `GET /auth/leagues/<int:league_id>` → `runtime_auth.leagues`
- `POST /auth/logout` → `runtime_auth.logout`

### League-scoped read-only gameplay (1)

- `GET /auth/leagues/<int:league_id>/seasons/<int:season_id>/matchweeks/<int:week_number>` → `runtime_auth.matchweek`

This exact signature delegates to the authenticated blueprint's explicit scoped
SELECTs. It is not a prefix exemption or permission for other raw-SQL views.

### Verified Loyola legacy browser access (14)

- `GET /` → `shell`
- `POST /auto-draft/<int:matchup_id>` → `edit_auto_draft`
- `GET /partials/fixtures/<int:week_number>` → `fixtures_partial`
- `GET /partials/matchups/<int:week_number>` → `matchups_partial`
- `GET /partials/matchweek/<int:week_number>` → `matchweek_partial`
- `GET /partials/outcome-options` → `outcome_options`
- `GET /partials/scores/<int:week_number>` → `scores_partial`
- `POST /pick` → `make_pick`
- `GET /pick-insight/<int:matchup_id>` → `pick_insight`
- `GET /tab/current` → `tab_current`
- `GET /tab/league` → `tab_league`
- `GET /tab/open` → `tab_open`
- `GET /tab/season` → `tab_season`
- `GET /tab/stats` → `tab_stats`

### Explicitly denied legacy admin / room-code entry (14)

- `GET /admin` → `admin`
- `POST /admin/classify-correspondent-sources` → `admin_classify_correspondent_sources`
- `POST /admin/correspondent-sources` → `admin_add_correspondent_source`
- `POST /admin/correspondent-sources/<int:source_id>/status` → `admin_set_correspondent_source_status`
- `POST /admin/generate-recap` → `admin_generate_recap`
- `POST /admin/import-api-season` → `admin_import_api_season`
- `POST /admin/login` → `admin_login`
- `POST /admin/logout` → `admin_logout`
- `POST /admin/select-recap` → `admin_select_recap`
- `POST /admin/set-results` → `admin_set_results`
- `POST /admin/sync-api-results` → `admin_sync_api_results`
- `POST /admin/sync-correspondent-classification-jobs` → `admin_sync_correspondent_classification_jobs`
- `GET,POST /join` → `join`
- `POST /set_result` → `set_result`

### Machine-to-machine service authentication (18)

- `POST /api/correspondent/sources` → `ingest_correspondent_source_api` — `X-Correspondent-Secret` / `CORRESPONDENT_INGEST_SECRET`
- `POST /api/correspondent/sources/batch` → `ingest_correspondent_source_batch_api` — `X-Correspondent-Secret` / `CORRESPONDENT_INGEST_SECRET`
- `POST /tasks/correspondent/classification/reconcile` → `scheduled_correspondent_classification_reconcile` — `X-Correspondent-Automation-Secret` / `CORRESPONDENT_AUTOMATION_SECRET`
- `POST /tasks/correspondent/classification/submit` → `scheduled_correspondent_classification_submit` — `X-Correspondent-Automation-Secret` / `CORRESPONDENT_AUTOMATION_SECRET`
- `POST /tasks/correspondent/delivery/ack` → `scheduled_correspondent_delivery_ack` — `X-Correspondent-Automation-Secret` / `CORRESPONDENT_AUTOMATION_SECRET`
- `POST /tasks/correspondent/delivery/claim` → `scheduled_correspondent_delivery_claim` — `X-Correspondent-Automation-Secret` / `CORRESPONDENT_AUTOMATION_SECRET`
- `POST /tasks/correspondent/notifications/x-cap/ack` → `scheduled_correspondent_x_cap_notification_ack` — `X-Correspondent-Automation-Secret` / `CORRESPONDENT_AUTOMATION_SECRET`
- `POST /tasks/correspondent/notifications/x-cap/claim` → `scheduled_correspondent_x_cap_notification_claim` — `X-Correspondent-Automation-Secret` / `CORRESPONDENT_AUTOMATION_SECRET`
- `POST /tasks/correspondent/recap/generate` → `scheduled_correspondent_recap_generate` — `X-Correspondent-Automation-Secret` / `CORRESPONDENT_AUTOMATION_SECRET`
- `GET /tasks/correspondent/status` → `scheduled_correspondent_status` — `X-Correspondent-Automation-Secret` / `CORRESPONDENT_AUTOMATION_SECRET`
- `GET /tasks/correspondent/targets` → `scheduled_correspondent_targets` — `X-Correspondent-Automation-Secret` / `CORRESPONDENT_AUTOMATION_SECRET`
- `POST /tasks/correspondent/x/checkpoint` → `scheduled_correspondent_x_checkpoint` — `X-Correspondent-Automation-Secret` / `CORRESPONDENT_AUTOMATION_SECRET`
- `POST /tasks/correspondent/x/claim` → `scheduled_correspondent_x_claim` — `X-Correspondent-Automation-Secret` / `CORRESPONDENT_AUTOMATION_SECRET`
- `POST /tasks/live-ingest` → `live_ingest_snapshot` — `X-Live-Ingest-Secret` / `LIVE_INGEST_SECRET`
- `GET /tasks/pre-match-briefs/status` → `scheduled_pre_match_brief_status` — `X-Sync-Secret` / `SYNC_SECRET`
- `POST /tasks/sync-football-reference` → `scheduled_sync_football_reference` — `X-Sync-Secret` / `SYNC_SECRET`
- `POST /tasks/sync-pick-insight` → `scheduled_sync_pick_insight` — `X-Sync-Secret` / `SYNC_SECRET`
- `POST /tasks/sync-results` → `scheduled_sync_results` — `X-Sync-Secret` / `SYNC_SECRET`
