# PR23 — Fixture intelligence retrospective evaluation

The historical engine finds memorable events and useful result anchors. Its
weaker output is small-sample venue H2H material. More importantly, this snapshot
cannot establish retrospective current-season form: all official results were
updated after the selected cutoffs. **Do not interpret this evaluation as a
successful audit of the combined current-form and historical stack.** The
read-only harness and the historical review are complete; the current-form
empirical review needs suitable availability evidence.

## Dataset, slates, and cutoff

Source: the supplied `local_data/pickem_with_reference_snapshot.db`, copied with
SQLite's backup API to disposable `local_data/pr23_eval.db`. The source was opened
with `mode=ro` and `query_only=ON`. The evaluator likewise uses `mode=ro`, query-only,
and one consistent read transaction. It imports no Flask code and writes no database.
The original snapshot SHA-256 remained:
`73c37c2cceff71f360dbf36f55ecadc1a9ad05143ed0cbf93c369794df35bef4`.

Official database season **2 (2026/27)**; **four full matchweeks, 40 fixtures**:

- Week 2: August 28–31; cutoff **2026-08-27 19:00 UTC**; IDs 771–780.
  109 raw candidates, 23 retained.
- Week 3: September 4–6; cutoff **2026-09-03 19:00 UTC**; IDs 781–790.
  88 raw candidates, 24 retained.
- Week 4: September 12–14; cutoff **2026-09-11 14:00 UTC**; IDs 791–800.
  94 raw candidates, 22 retained.
- Week 5: September 18–20; cutoff **2026-09-17 19:00 UTC**; IDs 801–810.
  86 raw candidates, 19 retained.

Selection covers all locally completed slates after the opening week, rather
than selecting only high-scoring packets. Each slate has exactly ten fixtures
and twenty distinct teams. Some weeks contain memorable prior meetings; others
expose thin-history cases. The snapshot does not contain later completed 2026/27
slates or dated 2025/26 official matchweeks; neither is fabricated.

**Rule:** one shared cutoff, 24 hours before the earliest kickoff of the entire
matchweek. Partial fixture/date selections retain that same whole-week cutoff.
An explicit `--as-of` must precede the whole week's first kickoff. Target-week
results are removed before calling PR19, and results from later kickoffs are
not passed to the engine. PR19 still requires `result.updated_at < cutoff`.
PR21 still requires reference match kickoff strictly before the same cutoff.
Fixtures/events at or after the cutoff cannot provide result evidence.

All 50 current-season result rows have `updated_at` on **2026-10-05**, so none is
eligible in these August/September replays. The user confirmed that no earlier
authoritative snapshot is available. The harness does not backdate them,
substitute reference results into the official ledger, or disable availability
checks. Every fixture packet reports **NOT EVALUABLE FROM AVAILABLE SNAPSHOT**.
Five primary and six supplemental current-form stories are **DATA UNAVAILABLE AT
REPLAY**, excluded from their respective coverage denominators and detector miss
counts. They are not engine misses. No reconstructed/exploratory scores are mixed in.
Even with earlier timestamps, these opening slates would be too early to assess
five-game venue windows reliably. Obtain later archived slates before judging
those families.

The historical reference snapshot contains 2024/25 and 2025/26. As PR21 documents,
these are latest corrected records of earlier matches, not an as-known-at-time
revision archive. Their later ingestion timestamps are provenance; match time
controls inclusion. This evaluation makes that limitation explicit.

## Reproduce and inspect

After making a disposable SQLite backup of the local source:

```powershell
.venv\Scripts\python.exe scripts/evaluate_fixture_intelligence.py `
  --db local_data/pr23_eval.db --season 2 `
  --week 2 --week 3 --week 4 --week 5 `
  --gold tests/fixtures/fixture-intelligence-gold-standard.json `
  --supplemental-gold tests/fixtures/fixture-intelligence-snapshot-seed.json `
  --reviews docs/pr23_candidate_reviews.json `
  --output docs/pr23_retrospective_eval.json
```

