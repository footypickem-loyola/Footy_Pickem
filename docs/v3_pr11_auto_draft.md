# Footy Pick ’Em V3 — Wave 2 / PR 11: Auto-Draft

## Purpose

Add the first major behavioral feature of V3: **Auto-Draft**.

Auto-Draft should let a player prepare an ordered list of preferred picks before or during the draft. When it becomes that player's turn, the app should automatically submit the highest-priority preference that is still valid and available.

Core product principle:

> **Auto-Draft should feel like a convenience layer on top of the existing snake draft, not a separate draft mode.**

The existing manual draft remains authoritative and fully usable.

## Product behavior

### 1. Priority list

Each player may maintain an ordered Auto-Draft queue for the current Matchweek / matchup.

Each queue item represents:

- one Premier League fixture
- one selected team from that fixture
- a priority/order position

Example:

1. Arsenal over Fulham
2. Liverpool over Brentford
3. Brighton over Wolves

A fixture may appear only once in a player's active queue.

A player may reorder, remove, or replace queue items while the draft is still open.

### 2. Auto-Draft enabled state

Auto-Draft must be explicitly enabled by the player.

The UI should clearly distinguish:

- queue exists but Auto-Draft is OFF
- Auto-Draft is ON
- queue is empty
- Auto-Draft has no valid remaining preference

Do not enable Auto-Draft merely because a player has added priorities.

### 3. When Auto-Draft fires

When all of the following are true:

- matchup is still in Draft state
- it is the player's turn
- Auto-Draft is enabled for that player
- at least one queued preference remains valid and available

the app should immediately make the highest-priority valid pick.

After that pick, if the snake order means the same player is still on the clock for the next pick, Auto-Draft should continue and make the next valid queued pick.

This matters for the B,B / A,A turns in the snake sequence.

Auto-Draft should stop as soon as:

- the next turn belongs to the opponent
- the draft reaches 10 picks
- Auto-Draft is disabled
- no valid queued preference remains

### 4. Availability / invalidation

A queued preference is valid only if:

- its fixture still belongs to the current matchup
- that fixture has not already been drafted
- the queued team is one of the fixture's two teams
- the matchup is still open for drafting

If an opponent drafts a fixture that appears in your queue, that queue item becomes unavailable and must be skipped automatically.

Do not silently convert the queue item into the other team from that fixture.

The UI should show unavailable/skipped items clearly enough that the player understands why they were bypassed.

### 5. Manual picks and Auto-Draft interaction

Manual drafting must continue to work normally.

If Auto-Draft is enabled and it is the player's turn, the automatic pick may happen before the player can manually act. That is expected behavior.

If the player wants to draft manually, they should turn Auto-Draft off.

A successful manual pick should also trigger evaluation of the next turn. If that next turn belongs to a player with Auto-Draft enabled, their Auto-Draft should execute.

This allows the draft to advance naturally without requiring page refreshes or a background worker.

### 6. No timing/deadline feature in this PR

PR 11 does **not** introduce a draft clock, timeout, scheduled execution, kickoff lock, or background cron.

Auto-Draft is event-driven by draft activity / requests.

If nobody interacts with the app after a player becomes eligible for Auto-Draft, it is acceptable for execution to occur on the next relevant application request rather than through a new background service.

Do not add polling infrastructure solely for Auto-Draft.

## Persistence / data model

Auto-Draft preferences must persist across sessions and server restarts.

Use the existing SQLAlchemy / SQLite architecture and existing custom migration approach.

A small schema addition is expected and acceptable in this PR.

Recommended logical model:

### AutoDraftSetting
Per:
- matchup
- player

Fields conceptually:
- id
- matchup_id
- player_id
- enabled
- timestamps if useful

Unique:
- matchup_id + player_id

### AutoDraftPreference
Per ordered priority item:
- id
- matchup_id
- player_id
- fixture_id
- team
- priority/order
- timestamps if useful

Constraints:
- preference belongs to the same matchup/player scope
- one active preference per fixture per player/matchup
- deterministic ordering

Exact model/table names may differ if there is a cleaner implementation.

Do not store auto-draft state in Flask session or browser-only state.

## Concurrency and correctness

This PR must improve draft-write safety where necessary for Auto-Draft.

The existing draft flow historically had a known weakness: turn checking and pick insertion were not atomically reserved.

Auto-Draft increases the importance of fixing this.

