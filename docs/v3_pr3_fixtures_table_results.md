# Footy Pick ’Em V3 — PR 3: Fixtures + Table & Results

## Purpose

Rebuild the remaining two schedule/results destinations that still rely on legacy presentation:

1. **Fixtures** — future Pick ’Em schedule.
2. **Table & Results** — official league record.

This PR should replace the legacy Open Weeks and Season presentation with V3 pages while preserving all existing game rules, scoring, database behavior, and season history.

Product definitions:

> **Fixtures = what’s coming.**

> **Table & Results = what is official.**

Completed weeks belong in Table & Results, not Fixtures.

## Scope

### 1. Fixtures page

Replace the legacy Open Weeks presentation with a real V3 Fixtures page using filesystem templates and the existing V3 shell.

Primary desktop control:

**League View | Player Schedule**

#### League View

Show upcoming, non-finalized Matchweeks only.

- Each Matchweek appears as an expandable/collapsible row/card.
- The closest upcoming Matchweek should be expanded by default when practical.
- Expanded Matchweek shows the three league H2H matchups.
- Show matchup players clearly.
- Show draft/status context when useful.
- Preserve access to incomplete/open weeks.
- Do not show completed/finalized weeks here.

This page is about the Pick ’Em schedule, not a commodity Premier League fixture browser.

#### Player Schedule

Show the logged-in player’s future Pick ’Em opponent by Matchweek.

- one row/card per upcoming Matchweek
- week number
- opponent
- matchup status/context where useful
- no accordion is required for the basic player schedule
- guest/unmatched-player state should fail gracefully

If there is no logged-in player, default to League View and show a clear read-only notice rather than inventing player context.

### 2. Table & Results page

Replace the legacy Season presentation with a real V3 Table & Results page.

Purpose:

> The official factual record of the league.

#### League Table

Desktop default: **Detailed**

Provide toggle:

**Summary | Detailed**

Summary columns:
- Rank
- Player
- For
- Against
- Net

Detailed columns should preserve the approved grouped structure:

**Standings**
- Rank
- Player
- For
- Against
- Net

**Your Picks**
- Correct
- Incorrect
- Draws

**Opponent Picks / Against**
- Correct
- Incorrect
- Draws

Use subtle grouped headers / separators rather than a wall of unrelated columns.

Requirements:
- existing official standings calculation remains authoritative
- existing rank/tiebreak behavior remains unchanged
- exact ties continue to share rank as today
- sorting may be added for meaningful columns, but official Rank must remain visible and unchanged
- desktop should fit the approved sports-desk presentation
- mobile defaults to Summary

For mobile Detailed behavior:
- prefer horizontal scrolling within the table container, or a similarly bounded approach
- do not let the page itself overflow horizontally
- do not invent a complex per-player drilldown unless clearly needed

### 3. Completed Matchweeks / Results

Below the table, show finalized/completed Matchweeks.

Use an accordion/card pattern.

Each finalized week row/card should show:
- Matchweek number
- three H2H results
- winner/draw
- net margin
- payout where already supported by existing calculations

Expanded detail should reuse existing official week/matchup data and may show richer matchup-level information.

Do not duplicate scoring/payout calculations inside templates.

Completed weeks should no longer appear in Fixtures.

### 4. Reusable presentation/view-model architecture

Continue the V3 monolith strategy:

> **New V3 functionality should be cleaner than the code it replaces.**

Prefer small dedicated presentation/helper modules, e.g.:

- `v3_fixtures.py`
- `v3_table_results.py`

or another similarly bounded structure.

Responsibilities:

#### Fixtures view model
Shape:
- upcoming weeks
- three matchups per week
- logged-in player’s future opponent schedule
- status labels
- links/context needed by the template

#### Table & Results view model
Shape:
- summary standings rows
- detailed standings rows
- finalized week/result rows
- expanded matchup result data
- season selector context

Rules:
- existing SQLAlchemy models and established calculation helpers remain authoritative
- templates should receive presentation-ready data
- do not recalculate rank/scoring/payout rules in Jinja
- avoid broad refactors unrelated to these two destinations
- reduce reliance on inline `OPEN_PARTIAL` / `SEASON_PARTIAL` where safely possible

### 5. Route compatibility

