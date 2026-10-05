# PR24 — Fixture intelligence quality improvements

The unchanged PR23 benchmark shows better top-three coverage with fewer retained
facts: **71 instead of 88**. Primary coverage rises from **1/10 to 2/10**;
supplemental top-three coverage rises from **7/14 to 10/14**. This is a focused
improvement on the same seed, not an independent holdout evaluation.

## Code and rules

Only two application source files change:

- `historical_fixture_intelligence.py`: exact positive scoring sequences and two
  historical score components; version `historical-fixture-intelligence-v2`.
- `fixture_intelligence.py`: H2H story suppression only; version
  `fixture-intelligence-v3`. Current-season detectors and the threshold of 60
  are unchanged.

`H2H_EXACT_GOALS_SEQUENCE` scans the newest consecutive stored H2H meetings,
across both venues, separately for each subject team. It needs at least three
identical positive goal totals. A different total ends the sequence; existing
history gaps stop traversal. Zero-goal sequences are not emitted. The existing
five-meeting window, completed-result validation and strict kickoff cutoff are
unchanged. Supporting fixture IDs/dates, count and lower-bound status are retained.
Strength is `min(64, 26 + 6*(count-3) + 8*min(goals_each,4))`, with rarity 8 for
three or more goals per meeting, otherwise 0; existing relevance and recency
components still apply. These are editorial heuristics, not league-wide rarity.
Three one-goal meetings can remain below threshold; new patterns do not
automatically outrank event stories.

H2H suppression applies only to eligible historical result runs. It normalizes
mirrored team perspectives to the advantaged club, with a separate all-draw
angle. Same-angle runs with overlapping evidence compete when one evidence
window contains the other, or when a two-meeting venue run overlaps a broader
overall run of at least three meetings. Prefer the larger window; then concrete
winning/losing rather than unbeaten/winless; then score, metric order and stable
ID. Thus win/loss beats generic phrasing over equal evidence, while a short venue
run cannot displace a longer overall story. Different angles and independent
two-game venue sequences remain eligible. Suppressed raw candidates retain
`redundant_h2h_story` and the representative's ID. This is editorial overlap
suppression, not a claim that all the underlying propositions are identical.

Prior-result anchors receive a `scoreline_interest` component:

- **Notable:** margin at least three goals **or** at least five total goals;
  adjustment 0, preserving the existing score.
- **Ordinary nonzero:** all other nonzero scorelines; adjustment -24.
- **0–0:** adjustment -32.

With the usual recent/same-venue relevance this gives 85, 61 and 53 respectively.
Raw context is always retained. Existing event-over-anchor suppression still
applies when the stronger event covers the same meeting. Recency can leave older
braces below ordinary recent anchors; no blanket brace boost was added.

Existing `PLAYER_VS_OPPONENT` candidates receive
`distinct_scoring_meetings = 22 + 4*(meetings-3)` only at three or more distinct
scoring meetings, otherwise 0. Goal counts, distinct scoring meetings and total
stored meetings remain separate evidence fields. Existing complete-goal-ledger
validation, player IDs, own-goal exclusion and event-set deduplication are
unchanged. Claim text remains the existing aggregate text; the recurrence is
explicit in structured evidence and the score breakdown. No appearances, rates,
current roster, availability or never-scored assertions are introduced.

## Unchanged benchmark

Baseline: merged PR23 `d03812d`. Same season 2, all 40 fixtures in weeks 2–5,
same whole-week cutoffs, primary gold, supplemental seed, and 92 review labels.
The PR23 harness, seeds, candidate-review metadata and report are untouched.
All **377 existing candidate IDs and factual evidence are preserved**. Four new
exact-goal candidates are added; three are retained. The new candidates have no
inherited human review labels. The compact `pr24_quality_summary.json` contains
metrics, comparisons and representative findings; raw packets stay local.

| Metric | PR23 before | PR24 after |
| --- | ---: | ---: |
| Raw candidates | 377 | 381 |
| Retained candidates | 88 | 71 |
| Primary raw / retained / top-3 / top-5 | 1 / 1 / 1 / 1 of 10 | 2 / 2 / 2 / 2 of 10 |
| Primary supported-data-only coverage | 1/2 | 2/2 |
| Supplemental raw | 13/14 | 13/14 |
| Supplemental retained | 10/14 | 10/14 |
| Supplemental top-3 | 7/14 | 10/14 |
| Supplemental top-5 | 10/14 | 10/14 |
| H2H duplication proxy | 4/88 (4.55%) | 0/71 (0%) |
| Fixtures with zero retained | 7 | 11 |
| Fixtures with zero score-80+ | 8 | 16 |

Primary coverage excludes five DATA UNAVAILABLE AT REPLAY cases; supplemental
coverage excludes six. The October 5 snapshot cannot prove September official
result availability: current-season signals remain **NOT EVALUABLE FROM AVAILABLE
SNAPSHOT**. `Result.updated_at` rules are unchanged. Historical reference replay
uses pre-cutoff fixture kickoffs, not a claim that this snapshot is an original
as-known-at-the-time archive. No timestamps were reconstructed or modified.

“Affected” below means score, rank or retained status changed; it does not mean
the manual label changed. Rank changes include gains and losses.