### Required behavior

A pick write must not allow:

- two users to draft the same fixture
- a stale client to draft when it is no longer their turn
- manual and Auto-Draft requests to both claim the same turn
- duplicate Auto-Draft execution after retry/refresh

The implementation should use the database transaction / constraints available in the existing stack to make the final pick write authoritative.

At minimum:

1. begin transaction
2. reload authoritative matchup/pick state
3. recalculate current turn
4. verify fixture still available
5. verify selected team belongs to fixture
6. insert pick
7. commit
8. on conflict, rollback and retry/re-evaluate only where safe

Do not rely on the UI state as the source of truth.

If SQLite locking/concurrency limits require a bounded retry strategy, keep it simple and documented.

Do not undertake a database-platform migration in this PR.

## Auto-Draft execution engine

Create a small deterministic helper/service outside the presentation layer.

Possible module:
- `auto_draft.py`

Responsibilities:

- read enabled state
- load ordered preferences
- determine valid remaining preference
- submit one authoritative pick
- continue while the same player retains the turn
- stop safely
- return a structured summary of actions taken

Do not put this loop in Jinja or client-side JavaScript.

Do not duplicate snake-order logic; reuse the existing authoritative `compute_next_turn` / draft helpers.

### Suggested operation

A helper like:

`run_auto_draft_for_matchup(db, matchup)`

may repeatedly:

1. compute current turn
2. load Auto-Draft setting for that player
3. stop if disabled
4. find the first valid queued preference
5. attempt the pick using the same authoritative write path as manual drafting
6. repeat if the resulting next turn is still Auto-Draft eligible

Use one shared pick-write service/helper for both manual and Auto-Draft if practical.

The goal is to avoid having two implementations of "make a pick."

## UI

Auto-Draft lives in **Matchweek → Draft** only.

It should use the established V3 Matchweek visual system.

### Desktop

The existing right-side Draft So Far / contextual rail is the preferred home.

Provide a compact Auto-Draft panel with:

- On/Off toggle
- ordered priority list
- add preference control
- reorder controls
- remove action
- status such as:
  - Auto-Draft On
  - Auto-Draft Off
  - Waiting for your turn
  - No valid priorities remaining

Do not let Auto-Draft overwhelm the primary manual drafting UI.

### Mobile

Use a compact collapsible section/card below the primary draft controls or Draft So Far.

The queue must remain easy to edit without page-level horizontal overflow.

### Adding a preference

The player needs to select:

- an available fixture
- one of the two clubs from that fixture

Prefer reusing the same fixture/team data already used by the manual draft UI.

Do not allow Draw.

Do not allow another player's queue to be edited.

### Reordering

Use low-complexity controls.

Acceptable:
- Move Up / Move Down buttons
- compact arrow buttons

Drag-and-drop is not required and should not be added unless it is trivial and accessible.

### Unavailable items

If an opponent takes a queued fixture, show the preference as unavailable/skipped or remove it from the active list with a clear status.

Do not hide the reason in a way that makes the queue appear to malfunction.

## Authorization

Only the logged-in player may:

- enable/disable their Auto-Draft
- add/edit/remove/reorder their preferences

They may do so only for a matchup in which they are a participant.

Archived seasons and finalized/completed drafts are read-only.

Do not weaken existing player/session checks.

## HTMX behavior

Use HTMX where useful.

Preferred experience:

- queue edits update the Auto-Draft panel without full reload
- a manual pick can trigger Auto-Draft progression
- Matchweek re-renders into the correct Draft / Matchup Set state afterward
- browser back/forward should not accidentally replay writes

All mutations must be POST requests.

GET requests must remain read-only.

## Notifications / visibility

No email, push, Slack, or n8n integration in this PR.

If Auto-Draft makes one or more picks, the refreshed Matchweek UI should make the resulting draft state obvious.

A lightweight inline message such as:
- "Auto-Draft selected Arsenal"
or
- "Auto-Draft made 2 picks"

is acceptable if it can be done cleanly.

Do not build a notification subsystem.

## Existing behavior to preserve

Do not alter:

- 10 fixtures per H2H
- 5 picks per player
- fixture exclusivity
- snake order A,B,B,A,A,B,B,A,A,B
- team-only selections
- scoring
- payouts
- standings
- week finalization
- Score Sync
- football-data.org integration
- Correspondent
- Training Ground / Around League analytics
- season history
- admin result entry
- Arsenal banter behavior for manual picks unless shared pick execution naturally invokes it; do not expand banter scope unnecessarily

