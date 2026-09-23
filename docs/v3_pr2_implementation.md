# V3 PR 2 — Matchweek implementation

The Matchweek route now renders filesystem templates with Draft, Matchup Set,
and Final presentations. The existing V3 shell and other destinations remain
unchanged. `v3_matchweek.py` shapes normalized matchup, player, pick-history,
owned-fixture, turn, and official-result data without database access.

## Scope and reference decisions

- The supplied Draft, Slate Complete, and Final mockups guide the editorial
  headings, prominent turn state, desktop draft-history sidebar, and two-player
  ownership slate. Mobile stacks the players, available fixtures, draft history,
  and league context. No stadium artwork or club crests were added.
- Other Matchups ships as compact summaries. The optional modal/sheet is deferred;
  there are no controls that imply an unimplemented modal or Auto-Draft action.
- Fixture data has no venue field. No venues, deadlines, countdowns, standings
  impacts, research, or Live projections are invented from the mockups. Existing
  kickoff timestamps use the established Eastern-time formatter; missing times
  display its existing TBD label and sort after known kickoffs.
- Ten picks means Matchup Set, not Live. Partial official results can annotate
  individual owned fixtures; final H2H scores, winner, net and payout appear only
  when the existing week state is finalized. No automatic polling was added.
- The original weekly per-pick points expression is extracted into
  `pick_contribution`, used by both weekly totals and the new view model. Existing
  outcome, turn and payout helpers remain authoritative. For example, a margin
  of four still pays $20 under the existing $5/point rule, regardless of illustrative
  dollar amounts in the mockups.
- Guests and players without a matchup see a read-only league matchup and a clear
  notice. Archived seasons remain read-only. Season leaders remain in Training
  Ground, linked from Matchweek; legacy room-code and admin-result clutter is
  omitted from the new player page.

## Compatibility

`/` and `/tab/current` retain season and forced-week selection, full-page rendering,
HTMX navigation, and active navigation updates. `/partials/matchweek/<week>` is a
new manual refresh target. New pick forms submit to the existing `/pick` endpoint
with `presentation=v3`, preserving all eligibility and draft validation. The
successful response replaces the complete Matchweek fragment, including the
tenth-pick transition; ordinary non-HTMX V3 posts redirect back to the full page.
Existing partial endpoints and legacy pick responses are retained for compatibility.
The shared pick-confirmation code supports both legacy selectors and V3 club buttons.

## Verification

The full unittest suite includes focused coverage for viewer/turn context, both
club actions, ten successful picks, five unique fixtures per player, kickoff
ordering with missing timestamps, official outcomes/contributions/payouts,
partial-result state, full/HTMX/history requests, empty/unassigned/archived states,
and escaping. Browser review uses separate local sample data for Draft, Ready,
Final and the ninth-to-tenth pick transition at 1440, 390, and 360 pixel widths.

No schema, draft rules, kickoff locks, score sync, Correspondent, provider polling,
or Live functionality changes are included.
