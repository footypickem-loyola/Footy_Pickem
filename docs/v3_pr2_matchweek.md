# Footy Pick ’Em V3 — PR 2: Matchweek

## Purpose

Rebuild the player-facing Matchweek experience on top of the V3 shell introduced in PR #6.

This PR should replace the legacy Current Week presentation with a V3 Matchweek page while preserving existing gameplay rules and calculations.

The core product model is:

> **Before pick 10: build the slate. After pick 10: follow the slate.**

## Important terminology

**Completed Slate does NOT mean Live.**

It means the draft is complete — all 10 Premier League fixtures have been assigned, five to each player — but the matches have not necessarily started.

Working lifecycle:

1. **Draft** — fewer than 10 picks; the matchup slate is still being built.
2. **Completed Slate / Pre-Match** — all 10 picks are complete; show the two players’ five owned fixtures each, ordered by kickoff.
3. **Live** — later V3 work, once live Pick ’Em consequences are introduced.
4. **Final** — all fixtures are finalized and the official matchup result is known.

“Completed Slate” is an internal/product term. The UI does not need to display that exact phrase. A cleaner user-facing label such as “Ready” / “Matchup Set” or no explicit state label is acceptable.

## Scope

### 1. Replace legacy Current Week presentation

The Matchweek tab should become a real V3 page rather than rendering the legacy Current Week UI inside the V3 shell.

Use normal filesystem templates and reusable components under `templates/v3/`.

Do not add another large inline Matchweek template to `pickem_flask_htmx_tabs.py`.

### 2. Introduce a reusable Matchweek / matchup view model

Create a normalized presentation model for one H2H matchup that can support Draft, Pre-Match, Final, and later Live.

It should expose presentation-ready data such as:

- matchup ID / week
- player A / player B identity
- logged-in player context
- first picker
- current turn
- total picks / draft completion
- each player’s owned fixtures
- selected club per fixture
- kickoff time
- venue if already available
- final result state where available
- pick contribution (+1 / 0 / -1) where finalized
- matchup For / Against / Net where applicable
- payout where applicable
- status/state

Do not duplicate scoring rules in templates.

Prefer extracting view-model/helper code into a small dedicated module rather than growing the monolith.

Suggested direction (exact filename may vary):

- `matchweek.py` or `v3_matchweek.py`
- pure/helper functions where practical
- existing SQLAlchemy models and established calculation helpers remain source of truth

Do not perform a broad application rewrite.

### 3. Draft state

Primary question:

> What is available, whose turn is it, and what has already been drafted?

Desktop design should include:

- Matchweek number
- player-vs-player matchup identity
- compact season context where already available
- prominent current-turn / YOUR TURN state
- available fixtures
- home and away clubs treated equally
- kickoff date/time
- venue if already present in current data
- pick action for either club
- Draft So Far / Pick Log showing sequence, player, selected team, and opposition

Remove/de-emphasize legacy clutter that does not serve the V3 design.

Do not add club form/research content.

Do not implement Auto-Draft yet. A restrained placeholder is acceptable only if it helps the approved design and is clearly non-functional.

### 4. Pre-Match / completed slate state

Once the 10th pick is made:

- remove available-fixture picking controls
- remove/de-emphasize current-turn and draft-order UI
- organize the page around fixture ownership
- show two columns on desktop: five owned fixtures for each player
- sort each player’s fixtures by actual Premier League kickoff
- make the selected club unmistakable
- show both clubs in the fixture
- show kickoff date/time
- show venue if already available
- keep Draft History as a secondary control/link/accordion rather than the primary experience

This is the state that bridges Draft to future Live functionality.

### 5. Final state

When all fixtures are finalized, reuse the same owned-fixture slate rather than switching to a completely different page.

Top summary should include, using existing official calculations:

- final Pick ’Em score / For totals
- winner
- net margin
- payout

Each owned fixture should include:

- final Premier League score if available in current data
- selected club
- correct / draw / incorrect
- contribution +1 / 0 / -1

Do not introduce new scoring calculations solely for this UI. Use existing source-of-truth logic.

### 6. Other Matchups module

Add a compact “Other Matchups” module underneath/alongside the logged-in player’s primary matchup when appropriate.

Product principle:

> **My matchup first; league context second.**

For PR 2, use existing current/final data only.

Do not implement the full future live projection system.