Alternatively select repeated `--fixture-id`, or `--start` / `--end` (exclusive).
`--season` is the official SQLite season ID, not Sportmonks season 28083.
The JSON contains all raw and ranked candidates, scores and components, evidence
IDs, sample dates, source rows/events, history, exclusions, suppression decisions,
top-five review summaries, gold comparisons, and metrics. Suppressed IDs resolve
to complete raw candidates in the same packet. Output cannot overwrite the input
database or metadata, including via a hard link. Without `--output`, JSON goes
to stdout. No wall-clock generation timestamp or machine-specific path is emitted.

## Gold comparison means equivalent football facts

The primary fixture contains the **14 user-supplied Squawka-style examples,
split into 21 atomic stories** so a computable fact and its deeper historical
superlative are not scored as one indivisible claim. They are semantic targets,
not verified quotations, and are not exhaustive. Fifteen stories have an explicit
fixture/pair/context binding in weeks 2–5. Five lack a precise opponent/date or
subsequent-preview target; the Newcastle–Liverpool example maps to week 1 outside
this selection. These **six unscored targets** remain in the artifact with binding
notes and likely capability requirements; they are not silently discarded or
assigned to convenient fixtures.

Binding notes disclose interpretation: the Brighton scoring sequence is
venue-independent, so the Brighton–Chelsea heading can be evaluated at
Chelsea–Brighton (777). The Arsenal opening-away condition maps to Villa–Arsenal
(780), despite the example's Arsenal–Villa heading. Team-context probes are not
claims that an unspecified original publication date has been recovered.

A separate **20-item snapshot-curated supplemental seed** measures examples
already observed in the snapshot. It was curated after inspecting engine output
and is not independent. Its comparisons and metrics are emitted under
`supplemental_benchmark` and are never pooled with the supplied primary seed.

Each item records fixture identity, claim, category, provenance, structured
equivalents, and a reason/rationale to use if no candidate exists. Each alternative
is a bundle of selectors: signal type, optional subject/venue, evidence/sample
values, and supporting external fixture IDs. Every member of a bundle must match.
Nested fact subsets and explicit numeric `at_least` constraints are supported.
Claim text is never compared. A category alone is rejected as too broad.

Examples of equivalence:

- “Four victories from five” can match a win-frequency candidate or a 4W/0D/1L
  form record with sample size five. It does **not** match four consecutive wins.
- Brighton's previous 3–1 win at Chelsea counts through the retained late-goal
  candidate's final score and fixture ID, even though the ordinary score anchor
  was suppressed. This avoids a false suppression miss caused by wording.
- Two Brentford–Chelsea home draws count through the two-match unbeaten **and**
  winless facts with the same two evidence fixtures. The football story is covered,
  even though its presentation is unnecessarily fragmented.

Forest winning twice at Anfield also matches Liverpool losing those same two
home meetings. Conversely, Haaland scoring three goals across four stored matches
does **not** establish scoring in each of his career-first five meetings; absent
goals do not establish appearances or a “never scored against” exception.

Equivalence is declared and auditable, not general natural-language understanding.
Different story emphasis can still require editorial judgment. The “eleven goals
across two Bournemouth–Liverpool meetings” item is deliberately classified as a
missing explicit aggregate: both scorelines exist in late-winner evidence, but
there is no pair-level high-scoring claim. Its miss reason is a **curated
hypothesis**, not proof that a reader could not derive the total. Reclassifying
that one supplemental item as an equivalent bundle would move supplemental
coverage from 10/14 (71.43%) to 11/14 (78.57%), after replay exclusions. It does not
affect primary coverage.

Comparisons record raw equivalents, retained equivalents, candidate IDs, rank,
top-three/top-five coverage, eligibility and one of **SURFACED**, **ENGINE MISS**,
**DATA UNAVAILABLE AT REPLAY**, **HISTORICAL DEPTH INSUFFICIENT**, or **EXTERNAL DATA
REQUIRED**. Unresolved/out-of-selection items are unscored. Threshold/suppression
classifications come from engine decisions. Absent-candidate classifications are
review hypotheses with rationales. A fact retained below position five is a
top-five `ranking_too_low` miss, but still counts in all-ranked coverage.

## Diagnostics, not model accuracy

- **377 raw candidates; 88 retained**, all of them within their fixture's top five.
- Primary: **21 stories = 15 bound + 6 unscored**. Of the 15 bound stories,
  **5 DATA UNAVAILABLE AT REPLAY cases are excluded**, leaving denominator **10**.
  Raw/retained/top-three/top-five coverage are each **1/10 (10%)**. This is broad
  capability coverage, not a claim that nine supported facts were mishandled.
