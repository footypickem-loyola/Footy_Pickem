# Minor pre-kickoff fixes — review notes

Prepared on `codex/minor-pre-kickoff-fixes`, based on
`f8cb8e63351150e98980b086dd299f329e19099d`. No commit, push, merge,
deployment, production database write, or Railway configuration change was made.

## Changes

- Fixtures cards and draft game options format the existing `kickoff_utc` in
  `America/New_York`, including EDT/EST and a missing-time fallback. A subsequent
  render uses the revised time after the existing football-data.org sync.
  `tzdata` supplies timezone data on platforms without a system timezone database.
- The cron caller now calls the existing protected sync endpoint on every run;
  the previous weekday gate is removed. The Railway schedule change is pending.
- Pick Log items retain compact, zero-padding list spacing with no visible
  status labels. Only the viewer's name is subtly yellow. Only the picked team
  is blue for correct or red for incorrect recorded results; unresolved and
  drawn fixtures remain neutral. Rows themselves have no background or border.
  Scoring functions are unchanged; draws remain worth zero.
- Successful HTMX tab responses replace the navigation out-of-band. Normal
  requests and history cache misses return the complete shell with the correct
  active tab and `aria-current`. Failed requests and panel loads leave it alone.
- Weekly rollup retains the compact Summary default. The Summary/Detailed
  buttons switch tables in place without navigation or writes. Detailed keeps
  one week per row, with each player spanning For Net, Against Net, and Total
  Net columns. Values come directly from `weekly_for_against`; Total Net is
  For minus Against. Two-row headers, subtle group separators, and lightly
  emphasized totals remain horizontally scrollable on small screens.
- Current Week and Scores GET handlers no longer persist recalculated week
  statuses. Existing result-submission and sync paths still update statuses.

## Pending Railway change — do not apply as part of this work

Read-only inspection confirmed the existing setting:

- Project: `shimmering-communication` (`c3fafbce-9787-4dff-9fe3-f6f56370fc81`).
- Environment: `production` (`6191cc5e-9c57-4fdd-aff6-ba3ee78291d7`).
- Service: `Score Sync Cron` (`b85ffba4-d887-4717-8ca7-b2f6073aa02b`).
- Settings → Deploy → Cron Schedule: change `*/15 * * * *` to `*/5 * * * *`.
- Keep Start Command `python trigger_score_sync.py` and restart policy `NEVER`.
- Keep the existing `SYNC_URL` and `SYNC_SECRET`. `SYNC_SCHEDULE_TIMEZONE` is
  unused by the updated caller and may remain set.
- Keep the source branch `main`; do not point production at this working branch.

The cron setting is not stored in Git. After separately authorized review,
merge/deployment of the caller must accompany the schedule change: changing only
the schedule leaves the old weekday gate active. Until the Railway setting is
changed, the updated caller would run every 15 minutes on all days.

The intended cadence is 288 invocations per day. Before release, check the API
quota and that runs finish before the next five-minute slot. No live sync was
triggered during validation.

## Validation

` .venv\Scripts\python.exe -m unittest discover -s tests -v `:
**132 tests passed**. Added tests cover timezone/DST formatting, an API-driven
reschedule in both displays, pending/correct/incorrect/draw rows, viewer and guest
highlighting, full/HTMX/history tab responses, weekly statistics and rendered
cells, and preservation of saved data even when a saved status is inconsistent
with results.

The first run found one pre-existing Correspondent assertion expecting the old
duplicate-batch error text. The identical failure was reproduced using an
untouched archive of the base commit. Only that test's expected error text was
updated; no Correspondent behavior was changed.

Real-browser checks on a separate local database copy passed for all four tabs,
Back and reload, the weekly detail toggle and columns, Week 1 navigation from the
rollup, kickoff displays, and computed blue/red result colors. No picks or results
were submitted through the browser.

Revised-layout browser checks also passed: signed-in Joe's name alone is yellow;
only team names receive result colors; pending and draw teams and all list rows
have transparent backgrounds. Pick items have zero padding/margin and no status
labels. Both toggle directions work without a URL change. At 390px viewport
width, the detail panel is 318px wide with 1,744px of horizontally scrollable
content and no page-wide horizontal overflow. All six group headers align with
their three sub-columns. Desktop and mobile layouts were visually inspected.

`git diff --check` passed. Existing SQLAlchemy deprecation warnings remain.

## Existing database preservation

Run the repeatable check against a local export or backup:

```powershell
.venv\Scripts\python.exe scripts/verify_render_preservation.py --source C:/Users/sbayd/Downloads/pickem_dbeaver.db --report .venv/pre-kickoff-preservation.json
```

The script opens the source read-only, uses SQLite backup to make a temporary
copy, starts the app in an isolated subprocess, and renders 401 requests across
76 weeks, two seasons, and all six players. Unexpected network connections are
blocked. The source is never supplied to the app. The temporary copy is removed
after comparison.

The verifier also checks every rendered Detailed value for all 76 weeks against
the existing weekly scoring calculations, in the displayed player order.

Before → after row counts:

- players: 6 → 6
- weeks: 76 → 76
- fixtures: 760 → 760
- matchups: 228 → 228
- picks: 1,149 → 1,149
- results: 381 → 381

Every column of every row in those tables was compared in ID order and remained
equal, including pick IDs/team/player/fixture assignments, matchup assignments,
results, week statuses, and historical timestamps. The complete SQLite schema
also remained equal. No model/schema/migration change was introduced. The
pre-existing startup schema routine is unchanged and performed no schema change
on this export.

The source export's file bytes remained identical. See
[the verification report](pre-kickoff-preservation.json) for before/after hashes.
This verifies the existing local export, not a newly downloaded production
snapshot; newer live drafts were not accessed.

## Checks before merge/release

- Check kickoff/date and long team names on the phones used for drafting.
- Review the compact Pick Log with yellow viewer names and blue/red team names.
- Confirm the weekly detailed view is readable on mobile and archived seasons.
- Confirm all production result imports use the existing write/sync paths; page
  views intentionally no longer repair stale week statuses.
- Separately authorize any eventual deployment and the pending cron change.