If interaction is included, prefer the approved V3 pattern:

- desktop: modal
- mobile: near-full-screen sheet / modal
- reuse the same matchup/slate component and normalized view model
- show the other H2H without navigating away

Keep this bounded. If the modal materially expands scope, the compact module may ship first and modal work can be deferred.

### 7. HTMX behavior

Preserve existing pick submission behavior.

A successful pick should update the relevant Matchweek content without a full application rewrite.

When pick 10 completes the draft, the UI should naturally transition from Draft to the Pre-Match slate presentation.

Do not alter draft ordering rules or pick eligibility behavior.

### 8. Responsive behavior

Desktop:
- primary matchup first
- two owned-fixture columns once draft is complete
- balanced, premium sports-product hierarchy

Mobile:
- stack matchup summary
- current-turn area when drafting
- available fixtures
- Draft So Far accordion/secondary section
- once complete, stack player 1’s five fixtures then player 2’s five fixtures
- Other Matchups below

Fix Matchweek-specific horizontal overflow where the new V3 page replaces legacy tables/selectors.

Admin overflow is outside this PR unless touched by shared CSS.

## Architecture / monolith rule

Continue the PR 1 strategy:

> **New V3 functionality should be cleaner than the code it replaces.**

For PR 2:

- new Matchweek templates live outside `pickem_flask_htmx_tabs.py`
- presentation calculations should be shaped in a view-model/helper layer
- templates should not recalculate game rules
- avoid moving unrelated Correspondent/admin/sync logic
- do not introduce an application-factory/blueprint rewrite just for cleanliness
- reduce, rather than increase, reliance on inline Matchweek templates where reasonably safe

## Explicit non-goals

Do NOT implement:

- Auto-Draft
- priority/bulk picks
- live event timeline
- live clocks
- live score polling changes
- projected live payout
- projected standings
- finalized-fixtures-only live scoring
- What to Look For
- personalized briefing
- AI Scout
- season-position graph
- new DB schema
- scoring-rule changes
- draft-rule changes
- kickoff locking changes
- Correspondent changes
- football-data polling changes
- React/Vue/SPA rewrite

Live Matchweek is Wave 2 functionality and should be supported architecturally, not implemented here.

## Visual direction

Continue the approved **Premium Sports Desk** shell from PR 1.

Matchweek should follow:

> **Quiet structure. Loud moments.**

Use emphasis for:
- YOUR TURN
- selected teams
- finalized correct/wrong states
- final winner/net/payout

Do not make every card/status visually loud.

Use the approved Matchweek mockups as the main visual reference, especially the ownership-based two-column slate. Where a mockup contains Live-specific data, reproduce the layout concept but do not invent live functionality in this PR.

## Testing expectations

Run the full existing test suite.

Add focused tests for at least:

- Draft state rendering
- current player/current turn presentation
- available fixture rendering
- pick action still works
- 10th pick transitions to Pre-Match slate
- each player owns exactly five unique fixtures after draft completion
- owned fixtures sort by kickoff
- finalized result state renders correct/draw/incorrect and contribution
- Matchweek route still works as full page and HTMX
- current player with no matching matchup is handled safely
- responsive markup does not rely on wide legacy Matchweek tables
- existing security/gameplay tests remain green

## Browser verification

Before requesting review:

- desktop around 1440px
- mobile around 390px
- narrower mobile around 360px

Inspect Draft, Pre-Match, and Final states with representative data.

Do not merge or deploy.

## Acceptance criteria

PR 2 is ready for review when:

1. Matchweek is a genuine V3 page rather than the legacy Current Week presentation.
2. Draft and completed-slate/pre-match states use the approved V3 hierarchy.
3. Final reuses the same slate structure.
4. A reusable normalized matchup view model exists.
5. New presentation code lives outside the monolith.
6. Existing scoring/draft behavior is unchanged.
7. No schema or live-feature expansion is present.
8. The full test suite passes.
9. Desktop/mobile browser smoke tests pass.
10. No merge or deployment has occurred.

## Follow-on

After PR 2:
- PR 3: Fixtures + Table & Results
- PR 4: Training Ground + Around the League + deterministic stats/season-position graph
- PR 5: responsive polish + cleanup

Wave 2 then introduces Auto-Draft and true Live Matchweek behavior on top of the reusable Matchweek/slate model created here.