## Explicit non-goals

Do NOT implement:

- Live Matchweek / timeline
- live score display changes
- draft timer
- automatic pick based on kickoff time
- kickoff locking
- default/random fallback picks
- AI-generated pick rankings
- probability/model-based recommendations
- notifications
- email reminders
- admin-managed Auto-Draft
- cross-week reusable preference templates
- "pick any favorite automatically"
- global player ranking/preferences
- drag-and-drop dependency
- frontend framework rewrite
- Postgres migration

If the queue runs out, Auto-Draft should simply stop.

It must never make an unrequested fallback selection.

## Migration safety

Because this introduces schema, follow the repo's existing migration pattern.

Requirements:

- additive migration only
- existing production data untouched
- migration safe to run on an existing production SQLite database
- migration idempotent
- no destructive table rebuild unless absolutely necessary and explicitly reviewed
- no production data writes during development/review

Document the new tables/columns and migration behavior in the PR.

## Testing expectations

Run the full existing suite.

Add focused coverage for at least:

### Preference CRUD
- player can add a valid preference
- cannot add Draw
- cannot add invalid team
- duplicate fixture preference rejected/handled
- reorder deterministic
- remove works
- enable/disable persists
- cannot edit opponent's queue
- archived/finalized matchup read-only

### Execution
- Auto-Draft OFF makes no automatic pick
- enabled player on turn picks highest-priority available preference
- unavailable first preference is skipped
- no fallback when all preferences invalid
- B,B consecutive turn makes two picks when two valid priorities exist
- Auto-Draft stops when turn changes
- draft completes correctly on pick 10
- queue execution is idempotent across repeated requests

### Manual interaction
- manual pick still works
- manual pick may trigger opponent Auto-Draft
- manual pick may trigger same-player second snake pick if enabled
- manual and auto paths use authoritative turn validation

### Concurrency / stale state
- stale turn submission rejected
- same fixture cannot be selected twice
- repeated Auto-Draft execution does not duplicate picks
- simulated competing writes fail safely / leave valid draft state

### Presentation
- panel only appears for participating player during Draft
- correct enabled/off status
- unavailable preference display
- full-page and HTMX behavior
- mobile markup remains bounded

Existing security, scoring, analytics, Correspondent, and sync tests must remain green.

## Browser verification

Before requesting review:

### Desktop ~1440px
- partial queue
- reorder
- enable/disable
- manual pick with Auto-Draft off
- enable and trigger one auto pick
- B,B or A,A double-pick behavior
- opponent takes first queued fixture -> next preference used
- queue exhaustion
- transition from Draft to Matchup Set

### Mobile ~390px and ~360px
- edit queue
- toggle Auto-Draft
- no overflow
- confirmation/manual controls remain usable
- Auto-Draft result visible after refresh

Also verify:
- direct Matchweek URL
- HTMX updates
- browser refresh
- browser back/forward does not replay POST
- archived season read-only

## Review / release safety

Do not merge or deploy.

Do not touch production data.

Use an isolated/local test database for Auto-Draft execution review.

Because this PR changes draft writes and adds schema, report clearly:

- migration details
- new models/tables
- shared manual/auto pick-write path
- concurrency protection added
- full test count
- browser verification performed

## Acceptance criteria

PR 11 is ready for review when:

1. A player can persist and reorder a current-matchup priority queue.
2. Auto-Draft is explicitly enabled/disabled.
3. Highest-priority available preference is selected automatically on that player's turn.
4. Unavailable preferences are safely skipped.
5. Consecutive snake turns Auto-Draft correctly.
6. Queue exhaustion never creates a fallback pick.
7. Manual and Auto-Draft use an authoritative safe pick-write path.
8. Stale/concurrent writes cannot corrupt draft state or double-select a fixture.
9. Existing draft/scoring behavior remains unchanged otherwise.
10. Schema migration is additive/idempotent.
11. Full tests pass.
12. Desktop/mobile browser smoke tests pass.
13. No merge/deployment/production-data change has occurred.

## Follow-on

After PR 11:

**PR 12: Live Matchweek / Timeline**

That work will introduce live/provisional Pick ’Em consequence views and should remain separate from Auto-Draft.
