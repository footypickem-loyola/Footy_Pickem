# Footy Pick ’Em V3 — PR 4: Training Ground + Around the League

## Purpose

Rebuild the final two major player-facing destinations that still rely on legacy/placeholder presentation:

1. **Training Ground** — the logged-in player’s personal scouting dossier.
2. **Around the League** — league-wide Pick ’Em intelligence.

This PR should also add the first deterministic V3 analytics layer, including a season-position graph, while preserving all existing game rules, scoring, ranking, payout, database, sync, Correspondent, and authentication behavior.

Product definitions:

> **Training Ground = me.**

> **Around the League = us.**

Pick ’Em-specific information should outrank commodity Premier League information.

## Scope

### 1. Training Ground

Replace the legacy Stats presentation with a genuine V3 Training Ground page using filesystem templates and the existing V3 shell.

The page should feel like a personal scouting dossier rather than a generic statistics dashboard.

Recommended hierarchy:

#### This Week
Use existing current-week/player context where available.

Possible supported fields:
- current opponent
- current Matchweek
- draft / matchup state
- current season rank
- season net
- current streak or recent form if deterministically supported

Do not duplicate the Matchweek page. Keep this section compact and directional.

#### What to Look For
For PR 4 this is **presentation architecture only**.

- Provide a clearly labeled section or placeholder that establishes the future canonical home for the personalized pre-match briefing.
- Do NOT generate AI scouting content.
- Do NOT invent storylines.
- If no deterministic briefing exists yet, use restrained explanatory empty-state copy.

The actual personalized What to Look For feature belongs to a later intelligence wave.

#### My Season
Show a concise season summary using existing official finalized-week data.

Good candidates:
- Rank
- For
- Against
- Net
- Correct
- Incorrect
- Draws
- correct-pick percentage
- record / matchup wins-losses-draws if already reliably derivable

Use existing helpers where possible. Do not create competing versions of official standings totals.

#### My History & Tendencies
Reuse and improve existing player-specific analytics:

- head-to-head record vs each opponent
- club picking record
- most-picked clubs
- best/worst club results where sample size is shown
- recent form / matchup trend if deterministically supported
- season-by-season selection if archived seasons are available

Preserve useful existing filters where practical, including:
- player selector where appropriate for read-only exploration
- club filter
- minimum picks threshold
- sort options

The logged-in player should remain the default focus.

### 2. Around the League

Replace the current placeholder with a genuine V3 league intelligence page.

This is not a Premier League news page.

It should summarize what is happening **inside the Pick ’Em league**.

Recommended hierarchy:

#### Season Position
Add a deterministic season-position chart showing each player’s rank by finalized Matchweek.

Requirements:
- x-axis = finalized Matchweek number
- y-axis = league rank
- rank 1 visually at the top
- one series per player
- use official existing standings/tiebreak behavior at each finalized week
- exact ties should retain the official shared rank
- do not infer positions from current/provisional weeks
- archived-season selection should work
- chart must remain readable on mobile

Prefer a lightweight implementation with existing browser capabilities or a small, bounded chart dependency only if already present / clearly justified.

Do not introduce a major frontend framework.

If a new external chart dependency is proposed, stop and explain before adding it.

#### League Leaders / Superlatives
Add deterministic season leader cards/rows using finalized data only.

Candidates where supported:
- highest net
- most correct picks
- most points for
- most points against
- best current finalized-week streak
- biggest single finalized-week matchup win
- closest finalized matchup
- most frequently selected club
- best/worst club pick record with minimum sample shown

Do not force every candidate into the UI. Prefer a small useful set that the existing data supports cleanly.

No subjective AI labels.

#### Head-to-Head Explorer
Provide a league-wide H2H selector/table.

Users should be able to inspect:
- Player A vs Player B
- meetings
- wins / losses / draws
- cumulative net or margin where supported
- recent meeting results where practical

Reuse existing matchup/finalized-week calculations.

#### Club Picking Record
Provide league-wide club-selection intelligence.

Useful dimensions:
- by club across entire league
- by player when selected
- picks
- correct
- incorrect
- draws
- net contribution / success rate where already derivable
- minimum sample filter

Do not imply predictive value from small samples.

#### Recent Form / League Trends
Add simple deterministic trend presentation based on finalized Matchweeks.

Examples:
- last 3 / last 5 matchup outcomes
- net over recent finalized weeks
- winning/losing streaks
- standings movement since prior finalized week

Use only if cleanly supported without creating excessive business-logic duplication.

### 3. Deterministic analytics layer

Create reusable V3 analytics/presentation helpers outside the monolith where practical.

Possible structure:

- `v3_training_ground.py`
- `v3_around_league.py`
- `v3_stats.py` or `v3_analytics.py`

Exact filenames are flexible.

Responsibilities should include presentation-ready shaping for:
- personal season summary
- H2H records
- club records
- leader/superlative cards
- season position series
- recent form/streaks
- league-wide club records

Rules:
- existing domain/scoring helpers remain authoritative
- finalized weeks only for official historical analytics unless clearly labeled otherwise
- templates must not recalculate scoring/ranking/tiebreak logic
- avoid SQL/business logic in Jinja
- avoid broad refactors unrelated to these destinations

### 4. Season-position graph calculation

The graph should be deterministic and based on official standings snapshots.

