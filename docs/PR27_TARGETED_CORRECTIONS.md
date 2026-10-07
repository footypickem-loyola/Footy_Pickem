# PR27 targeted grounding, actor and timing corrections

Continues draft PR27 after `52aef0b`. This is an **offline correctness pass**:
no OpenAI requests, AI repair, output editing, production database access/writes,
deployment, merge, Railway changes or n8n changes. The original live evaluation
and its frozen artifacts remain unchanged.

## Six observed outcomes: cause and correction

1. **Hull false acceptance — historical ranking scope.** The old guard checked
   citations/scorers but did not distinguish a team's last result date from the
   league comparison's observation time. The exact “By 19 September 2026…”
   sentence is now rejected. Writer-only ranking metadata explicitly declares
   `league_ranking_at_cutoff` and the frozen `as_of`; the shared/five-pick prompt
   prohibits combining a historical date/date range with a league ranking.
   Deterministic validation checks both new and preserved contexts using the
   existing ranking signal types. Dated team statistics without the ranking
   remain allowed. No historical rankings are recomputed.
2. **Villa false acceptance — W-D-L arithmetic.** Valid IDs previously sufficed
   even when prose reversed wins/draws. Counts are now checked against the cited
   team's complete `provenance.rows` outcome sequence, or explicit consistent
   structured wins/draws/losses/played totals where rows are absent. W,D,D,D,W
   permits two wins/three draws/zero defeats, not three wins/two draws. Repeated
   facts about the same sample are deduplicated; different samples or an ambiguous
   subject fail closed. Counts cannot borrow the opposing club's sample; clauses
   naming competing subjects fail closed rather than guessing. Supported noun/verb count forms, digits/number words,
   zero/no, once/twice, a win and without a win are handled. Scorelines such as
   “won 1-0” and “a 2-2 draw” are not mistaken for W-D-L counts.
3. **Palace false rejection — club alias.** The exact-match actor guard knew
   Crystal Palace but not Palace. A small explicit canonical alias map now allows
   Palace only for Crystal Palace, with Manchester City/United spellings for the
   existing Man City/United canonical names. No fuzzy or arbitrary substring
   matching was introduced. Unknown player/club substrings still fail.
4. **Coventry false rejection — team pronoun.** “They” was interpreted as a named
   scorer. It is now allowed only for an aggregate with one cited team subject,
   no player scorer facts, and an unambiguous matching team antecedent in the
   immediately preceding sentence. Missing or competing antecedents fail closed.
   The specific-goal requirement still requires the authoritative player's name
   in the same sentence; pronouns cannot replace it.
5. **United false rejection — name particle.** The old regex captured only Ligt
   after lowercase “de”. Actor tokenization now retains legitimate particles
   such as de/van/von/der and compares the full normalized actor against the
   authoritative cited name. The exact Matthijs de Ligt sentence passes.
6. **Tottenham false rejection — same name-particle bug.** Its differently worded
   de Ligt sentence also passes the same fix. An invented player with a similar
   surname or an uncited known scorer still fails; minute, own-goal and specific
   event protections are unchanged.

`correspondent/football_claim_checks.py` contains the narrow deterministic count
and scope checks. `pre_match_writer.py` applies them for both PR25's five-pick and
the shared single-team writer. The only prompt addition is the scope/arithmetic
contract; this is not a broad editorial/style revision. Ranking metadata is added
in the writer projection, not in PR24 candidate generation or scoring.

## Offline replay of the exact twenty preserved outputs

Read the original `.output.txt` and `.context.json` files under
`local_data/pr27_live_20261007T124804Z`. Validate without changing either file or
opening the source/content databases. Per-file SHA-256 comparisons confirmed all
existing local artifacts unchanged. The replay did not generate, repair, edit,
repersist or retry anything.

- Sunderland — accepted, unchanged.
- Brighton Hove — accepted, unchanged.
- Arsenal — accepted, unchanged.
- Leeds United — accepted, unchanged.
- Everton — accepted, unchanged.
- Hull City — **rejected**, sentence 2: historical date attached to cutoff ranking.
- Chelsea — accepted, unchanged.
- Bournemouth — accepted, unchanged.
- Crystal Palace — **accepted**, formerly rejected.
- Nottingham — accepted, unchanged.
- Coventry City — **accepted**, formerly rejected.
- Newcastle — accepted, unchanged.
- Liverpool — accepted, unchanged.
- Man City — accepted, unchanged.
- Fulham — accepted, unchanged.
- Ipswich Town — accepted, unchanged.
- Man United — **accepted**, formerly rejected.
- Tottenham — **accepted**, formerly rejected.
- Aston Villa — **rejected**, sentence 2: Brentford W-D-L count mismatch.
- Brentford — accepted, unchanged.