Preserve existing URLs where practical:

- `/tab/open` remains the Fixtures destination for compatibility
- `/tab/season` remains Table & Results for compatibility

The V3 navigation should continue to work with HTMX and normal browser navigation.

Season query parameters and archived-season browsing should continue to work.

### 6. Responsive behavior

Desktop:

**Fixtures**
- clear page header
- League View / Player Schedule toggle
- expandable future weeks
- matchup rows/cards with strong hierarchy

**Table & Results**
- detailed standings default
- grouped column headers
- completed Matchweeks below
- accordion expansion

Mobile:

**Fixtures**
- compact toggle
- stacked week accordions
- Player Schedule as simple stacked rows/cards

**Table & Results**
- Summary default
- Detailed available through toggle
- detailed table scrolls inside its own container
- completed weeks stack cleanly

Use the approved Premium Sports Desk visual system from PRs 1–2.

### 7. Preserve existing behavior

Do not alter:

- scoring
- rank/tiebreak rules
- draft rules
- payouts
- current-week selection
- result finalization logic
- database schema
- score sync
- football-data polling
- Correspondent
- join/admin/security behavior

This is presentation + view-model restructuring, not game behavior change.

## Explicit non-goals

Do NOT implement in this PR:

- Auto-Draft
- Live Matchweek
- live score timelines
- projected standings
- projected payouts
- What to Look For
- personalized briefing
- Training Ground redesign
- Around the League redesign
- season-position graph
- AI Scout
- new DB schema
- scoring or payout changes
- kickoff locking
- club crest asset project unless an existing reusable treatment already exists
- React/Vue/SPA rewrite
- broad application-factory/blueprint refactor

## Visual direction

Continue the approved **Premium Sports Desk** visual system.

### Fixtures
The page should feel like a league schedule desk:
- upcoming weeks first
- matchup-oriented
- compact
- expandable
- no commodity football clutter

### Table & Results
The page should feel authoritative and official:
- strong but quiet table hierarchy
- grouped statistics
- clear rank
- completed results underneath
- avoid finance/accounting-dashboard styling

Use the supplied V3 Fixtures and Table & Results mockups as the primary visual references.

Treat them as visual direction, not literal data requirements.

Do not invent fields that do not exist.

## Testing expectations

Run the full existing test suite.

Add focused regression coverage for at least:

### Fixtures
- finalized weeks excluded
- upcoming/incomplete weeks included
- league view shows all three H2Hs
- player schedule shows only logged-in user’s future opponent
- guest/no-matchup states handled safely
- archived season browsing works
- full-page and HTMX navigation both work

### Table & Results
- Summary values match existing official totals
- Detailed values match existing detailed totals
- rank/tie behavior unchanged
- finalized weeks rendered
- non-finalized weeks excluded from completed results
- payout/net values match existing helpers
- season selection preserved
- full-page and HTMX navigation both work
- mobile Detailed markup remains inside a scroll container

Existing security/gameplay/Correspondent/score-sync tests must remain green.

## Browser verification

Before requesting review:

- desktop around 1440px
- mobile around 390px
- narrower mobile around 360px

Inspect:
- Fixtures League View
- Fixtures Player Schedule
- Table & Results Summary
- Table & Results Detailed
- completed Matchweek accordion
- season switching
- HTMX navigation
- browser back/forward
- horizontal overflow

Do not merge or deploy.

## Acceptance criteria

PR 3 is ready for review when:

1. Fixtures is a genuine V3 page and no longer just legacy Open Weeks.
2. Fixtures contains future/non-finalized Pick ’Em schedule only.
3. League View and Player Schedule both work.
4. Table & Results is a genuine V3 page.
5. Summary/Detailed standings work with official existing calculations.
6. Completed Matchweeks/results live under Table & Results.
7. New presentation code lives outside the monolith where practical.
8. No game-rule/schema/sync/Correspondent changes are present.
9. Full tests pass.
10. Desktop/mobile browser smoke tests pass.
11. No merge or deployment has occurred.

## Follow-on

After PR 3:
- PR 4: Training Ground + Around the League + deterministic stats/season-position graph
- PR 5: responsive polish + cleanup

Wave 2 then introduces Auto-Draft and true Live Matchweek functionality.