- Primary classifications: **1 surfaced**, **1 supported engine miss** (Brighton's
  repeated three-goal pattern), **7 historical-depth limits**, **1 external-data
  requirement** (Raya), plus the **5 excluded replay-unavailable cases**.
  Among only the two data-supported primary stories, coverage is **1/2 (50%)**.
  Both denominators are exposed; neither is statistically meaningful accuracy.
- Supplemental: **20 bound stories, 6 excluded**, denominator **14**. Raw
  equivalents **13/14 (92.86%)**; retained/top-five **10/14 (71.43%)**;
  top-three **7/14 (50%)**. Four scored misses: **3 threshold**, **1 missing explicit
  aggregate**. Six unavailable cases do not count as detector misses.
  There were no retained facts below rank five in either seed.
- **7/40 fixtures had no retained signals**. **8/40 had no score-80+ signal**;
  the extra fixture was Ipswich–Liverpool with its score-77 historical anchor.
  “Strong” here is only an engine-score proxy, not an editorial quality verdict.
- Shared-story duplication proxy: **4 flagged later slots / 88 top-five slots
  (4.55%)**. It flags same-team, related run metrics with nested evidence windows.
  It can flag a useful refinement and can miss broader thematic repetition;
  it does not change ranking. Manual noise labels need not equal this proxy.
- Provisional review labels cover **all 88 ranked facts plus 4 selected suppressed
  facts**: EXCELLENT **12 (13.04%)**, USEFUL **48 (52.17%)**, MARGINAL
  **26 (28.26%)**, NOISY **6 (6.52%)**, WRONG_OR_MISLEADING **0**.
  The remaining **285 raw facts are unreviewed**. These are Codex-assisted
  editorial judgments, not user-approved or blinded human ratings. Zero “wrong”
  labels is not an independent factual audit.

The optional review schema requires fixture/candidate IDs, one label, recognized
reason tags and a rationale. Labels live only in `docs/pr23_candidate_reviews.json`;
they cannot affect detectors, thresholds, ranking or equivalence. Stale candidate
IDs fail validation instead of silently attaching a judgment to another fact.

## Supplied target disposition

1. **Haaland first five / sixth-meeting milestone:** historical depth insufficient
   for both. First-career ordering, complete scoring meetings and league-wide
   player ranking are absent. Existing goal totals do not prove the story.
2. **Forest's two Anfield wins:** surfaced, **rank 3, score 68**, through Liverpool's
   two home defeats. There is no generic overall H2H run in this packet to compare
   it against; it sits below a late winner and the prior score anchor.
