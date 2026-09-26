# Footy Pick ’Em V3 — PR 5: Responsive Polish + Cleanup

## Purpose

Polish and harden the V3 player-facing experience now that PRs 1–4 are live.

This is **not** a redesign pass.

The current V3 surface already looks good. PR 5 should be conservative: fix clear inconsistencies, responsive rough edges, dead/duplicated presentation code, and small UX issues discovered while viewing the complete product as one system.

Primary principle:

> **Polish what exists. Do not reinvent it.**

The goal is to finish Wave 1 with a coherent, production-ready V3 surface before starting Auto-Draft and true Live Matchweek work.

## Scope

Review the full current player-facing V3 experience:

- Matchweek
- Fixtures
- Table & Results
- Training Ground
- Around the League
- shared shell/navigation
- mobile bottom navigation
- common cards/tables/toggles/forms
- desktop/mobile responsive behavior

### 1. Cross-page visual consistency

Audit for small inconsistencies across PRs 1–4.

Examples:
- page title sizing
- card radius / borders / shadows
- section spacing
- muted text hierarchy
- toggle styling
- form/select/button sizing
- table header density
- link treatment
- status pill treatment
- positive/negative metric styling
- desktop content widths
- empty states / notices

Do not normalize differences that are intentional for page hierarchy.

Do not redesign the visual system.

### 2. Responsive polish

Verify and improve behavior at:
- desktop around 1440px
- medium/tablet around 900–1024px
- mobile around 390px
- narrow mobile around 360px

Focus on:
- no page-level horizontal overflow
- bounded table/chart scrolling
- no clipped controls
- no overlapping labels
- useful mobile spacing
- bottom nav not covering content
- sensible card stacking
- charts remain readable
- Matchweek draft controls remain usable
- long player/team names do not break layout

Prefer CSS/layout fixes over new JS.

### 3. Navigation / state polish

Verify:
- active desktop nav state
- active mobile nav state
- HTMX navigation
- normal direct navigation
- browser back/forward
- season switching
- archived-season browsing
- query-string state preservation where already expected

Fix obvious regressions or inconsistencies only.

Do not introduce a new routing/navigation architecture.

### 4. Small UX cleanup

Address only clearly useful low-risk issues.

Examples:
- inconsistent labels/copy
- awkward pluralization
- unclear empty-state language
- buttons/links that look disabled but are active, or vice versa
- accessibility labels that no longer match visible copy
- focus/keyboard usability on details/toggles/forms
- obvious hover/focus states
- table headers that are ambiguous
- repeated placeholder wording

Do not add new product features.

### 5. CSS / JS cleanup

Audit the V3 presentation files for:
- duplicated CSS rules
- obsolete selectors
- selectors left behind from replaced legacy presentation
- redundant JS
- brittle one-off layout hacks that can now be simplified safely

Be conservative.

Do not perform a large CSS rewrite just for cleanliness.

Do not move code across files unless there is a clear benefit.

### 6. Legacy cleanup

Now that the main V3 pages are implemented, identify legacy presentation constants / inline templates / CSS / JS that are truly unused by player-facing routes.

Safe deletion is welcome if:
- references are verified absent
- tests cover the affected routes
- admin or legacy compatibility is not broken

Do not remove:
- admin presentation still in use
- partials still used by existing routes
- compatibility helpers without proving they are unused

If uncertain, leave it.

### 7. Accessibility basics

Perform a light pass:
- keyboard focus visibility
- accessible labels for controls
- sensible heading order
- tables retain scopes/captions where needed
- chart text/underlying data remains accessible
- color is not the only indicator where practical
- clickable details/toggles are keyboard operable

This is not a formal WCAG certification project.

## Explicit non-goals

Do NOT implement:
- Auto-Draft
- live Matchweek/timeline
- projected payouts
- projected standings
- live-score changes
- What to Look For
- AI briefing
- Correspondent changes
- new analytics
- new navigation destinations
- new DB schema
- scoring/ranking/payout changes
- kickoff locking
- club crest project
- frontend framework rewrite
- broad blueprint/application-factory refactor

If you find a product issue that would require feature design rather than polish, document it instead of solving it in PR 5.

## Architecture principle

Continue:

> **New V3 functionality should be cleaner than the code it replaces.**

But PR 5 is not an excuse for broad refactoring.

Favor:
- small diffs
- deletion of proven dead presentation code
- shared CSS improvements when genuinely shared
- regression tests for behavior touched

Avoid:
- speculative abstractions
- large file moves
- domain/business-logic refactors

## Testing expectations

Run the full existing test suite.

Add targeted regression tests only where PR 5 changes behavior rather than pure CSS.

Existing tests for:
- game rules
- security
- scoring
- rankings
- payouts
- Correspondent
- score sync
- V3 routes
must remain green.

## Browser verification

Perform one complete V3 smoke pass.

### Desktop ~1440px
- Matchweek: Draft / Pre-Match / Final where locally available
- Fixtures: League View / Player Schedule
- Table & Results: Summary / Detailed / completed week
- Training Ground
- Around the League

### Tablet ~900–1024px
- navigation/layout
- tables/charts
- multi-column pages collapse sensibly

### Mobile ~390px and ~360px
- all five primary destinations
- bottom nav
- no page-level horizontal overflow
- tables/charts scroll only inside bounded regions
- forms/toggles remain usable

Also verify:
- HTMX nav
- direct URLs
- browser back/forward
- season switching
- archived season
- guest/read-only state where practical

## Review workflow

Before making broad visual changes, compare the complete current V3 app and prefer **no change** if the existing implementation is already coherent.

If you encounter a questionable item, classify it as:

1. **Bug / inconsistency** — fix it.
2. **Clear low-risk polish** — fix it.
3. **Subjective redesign / new feature** — leave it and report it.

Do not churn UI merely to create a larger PR.

## Acceptance criteria

PR 5 is ready when:

1. The five V3 destinations feel coherent as one product.
2. Desktop/tablet/mobile have no obvious layout regressions.
3. No page-level horizontal overflow remains.
4. Shared controls/tables/status treatments are reasonably consistent.
5. HTMX/history/season navigation remains sound.
6. Any dead presentation code removed is demonstrably unused.
7. No new product feature or business-logic change slipped in.
8. Full tests pass.
9. Browser smoke checks pass.
10. No merge or deployment has occurred.

## Follow-on

After PR 5, Wave 1 of the V3 UI rebuild is complete.

Then proceed to Wave 2:
- Auto-Draft
- true Live Matchweek / timeline

Do not begin Wave 2 work inside this PR.
