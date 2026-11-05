# Authenticated league navigation (Task 29, local review)

The real Flask journey is Login -> My Leagues -> selected league's active seasons
-> selected season's Matchweeks -> existing scoped Matchweek pick interface.
It uses server-rendered Jinja templates, ordinary links and the existing mobile
stylesheet. No frontend framework, provider call, session-store migration or
remembered selection is added. The feature stays default-off.

## Routes and selection rules

- Browser requests with `Accept: text/html` to `/auth/leagues` show only the
  verified user's active, non-departed memberships in setup/active leagues.
- `/auth/leagues/<league_id>` shows that league's active, unarchived seasons.
  Seasons without a valid canonical football-season link are shown as not ready,
  without a destination link. Unknown/foreign/inactive leagues return 404.
- `/auth/leagues/<league_id>/seasons/<season_id>` lists its Matchweeks. It requires
  a current account/membership and an active, unarchived season in that exact
  league, plus valid canonical football season/week and slate associations.
  Foreign/inactive/archived seasons return 404; inconsistent links/statuses return
  409. An empty schedule has a clear empty state. Every generated link keeps the
  league, season and local week number explicit.
- Exactly one unfinished week produces a prominent Continue link, labeled as the
  only unfinished week. Multiple unfinished weeks, or all finalized weeks, require
  explicit selection; no date-based guess or automatic redirect is made. Finalized
  and provisional weeks in an active season remain selectable read-only views.
- Existing direct historical Matchweek reads remain supported and locked for
  inactive/archived seasons. They are not offered as active navigation targets;
  pick submission still refuses. Setup leagues are explicitly labeled read only.

The existing membership JSON API is preserved when HTML is not explicitly
preferred (including `Accept: application/json` and existing no-Accept clients).
It retains league fields and `logout_csrf`. Content negotiation uses `Vary: Accept`;
all private responses remain no-store. Browser callback already redirects to
`/auth/leagues`, so it now lands on a usable page without constructing URLs.

Account and membership status are re-read on every protected request in the same
SQLite read transaction as navigation metadata. A stale link, browser history or
league ID grants nothing. Query-based identity/season/redirect overrides are
refused. Cross-league season links refuse before returning its metadata. The new
route is explicitly classified in the 57-signature default-deny route inventory.
Stylesheet-only CSP applies to these HTML pages; labels are escaped. No identity,
invitation, claiming, commissioner or platform-admin capability is added. Legacy
Loyola routes and disabled-mode UI are unchanged; League B remains excluded from
legacy global routes.

## Focused synthetic validation

From this worktree after installing requirements:

```powershell
python -B -m unittest discover -s tests -p test_runtime_league_navigation.py
```

Twelve tests cover the full callback-to-Matchweek link journey, two-league switching,
JSON compatibility, direct-link authorization, revocation, account/league/season
states, ambiguous week selection, canonical inconsistencies, invalid selectors,
escaping, empty schedules and disabled legacy HTTP picks. All accounts/databases
are synthetic and network access is blocked. Auth/boundary/draft/Matchweek focused
regressions are also checked; no broad suite or migration is run.

The next functional milestone is league-scoped standings/scoring presentation
using the existing scoring rules, or a separately reviewed scoped Auto-Draft UI.
Production remains dependent on approved identity links, persistent auth storage,
reviewed Auth0/HTTPS setup, Stage 2 fencing and writer coordination. None is
configured or exercised here.

Validation result: 76/76 focused runtime tests passed (16 authentication, 13 route
boundary, 10 Matchweek projection, 16 drafting/security, 9 pick UI, 12 navigation).
Synthetic My Leagues, seasons, Matchweek chooser and pick screens were checked in
an isolated browser at a 390px emulated viewport; all had content width 390px with
no horizontal overflow. Browser resources were local synthetic files; remote host
resolution was disabled. No live account or provider was used.
