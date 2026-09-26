# V3 PR 5 verification

Reviewed all five destinations against the PR 5 contract on 2026-09-25.

## Changes and classification

- Bug: the analytics background extended 8px beyond the page between 701px and 760px. Its inset now changes at the shell's 760px breakpoint.
- Bug: long player names overlapped adjacent cells in the summary standings and H2H matrix. Summary names now wrap; the matrix uses intrinsic column widths inside its existing scroll region.
- Bug: keyboard focus stayed behind the pick confirmation dialog. Opening now focuses Go Back, Tab/Shift+Tab stay within the dialog, and cancellation/confirmation restore the triggering control. The existing confirmedPick event is unchanged.
- Inconsistency: the Weekly Results separator was before the player's Draws column after Total Net was inserted. It now separates Opponent Picks.
- Low-risk accessibility polish: declare English on the document element.
- Safe cleanup: remove selectors for the replaced pick list, intro layout, feature text, inline season record, and checkbox chart legend. Searches of templates, scripts, and the monolith found no remaining consumers.

Intentional page hierarchy, typography, card density, and placeholders remain unchanged. Uncertain legacy/admin presentation was retained. No application/domain Python, schema, scoring, ranking, payout, authentication, or sync behavior changed.

## Browser smoke

Used the local review SQLite database in read-only mode on port 5006 and a separately generated temporary test database on port 5007. No production database or existing review data was modified. The synthetic dataset included six finalized weeks, an incomplete week, an empty archived season, and a long player name.

- 1440px: Draft, Matchup Set, Final; Fixtures League View and Player Schedule; Summary/Detailed standings, sorting, completed-week expansion; Training Ground and Around the League.
- 1024px: all five destinations, navigation, card stacking, bounded tables. Additional 900px checks covered the shared header, Draft controls, Training Ground, and Weekly Results.
- 390px and 360px: all five destinations, bottom navigation and active state, forms and table controls. Draft pick controls and cancellation were exercised; Matchup Set and Final were also checked at 360px.
- 730px: analytics page width matched the viewport's content width after the background fix (715px client and scroll widths).
- Long-name summary cells no longer overlap scores. The H2H matrix and charts scroll inside labeled regions; document width remains bounded. Multiweek chart lines, endpoint initials, and exact-position disclosure were inspected.
- Direct URLs, HTMX navigation, season switching, archived/read-only and guest views, and browser back/forward retained the expected page and season. No browser console errors were observed in the final checks.
- Confirmed visible keyboard focus, dialog focus entry/wrapping/restoration, and footer/content clearance above the mobile bottom nav. Confirmation was cancelled in the browser; submission behavior is covered by the focused test without writing review data.

This was a browser smoke pass, not a formal accessibility certification or exhaustive cross-browser test.

## Automated validation

- `.venv/Scripts/python.exe -m unittest discover -s tests`: 162 passed. Existing datetime deprecation warnings remain.
- `node --test tests/pick_confirm.test.cjs`: 3 passed; uses Node's built-in test runner without additional dependencies.
- `git diff --check`: passed.

The original untracked overview and local server logs are preserved and excluded from the PR. Nothing was merged or deployed.
