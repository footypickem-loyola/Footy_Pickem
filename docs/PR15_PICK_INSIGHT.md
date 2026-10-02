# V3 PR15 — What to Look For / Pick Recap

A committed V3 manual pick opens What to Look For after the refreshed draft
panel is installed. Closing it restores focus to the next available pick (so
consecutive snake turns work), or the recap control when the turn is over.
Rejected and stale picks do not open insight. GET refreshes do not replay it.

Bulk Picks confirmation remains the existing atomic priority-list operation.
It exposes View Pick Recap without automatically opening any popup. The bulk
recap follows the player's locked priority order and identifies queued,
drafted, and opponent-owned/skipped fixtures. Confirmed priorities are not
misrepresented as fixture ownership. Pick Recap, including from Matchup Set,
shows only the signed-in player's actual persisted picks in draft order.
There is one contextual recap CTA in the Matchweek header. Confirmed bulk
priorities take precedence while the player owns fewer than five fixtures;
once they own five, it opens the owned-picks recap, even if the opponent still
has a turn remaining. No second recap CTA is rendered in the bulk dialog.
The read-only endpoint also works in live, final, and archived states.

## Existing implementation inspected

- `make_pick`, `DraftService.command`, `_pick`, `_run`, and `_confirm`: persistence,
  ownership, expected-count conflicts, consecutive turns, automatic picks,
  bulk confirmation and locking.
- V3 `base.html`, `legacy.js`/CSS, `bulk_picks.js`/CSS, matchup and live components:
  confirmation, Arsenal-only celebration, native dialog conventions, mobile
  sizing, scroll containment, complete versus live versus finalized states.
- `Fixture`, `Result`, `Week`, `Season`, result import/manual overrides,
  `v3_matchweek`, `v3_analytics`, `FootballDataClient`, `SportmonksClient`, and
  the normalized live state/event models.

## Exact metric sources and rules

All calculation is in `v3_pick_insight.build_pick_insight`, using a server-side
join of `Fixture`, official `Result`, and `Week` scoped to the selected season.
No template or JavaScript computes football metrics.

1. **Overall Record:** `Result.outcome` (Home/Away/Draw), interpreted against
   `Fixture.home`/`away` for the selected club. Includes manual official results
   and football-data.org results already synchronized into `results`.
2. **Home/Away Record:** the same official outcomes filtered to the venue side
   of the selected fixture. No completed venue games produces `0W 0D 0L`.
3. **Last 5:** last five eligible official outcomes ordered by kickoff, then
   match number and fixture ID. Display is oldest to newest, left to right.
   Fewer than five shows only available matches; no history shows `—`.
4. **Goals / Game:** sum of the selected club's official `Result.home_score`
   or `away_score`, divided by the number of eligible games, one decimal place.
5. **Goals Against / Game:** corresponding opponents' official scores, same
   denominator and rounding. Both averages show `—` when no games exist or
   any eligible result lacks valid scores. Manual outcome-only overrides do
   not become fictitious 0–0 results or get excluded from the denominator.
6. **Top Scorer:** `—`. No complete PL season player-goal ledger or scorer
   snapshot is stored. The existing football-data.org integration requests
   competition matches, not season scorers. Sportmonks requests livescores
   with events; those are partial live snapshots, not guaranteed season totals.
   Summing them would publish misleading PL goal counts. A complete,
   season-scoped scorer source with stable player/club mapping is the missing
   dependency. No provider call was added for this PR.

The sample includes recorded official results before the selected fixture's
kickoff, bounded by the current UTC time. Unknown kickoffs fall back to earlier
matchweeks, with stable ordering. Unknown historical timestamps sort before
known timestamps. The selected fixture is always excluded. Postponed matches
with known dates follow actual kickoff order rather than matchweek number.
This is a read-time pre-fixture view, not an immutable pick-time snapshot;
later official corrections can change its values. Missing imported history
is not inferred. Coverage and missing-score handling are documented here;
the player-facing source note is simply “Based on Premier League results this season”.

