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
  there are no controls that imply an unimplemented modal. Draft includes a
  disabled Auto-Draft placeholder, explicitly labeled Coming in Wave 2.
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

## Draft visual revision

The approved Draft reference now drives a compact matchup card with next-pick
order, a separate current-turn banner, aligned kickoff/home/away/pick columns,
and a narrower Draft So Far rail containing all ten slots, including pending
slots. Mobile stacks these regions and retains a native history disclosure.
The existing snake-order expression is shared by eligibility and presentation;
no ordering behavior changed. Focused tests check every remaining slot and
that the next-order chip agrees with eligibility after each successful pick.

Intentional differences from the reference: real season/player/fixture names,
fixture ownership instead of unsupported player records/ranks, no invented
venues/deadline, no stadium/lion artwork or club crests. Team identity elements
can accept a crest later without changing row structure. Existing global shell,
manual refresh and stats links remain. Auto-Draft has no action or submission.

## Hero and context polish

Draft now uses a slightly smaller heading, a subtle navy-to-light page wash,
and an original, faint SVG stadium illustration. The Premier League logo is a
local copy of the official asset from
https://www.premierleague.com/resources/v1.37.4/i/svg-files/elements/pl-logo-light.svg
(accessed 2026-09-24); it identifies the competition and does not imply endorsement.
No external requests or new dependencies are required at render time.

The hero shows the earliest known fixture kickoff, labeled First kickoff: the app
has no enforceable draft deadline, so this is not presented as one. Venues remain
omitted because fixture data has no venue field. Player W–L–T records aggregate
existing finalized H2H scores before the selected week; places and net points
use the existing standings helper over the same cutoff. Before any final results,
players see 0–0–0 and No final results yet instead of a misleading shared first place.
Fixture opponents and vs are visually centered; Pick spans both equal actions.
