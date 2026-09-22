# Footy Pick ’Em V3 — Wave 1 / PR 1 Implementation Brief

## Purpose

Establish the V3 application shell and UI foundation without changing core game behavior.

This PR should make the app begin to look and navigate like V3 while also starting the gradual cleanup of the current monolithic Flask file.

## Product baseline

Primary player-facing destinations:

1. Matchweek
2. Fixtures
3. Table & Results
4. Training Ground
5. Around the League

Admin remains separate from the normal player experience.

Core visual direction: **Premium Sports Desk**

Principle: **Quiet structure. Loud moments.**

The UI should feel professional, modern, polished, sports-oriented, clean, and immediately understandable. Use soft light backgrounds, dark/navy navigation, white content cards, subtle blue accents, restrained borders/shadows, and selective green/red state treatment. Do not drift into a sportsbook/casino aesthetic.

## Scope for PR 1

### 1. Introduce a real template/static structure

Do not add another large inline V3 template/CSS block to `pickem_flask_htmx_tabs.py`.

Create a conventional Flask presentation layer, e.g.:

- `templates/v3/base.html`
- `templates/v3/partials/navigation.html`
- `templates/v3/pages/`
- `templates/v3/components/`
- `static/v3/v3.css`
- `static/v3/v3.js` only if needed

Exact filenames can vary if there is a cleaner structure.

The existing app may continue to use legacy inline templates where those screens have not yet been migrated. This PR should create the new pattern rather than refactor unrelated legacy code.

### 2. V3 application shell

Implement the shared V3 shell:

- dark/navy desktop header
- Footy Pick ’Em product mark/title
- primary navigation
- active-page state
- season/account utility area where appropriate
- responsive page container
- mobile bottom navigation for the five player destinations

Desktop nav labels:

- Matchweek
- Fixtures
- Table & Results
- Training Ground
- Around the League

Admin should not appear as an equal player-facing destination in the primary nav.

### 3. Route compatibility / navigation aliases

Avoid breaking existing bookmarked/internal URLs unnecessarily.

The current routes can initially remain the underlying handlers, but the V3 shell should expose the new product labels.

Prefer a migration path where future PRs can introduce cleaner named routes without forcing PR 1 to rewrite all page behavior.

Do not change database models or scoring behavior.

### 4. UI foundation / design tokens

Introduce reusable CSS variables/classes for:

- page background
- navy/header color
- primary blue accent
- muted text
- border color
- green success/correct state
- red wrong/loss state
- neutral/draw state
- card radius
- card border/shadow
- spacing scale
- typography hierarchy
- max content width

Exact palette and font family are not locked. The system should make them easy to tune later.

### 5. Reusable presentation components

Establish reusable template/component patterns for future V3 PRs.

At minimum, create or prepare patterns for:

- page header
- card
- status badge
- section header
- table container
- accordion shell
- matchup identity/summary shell
- empty-state shell

Do not implement full Matchweek/Fixtures/Table/etc. redesigns in PR 1 unless a tiny placeholder is necessary to demonstrate the shell.

### 6. Preserve current functionality

Existing core functionality should still work:

- join/player session flow
- current-week gameplay
- open-week access
- season/results access
- stats access
- admin access
- HTMX interactions
- result sync
- Correspondent automation
- score sync

The intent is presentation architecture + navigation, not gameplay change.

## Explicit non-goals

Do NOT implement in this PR:

- Auto-Draft
- priority/bulk picks
- live event timeline
- live projected standings
- live projected payout logic
- What to Look For
- personalized briefing
- AI Scout
- season-position graph
- new scoring logic
- DB migrations
- new football-data provider behavior
- Correspondent changes
- large-scale application factory/blueprint rewrite
- React/Vue/SPA conversion

## Monolith strategy

V3 should reduce dependence on `pickem_flask_htmx_tabs.py` incrementally.

Rule:

> New V3 functionality should be cleaner than the code it replaces.

For PR 1:

- presentation assets should move outside the monolith
- only minimal route glue should be added to the main file
- do not move unrelated business logic merely for cleanliness
- do not perform a high-risk all-at-once refactor

Future V3 PRs should progressively extract page/view-model logic as each screen is rebuilt.

## Technical preference

Flask + HTMX remains the implementation model.

HTMX is sufficient for:
- page/tab swaps
- accordions
- modals
- partial refreshes
- future draft updates
- future scoped selectors/tables

No frontend framework rewrite is required.

## Visual reference intent

Treat the approved mockups as visual references, not pixel-perfect specifications.

Important characteristics to preserve:

- dark navy header
- soft off-white/gray page background
- white cards
- restrained blue accents
- clean information hierarchy
- selective green/red state color
- generous but not wasteful spacing
- sports-product feel rather than finance/accounting dashboard
- professional first, energetic only around important game moments

## Testing expectations

Run the full existing test suite.

Add focused regression coverage for any route/navigation behavior changed by this PR.

At minimum verify:

- app imports successfully
- join flow still works
- each existing player-facing route still returns successfully
- active nav state is correct
- admin remains accessible
- no existing security regression is introduced

No production deployment should occur as part of implementation.

## Acceptance criteria

PR 1 is ready for review when:

1. The new V3 shell exists in conventional template/static files.
2. The five V3 destinations are visible in navigation.
3. Desktop and mobile navigation patterns are implemented.
4. Current app functionality still works.
5. No DB/schema/gameplay changes are present.
6. The main monolith has not grown with another large inline V3 presentation block.
7. The full test suite passes.
8. The diff is bounded enough to review as a foundation PR.

## Expected follow-on PRs

- PR 2: Matchweek
- PR 3: Fixtures + Table & Results
- PR 4: Training Ground + Around the League + deterministic stats/season-position graph
- PR 5: responsive polish + cleanup

Wave 2 then handles Auto-Draft and Live Matchweek functional expansion.