Official results are the sole authority. No live score, event, projection,
or provider status overrides them. There are no provider requests on the
pick/recap path and no browser Sportmonks calls.

## Visual implementation and deviations

One shared Jinja component and one view model serve all three modes. The
existing Bulk Picks native dialog shell supplies viewport limits, backdrop,
scroll locking and mobile behavior. Insight is in the persistent page shell,
outside the polling Matchweek region. Native modal behavior traps focus and
supports Escape; content scrolls while the header/footer remain accessible.

The primary reference's centered heading, venue/opponent above the pitch,
selected-club hero, pitch outline, and six pastel horizontal bands are retained.
No shot/goal dots or Opta assets are used. Short/mobile viewports scroll rather
than shrinking text beyond legibility. A compact source note reads
“Based on Premier League results this season”. Unavailable metrics display
`—` without diagnostic copy.

Crests are not persisted in the existing models or available as local club
assets, so the current implementation uses the approved club-name fallback.
The shared component/view model accepts an optional crest and hides a failed
image while retaining the name. No unverified external crest URLs are invented.
Top scorer remains the approved `—` fallback. No AI, external news, injuries,
xG, odds, recommendations, or other analytics were introduced.

## Files

- `pickem_flask_htmx_tabs.py`: authorized read-only endpoint, recap availability,
  successful V3 manual pick event. Legacy Arsenal event remains on legacy mode.
- `v3_pick_insight.py`: server-side metrics and shared view model.
- `templates/v3/components/pick_insight.html`: shared insight content.
- `templates/v3/base.html`: persistent dialog and asset references.
- `templates/v3/components/bulk_picks.html`: confirmed-list dialog; recap is in the Matchweek header.
- `templates/v3/pages/matchweek.html`: recap entry points.
- `static/v3/pick_insight.css` and `.js`: pitch design and modal interaction.
- `tests/test_v3_pick_insight.py`, `tests/pick_insight_cases.py`,
  `tests/test_app.py`: focused data/HTTP coverage integrated into existing suite.
- `scripts/review_pick_insight.py` and `scripts/verify_pick_insight_browser.cjs`:
  repeatable synthetic browser verification, never opens an existing app DB.
- `docs/PR15_PICK_INSIGHT.md`: data provenance, limitations, and validation.

## Validation

Narrow CTA/copy follow-up: 14 focused Python insight tests and 7 Bulk Picks
Node tests passed. The PR15 browser harness passed all 12 layout checks again,
including assertions for exactly one recap CTA, routing to owned picks with
five selections before the opponent's final turn, and the simplified source note.

- `python -W ignore::DeprecationWarning -m unittest discover -s tests`:
  **225 tests passed**.
- `node --test tests/bulk_picks.test.cjs`: **7 tests passed**.
- PR15 Playwright harness: **12 layout checks passed** at 1440×1000,
  390×844, 360×640, and 844×390. Exercises manual confirmation, consecutive
  turns, no replay on reload, bulk confirmation without auto-open,
  Previous/Next, completed recap, focus containment/restoration and Escape.
  All six bands are reachable at every viewport. A simulated HTTP 503 shows
  a safe error, then retries successfully without altering saved picks.
  Zero page errors, horizontal overflows, or browser football-provider calls.
- Existing Live Matchweek Playwright harness: **30 viewport/scenario checks
  passed**, including HTMX refresh, reload, Back navigation, preserved scroll,
  and automatic pre-match/live/final transitions.
- Browser screenshots are produced through `PR15_BROWSER_OUTPUT`.

Run browser verification with the project's Python environment and Playwright
on Node's module path:

```text
python scripts/review_pick_insight.py
node scripts/verify_pick_insight_browser.cjs
```

The test server listens only on `127.0.0.1:5115` and creates its own temporary
SQLite database. Production configuration/data, Railway, deployment, scoring,
draft service logic, Correspondent, and live ingestion are unchanged.