3. **Brighton's three goals in three meetings:** ENGINE MISS, supported by fixture
   IDs **19134574 / 19427507 / 19427198** (3–0, 3–1, 3–0 from Brighton's perspective).
   Their three-win run is a different fact. The conditional fourth/West Ham 1974
   record is separately historical-depth-insufficient.
4. **Arsenal four away openers / fifth club record:** historical-depth-insufficient;
   **Raya approaching 50 clean sheets:** external player/appearance data required.
   Team clean sheets cannot be automatically credited to a goalkeeper.
5. **Chelsea 21-game clean-sheet drought:** DATA UNAVAILABLE AT REPLAY; excluded.
   Also needs cross-season sequence continuity, so later data restoration should
   not be mistaken for proof that current-season-only PR19 already handles it.
6. **Arsenal nine wins / tenth conditional / five-from-five start:** three
   unavailable replay cases, all excluded. Four current-season wins are not a
   nine-game cross-season run, but a four-win season-start fact can cover the
   underlying perfect-start story without copying milestone wording.
7. **Spurs opening four scoreless:** unavailable and excluded; **worst since
   2008/09:** separately requires deeper history.
8. **Hull promoted-side start:** historical depth/promotion baseline insufficient;
   the supplied games-played window is unspecified and not invented.
9. **Haaland's never-scored-against exception:** unscored unresolved opponent/date;
   requires appearance coverage and complete opponent history. Never infer it
   from a missing goal event.
10. **Isak matching last season's total:** unscored unresolved season/date;
    player-season totals and competition scope are required.
11. **United worst-ever start:** unscored unresolved matchweek/window, with a
    deep-history comparison requirement.
12. **Newcastle–Liverpool 202 versus 211 goals:** fixture 769 is outside the
    selected slates; unscored, historical-depth-insufficient in any case.
13. **Liverpool 14 opening-week games unbeaten:** unresolved subsequent-preview
    target and insufficient history. Its already-extended wording cannot justify
    leaking an opener's result into its own pre-match packet.
14. **Wissa performance line:** unscored unresolved source match/later preview;
    external match statistics and match-wide comparisons required. Post-match
    duels/passes/tackles must not become pre-match facts for the same fixture.

## Detector audit and product questions

**Current-season families:** winning/unbeaten/losing/winless, clean-sheet/drought,
scoring/drought, last-five WDL, rolling frequencies, recent goals, home/away form,
home/away split, overall-vs-venue contrast, league defensive extremes, season-start
and continuation all produced **0 eligible raw candidates** here. This is an
availability limitation, not evidence of quality or noise. Home/away form and
contrast retain their existing regression coverage; their real-slate usefulness
is **not measured**. League extremes are neither proven too common nor too weak.
Likewise this set cannot establish whether 2/5 or 3/5 facts cause real preview
noise. Existing 2/5 threshold regressions remain green; 3/5 needs later-slate
editorial review, not a scoring change inferred from this empty sample.

**Exact prior-season fixture:** 33 raw, 26 retained; 16 USEFUL and 10 MARGINAL.
Useful orientation/date anchors, but ordinary 0–0 or routine 1–0 results score
81–85 and can outrank a distinctive brace at 63. Seven anchors were suppressed
by event coverage. Keep the factual anchor available while reviewing its headline
priority; do not measure suppressed text alone as lost football information.

**H2H runs:** 30 raw, 13 retained; 11 USEFUL, 1 MARGINAL, 1 NOISY.
**Venue H2H:** 25 raw, 17 retained; 1 USEFUL, 12 MARGINAL, 4 NOISY. Together they occupy
30/88 retained slots (34.1%). Small two-meeting venue runs are the clearest noise
source in this limited sample. Palace–City presents a four-match unbeaten run,
three-match winning run and Palace home winless run above Haaland's brace.
The broad H2H story is valid, but three variations consume scarce headline space.
Forest's two Anfield wins are the useful counterexample to a blanket rule that
every two-game venue sequence is noise.

**Braces/hat-tricks:** 28 raw, 11 retained (3 hat-trick/four-goal facts and 8
braces). All three hat-tricks rank first, scores 96–100, and received EXCELLENT
labels. All eight retained braces received USEFUL labels; an additional reviewed
suppressed brace was USEFUL. Braces can rank too weakly relative to generic H2H
or plain score anchors. There is no evidence that hat-tricks need a blanket boost.

**Late decisive goals:** 20 raw, all 20 retained across **17/40 fixtures**;
9 EXCELLENT, 9 USEFUL, 2 MARGINAL. These are strong specific stories, but not rare
within this evaluation's two-season lookback. Older late-goal repeats and opposite-
venue events need clear match context. Avoid claiming statistical rarity from a
high heuristic rarity score. The timeline reconciliation remains valuable.

**Player-v-opponent totals:** 237 raw, **0 retained**. The reviewed Trossard
three-goals-in-three-distinct-meetings story scores 56 and is USEFUL; Haaland's
three-goal total at 56 is MARGINAL because a brace is already retained. Palmer's
four-goal aggregate is correctly suppressed as the same four event IDs as the
four-goal game. Do not globally promote generic aggregates. Recurrence across
different meetings is a better review target. Appearances and present club/roster
membership remain unverified; these are historical totals, never current-player
availability or per-appearance rates.

**Red-card context:** 4 raw, 1 retained. Chelsea's second-yellow dismissal in
the latest stored Arsenal meeting ranks fourth, score 62, and is USEFUL context.
That small sample does not support a broader conclusion about the family.

**Human story but no packet:** Coventry–Brighton (798) has no retained candidate,
although the snapshot shows Coventry had failed to score in all three prior
league games. Hull–Villa (788) similarly has two opening Hull wins in the official
snapshot but no usable packet. Their update times disqualify those stories and
the stored two-season PL reference sample supplies no fallback. The other empty
fixtures are 774, 784, 795, 806 and 808. Do not fill these gaps with invented
all-time/promoted-team comparisons.

## Ten representative review examples

1. **Chelsea–Brighton (777):** Palmer's four goals in the stored 2024/25 meeting;
   rank 1, score 96, EXCELLENT. Gold matched player ID, four goals and fixture
   19134496, without matching prose.
2. **Newcastle–Bournemouth (782):** Kluivert's three goals in fixture 19134620;
   rank 1, score 96, EXCELLENT. Specific remembered opponent story.
3. **Villa–Arsenal (780):** Villa's 90+5 winner, final score 2–1, fixture
   19427596; rank 1, score 94, EXCELLENT.
4. **Palace–City (771):** Haaland's brace in fixture 19427610 ranks only fifth,
   score 63, USEFUL; multiple similar H2H angles rank above it.
5. **Leeds–Brentford (778):** the previous 0–0 is the sole ranked fact,
   score 85, MARGINAL. High score is not necessarily a strong editorial story.
6. **Brentford–Chelsea (801):** separate two-home-game unbeaten and winless
   claims rank third/fourth, score 64 each. The equivalent story is “two draws”;
   semantic coverage succeeds, but the latter slot is NOISY.
7. **Spurs–Villa (802):** Solanke's brace in the 2024/25 home meeting,
   fixture 19134877, exists raw at score 51 and is removed by the threshold.
   Provisional USEFUL label; worth reviewing venue/recency scoring.
8. **Villa–Arsenal (780):** Trossard's three goals in three of four stored
   meetings exists raw at 56, below the threshold. A recurring scoring story is
   lost alongside many genuinely generic player totals.
9. **Spurs–Villa (802):** the opening four scoreless matches, supported by
   official fixtures 766/775/783/796, cannot be surfaced at the September 17
   cutoff because the results were updated October 5. Excluded from coverage;
   the separate “since 2008/09” comparison requires deeper history.
10. **Chelsea–Brighton (777):** Brighton scored exactly three in each of the last
    three stored meetings. The engine has a three-win run but no exact-score
    sequence. This is the primary seed's clearest supported detector gap.

## Ranked next steps — recommendations only

1. **Preserve result availability/revisions and archive preview-time snapshots.**
   Recover a real pre-cutoff ledger and evaluate later complete slates before
   changing current-form scores. Current-season reference synchronization alone
   does not restore the original official result availability timestamps.
2. **Evaluate exact-score-pattern sequences.** Brighton's three consecutive
   three-goal games are supported by existing reference rows and missed as a story.
   Measure this detector on independent examples; keep the 1974 conditional
   record blocked until its historical comparison data exists.
3. **Review small-sample H2H redundancy and venue-brace priority.** Compare one
   concise H2H angle with the strongest event story. Re-run this unchanged seed
   plus an independent holdout to test whether useful facts move into the top three.
4. **Retain exact date, venue and result context when an event replaces an anchor.**
   The evidence is present, but compact claim text can obscure which meeting is
   meant. Review selection/presentation contracts before introducing prose.
5. **Evaluate repeated player scoring across distinct meetings.** Distinguish
   Trossard's multi-meeting pattern from an aggregate that duplicates one brace.
   Add roster/appearance evidence before making current-player assertions.
6. **Assess explicit high-scoring H2H and current-season reverse-meeting summaries.**
   The former has an auditable seed example. The latter is a prospective evaluation
   gap: these opening slates contain no current-season reverse fixtures, so this
   set supplies no measured need or success rate for that detector.
7. **Expand data/holdout coverage before milestones or external context.** Later
   league scoring/conceding ranks, promoted-team starts and conditional records
   need complete comparable samples. “Never”/“first since” requires deeper history;
   manager, injury and tactical context requires other sources. None is inferred
   from this two-season dataset, and none is implemented here.

No detector, ranking policy, importer, production endpoint, UI, Railway service,
or official/reference database content was changed. **368 Python tests and 10
JavaScript tests passed**. Repeated artifact generation was byte-identical; source
and disposable database hashes were unchanged. The new tests cover selection, determinism, read-only
access, strict cutoff, future result/event mutation, schema validation, semantic
equivalents, miss reasons, metrics, empty packets and malformed inputs.