For each finalized Matchweek N:
1. calculate official standings through week N using the existing standings helper
2. record each player’s official rank
3. output one series per player

Do not create a separate ranking formula.

If a player has no finalized history before a point where the season exists, represent the data consistently rather than inventing a rank.

The view model should expose chart-ready data separately from markup.

### 5. Route compatibility

Preserve existing routes where practical:

- `/tab/stats` remains Training Ground
- `/tab/league` remains Around the League

Existing season query parameters should continue to work.

Existing player/filter query parameters from legacy Stats should be preserved where useful and safe.

HTMX navigation, normal browser navigation, browser back/forward, and archived-season browsing must continue to work.

### 6. Responsive behavior

Desktop:

**Training Ground**
- strong personal page header
- compact This Week section
- season summary cards
- H2H and club tendencies with clear table/card hierarchy
- restrained filters
- no finance-dashboard aesthetic

**Around the League**
- season position chart near the top
- leader cards / league trends
- H2H explorer
- club intelligence
- responsive table/chart containers

Mobile:

**Training Ground**
- stacked sections
- compact stat cards
- tables scroll only inside bounded containers if needed

**Around the League**
- chart remains readable without page-level horizontal overflow
- legend/labels remain usable
- leader cards stack
- H2H and club tables remain bounded

Continue the approved **Premium Sports Desk** visual system.

### 7. Preserve existing behavior

Do not alter:

- scoring
- rank/tiebreak rules
- draft rules
- payouts
- current-week selection
- result finalization
- database schema
- score sync
- football-data polling
- Correspondent workflows/prompts
- join/admin/security behavior
- Matchweek behavior
- Fixtures behavior
- Table & Results behavior

This is presentation + deterministic analytics/view-model work.

## Explicit non-goals

Do NOT implement in this PR:

- Auto-Draft
- live Matchweek/timeline
- projected payouts
- projected standings
- live score ingestion changes
- AI-generated What to Look For
- personalized AI briefing
- Correspondent changes
- X/Grok investigation
- new DB schema
- scoring/payout/rank changes
- kickoff locking
- push notifications
- email changes
- React/Vue/SPA rewrite
- broad blueprint/application-factory refactor
- speculative predictive analytics

### Club crests
Treat club crests as a **nice-to-have only**.

If existing reusable crest assets can be used cleanly with minimal work, they are acceptable.

Do not let crest work introduce:
- external scraping
- new brittle dependency
- large asset-management work
- licensing uncertainty
- significant code
- scope creep

The layout should remain compatible with adding crests later.

## Visual direction

Continue the approved **Premium Sports Desk** system:

> Quiet structure. Loud moments.

Training Ground should feel personal and analytical.

Around the League should feel like the league’s internal intelligence desk.

Avoid:
- sportsbook styling
- fantasy-gaming neon
- generic BI dashboards
- commodity Premier League news layouts
- excessive cards for every statistic

Use hierarchy and whitespace. Highlight genuinely notable Pick ’Em information.

If no approved mockup exists for a particular screen, use the written IA and the established PR 1–3 visual language rather than inventing unsupported data or treating an imagined mockup as authoritative.

## Testing expectations

Run the full existing test suite.

Add focused regression coverage for at least:

### Training Ground
- logged-in player defaults correctly
- player selector/query parameter still works where supported
- finalized-only season totals match existing official helpers
- H2H records match finalized matchup history
- club-record filters/minimum-picks behavior preserved
- guest/read-only state handled safely
- archived season works
- full-page + HTMX navigation work

### Around the League
- season-position series uses finalized weeks only
- each point matches official `standings_through_week`
- shared ranks remain shared
- current/provisional week excluded
- leader/superlative calculations use finalized data
- H2H explorer matches finalized history
- club records match existing pick/result evaluations
- archived season works
- full-page + HTMX navigation work
- empty/early-season states render safely

Existing security/gameplay/Correspondent/score-sync tests must remain green.

## Browser verification

Before requesting review:

- desktop around 1440px
- mobile around 390px
- narrower mobile around 360px

Inspect:

**Training Ground**
- current player default
- another-player selection if supported
- season summary
- H2H
- club records
- filters
- archived season

**Around the League**
- season-position graph
- leader cards
- H2H explorer
- club records
- early-season/empty state
- archived season

Also verify:
- HTMX navigation
- browser back/forward
- season switching
- no page-level horizontal overflow

Do not merge or deploy.

## Acceptance criteria

PR 4 is ready for review when:

1. Training Ground is a genuine V3 page and no longer legacy Stats presentation.
2. Around the League is a genuine V3 page and no longer a placeholder.
3. The season-position graph is deterministic and uses official finalized-week standings snapshots.
4. Personal H2H and club analytics are preserved/improved.
5. League-wide deterministic intelligence is available without AI or speculative metrics.
6. New presentation/analytics code lives outside the monolith where practical.
7. No schema/game-rule/sync/Correspondent changes are present.
8. Full tests pass.
9. Desktop/mobile browser smoke tests pass.
10. No merge or deployment has occurred.

## Follow-on

After PR 4:

- PR 5: responsive polish + cleanup of the Wave 1 V3 surface

Then Wave 2:
- Auto-Draft
- true Live Matchweek / timeline

Wave 3:
- deterministic pre-match signals
- personalized What to Look For
- richer intelligence / Correspondent integration