| Fixed label | Reviewed | Affected | Retained before → after | Removed / newly retained |
| --- | ---: | ---: | ---: | ---: |
| EXCELLENT | 12 | 1 | 12 → 12 | 0 / 0 |
| USEFUL | 48 | 27 | 46 → 45 | 2 / 1 |
| MARGINAL | 26 | 20 | 25 → 11 | 14 / 0 |
| NOISY | 6 | 5 | 5 → 0 | 5 / 0 |

## Representative fixture changes

1. **Chelsea–Brighton (777):** Palmer's four goals (96) and Brighton's 90+2 winner
   (94) remain first and second. Brighton's exact-three sequence enters third at
   82, ahead of its three-win run (73) and Welbeck's brace (63). Evidence:
   19134574, Brighton 3–0 Chelsea, 2025-02-14; 19427507, Chelsea 1–3 Brighton,
   2025-09-27; 19427198, Brighton 3–0 Chelsea, 2026-04-21. No 1974 record is inferred.
2. **Palace–City (771):** the notable 0–3 anchor (85) and City's four-meeting
   unbeaten run (82) remain first and second. The overlapping three-win run and
   Palace two-home-game winless run are suppressed. Haaland's brace moves from
   fifth to third, still 63; it was not given a score boost.
3. **Brentford–Chelsea (801):** the 90+3 equalizer (83), Chelsea's four-meeting
   unbeaten run (82), and Brentford's two-home-game unbeaten run (64) remain.
   The equivalent two-home-game winless headline is suppressed. Four slots become
   three; both underlying draw results and both raw formulations remain inspectable.
4. **Leeds–Brentford (778):** the sole 0–0 anchor falls from 85 to 53 and is filtered.
   The packet has no retained fact; the prior score remains available as raw context.
5. **Spurs–Villa (802):** the ordinary prior 1–2 falls from 85 to 61, still the sole
   retained fact. Solanke's older brace stays at 51 and remains below threshold.
   This venue/recency limitation is deliberately not solved by promoting all braces.
6. **Villa–Arsenal (780):** Villa's 90+5 winner remains first at 94. Trossard's three
   goals in **three distinct meetings out of four stored meetings** rise from raw-only
   56 to second at 78. Of 237 player aggregates, only this one qualifies for the new
   recurrence bonus and retention; the other 236 scores are unchanged.
7. **Liverpool–Forest (772), guardrail:** Forest's two Anfield wins remain represented
   by Liverpool's two home losses, third at 68. The late winner (86) and notable
   prior 0–3 (85) are unchanged.

## Tradeoffs and remaining limits

- The unchanged supplemental two-draw seed requires a bundle of both unbeaten and
  winless candidates. Removing the redundant slot turns that retained hit into a
  suppression miss, although raw coverage remains. Trossard supplies the replacement
  retained hit. No matcher or seed was changed to conceal this loss.
- Two previously USEFUL anchors leave retention: Sunderland 1–3 Fulham (776) and
  Villa 3–1 Forest (793) are covered by their now-higher brace candidates.
  Both raw facts remain. Four more fixtures have no retained signal; reduced
  score-80+ counts mainly reflect anchor calibration, not a human-quality verdict.
- All 12 reviewed EXCELLENT facts remain. All three hat-trick/four-goal candidates
  and all 20 late-decisive candidates retain identical IDs, evidence, scores and
  retained status. Their ranks can change as surrounding context changes.
- Broader H2H representatives can hide a narrower winning or venue distinction.
  This is intentional slot suppression with audit trails, not deletion of facts.
- The duplication measure is PR23's conservative proxy, not proof of no semantic
  overlap. Positive results on the same small seed still need a later holdout.

## Reproduce and validate

Run the unchanged evaluator below at baseline `d03812d` in a separate checkout for
`pr24_before.json`, then on this branch for `pr24_after.json`. Use an absolute path
to the same disposable snapshot when running from another checkout; do not run an
importer. Only the output path differs between runs.

```powershell
.venv\Scripts\python.exe scripts/evaluate_fixture_intelligence.py --db local_data/pr23_eval.db --season 2 --week 2 --week 3 --week 4 --week 5 --gold tests/fixtures/fixture-intelligence-gold-standard.json --supplemental-gold tests/fixtures/fixture-intelligence-snapshot-seed.json --reviews docs/pr23_candidate_reviews.json --output local_data/pr24_after.json
.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests/bulk_picks.test.cjs tests/pick_confirm.test.cjs
```

**379 Python tests and 10 JavaScript tests passed.** Eleven new regressions cover
the real Brighton scorelines, positive sequences of three/four, interruption,
gaps, zero exclusion, both orientations, exact cutoff, two-draw suppression,
Forest-style venue losses, overlapping runs, ordinary/notable anchors, three
distinct scoring meetings, own goals, one-game hat-tricks and deterministic order.
Existing read-only replay, future mutation, complete scorer evidence, appearance
limitations and event tests also pass. Repeated after runs are byte-identical.

Original source snapshot SHA-256:
`73c37c2cceff71f360dbf36f55ecadc1a9ad05143ed0cbf93c369794df35bef4`.
Disposable evaluation copy SHA-256:
`3defd82174491eea33199a0a61450896e122e7221fd845f51505fb1dd4eb6aae`.
Both remain unchanged. No source database writes, production writes, deployment,
Railway changes, importer changes, UI, AI writer, endpoints or external sources.
No full raw evaluation JSON is committed.