**18/20 whole outputs accepted; the two known false outputs rejected. All 45
supported sentences accepted; both unsupported sentences rejected. No newly
rejected valid sentence in the preserved batch.** The original store remains
16 succeeded/4 failed: replay validation is not a persistence migration. New
validation on read hides the two invalid stored bodies; the four newly acceptable
raw outputs were not inserted into the store. Do not confuse the replay's 18/2
classification with a new live generation or a completed twenty-brief batch.

During implementation the full suite caught an initial false rejection of the
fallback “Arsenal won 1-0”; replay also caught “without a win” being read as one
win. Both parser edge cases were corrected and have focused regressions. The
reported final totals above include those corrections.

Local-only replay helper/result/log:
`local_data/replay_pr27_corrections.py`, `local_data/pr27-offline-replay.json`,
`local_data/pr27-offline-replay.log`. No raw response or database is committed.
The committed tests reproduce the six observed sentences with minimal explicit
fact samples rather than requiring the private local snapshot.

## Updated 30h / 24h / 18h timing

All boundaries use the earliest actual official kickoff in the ten-fixture round,
including Friday/midweek rounds; there is no day-of-week convention.

- **−30h:** automatic eligibility opens. `generating_before_target` identifies
  safe work before readiness is due.
- **−24h:** primary readiness deadline. All twenty completed at or before this
  instant yields `ready_on_time`. Otherwise status immediately becomes
  `readiness_target_missed_recovering` with `intervention_required=true`.
- **−24h to −18h:** safe recovery may continue. Existing successful entries never
  regenerate; known failures retain their three-attempt cap and 15-minute delay;
  uncertain outcomes never automatically retry. Freshness and schedule gates
  still apply. `generation_allowed=false` plus `blocking_reasons` distinguishes
  a missed target that cannot currently recover from one eligible to proceed.
- **−18h:** hard stop for new automatic calls. An incomplete batch reports
  `hard_window_missed` and requires intervention. All twenty completing after the
  −24h target report `completed_late`, including an already in-flight response
  that completes after the hard stop but passes unchanged lease/kickoff checks.

Status exposes separate `eligible_at`, `target_at`, `hard_stop_at`,
`readiness_target_missed` and `generation_allowed` fields. Schedule errors retain
priority and fail closed; the readiness flag still reports a missed deadline when
the first kickoff is known. Completed-on-time content remains ready after the
hard stop and does not regenerate, even if official sync later becomes stale.

For the evaluated Week 6 first kickoff of **October 10, 2026 at 11:30 UTC**:
eligibility is **October 9 at 05:30 UTC (01:30 EDT)**, readiness is **October 9 at
11:30 UTC (07:30 EDT)**, hard stop is **October 9 at 17:30 UTC (13:30 EDT)**.
These are deterministic schedule calculations, not a claim that a real worker ran
in those windows. Timing/recovery verification uses simulated clocks and local
test databases only. No production monitor was changed.

## Verification and remaining limits

**480 Python tests passed**, including 14 focused grounding regressions and the
updated scheduler suite (30h opening, exact 24h alert, 18h stop, late completion,
on-time immutability, stale-source blocking, safe recovery, uncertain outcomes,
Friday/midweek calculations). **10 JavaScript tests passed.** Commands:

```text
.venv/Scripts/python.exe -m unittest discover -s tests
node --test tests/bulk_picks.test.cjs tests/pick_confirm.test.cjs
```

PR24 ranking/thresholds, kickoff/update cutoffs, scorer provenance, strict minute
validation, canonical storage, retry/fencing rules and game results/scoring/payout
behavior are unchanged. Unrelated local files and `.env` are preserved.

These remain targeted checks, not a general English entailment prover. Dates,
goals-for/against and all conceivable paraphrases are not comprehensively parsed.
Ranking sentences with historical bounds are conservatively rejected rather than
proven against a reconstructed table. W-D-L checks support explicit count forms
over one cited sample; complex nested/subsample comparisons are not a new supported
writing feature. Full-name actor checks do not establish every possible semantic
relationship between a scorer, club and clause. Human/source review is still
required. No new live thin/fallback/top-scorer coverage was created by this replay.

**Recommendation: a second controlled live twenty-brief evaluation is warranted
before merge**, using a fresh authorized snapshot and a new content version.
The prompt hash has changed, so the existing workflow correctly refuses to resume
paid work under an old version with different instructions; neither original
contexts nor stored metadata should be rewritten. A fresh run would test whether
the writer follows the strengthened contract and whether legitimate new phrasing
hits conservative guards. This recommendation is not authorization to call OpenAI.
PR27 remains draft; merge and production activation are not approved. Operational
activation prerequisites in the main guide still apply.
