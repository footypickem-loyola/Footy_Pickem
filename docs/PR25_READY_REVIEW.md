# PR25 targeted correction — ready for review

Both required live briefs passed the unchanged validator after the targeted
minute-formatting and scoring-club prompt correction. No material factual issue
remains in this two-slate review. This supports marking PR25 ready for human
review, not merging or deploying it.

## Narrow change

The v2 prompt now requires literal `at 90+N` stoppage-time notation and explicit
player/scoring-club attribution for every individual scoring achievement. Its
recurrence example now says Trossard scored **for Arsenal**. No changes to the
writer, strict minute validator, context schema, fallback layer, scorer identities,
provenance, PR24 ranking, cutoffs or database behavior.

Three focused tests cover accepted literal minutes, rejection of both observed
ordinal constructions, and supported Sunderland/Chelsea and opposing Fulham scorer
attribution. Club attribution remains a prompt obligation and human semantic check;
these tests do not claim the lexical validator proves arbitrary club relationships.

## Controlled live run — 6 October 2026

One real generation per slate, `gpt-5.6-luna`, SDK retries disabled. No AI repair,
silent retries, selective regeneration or post-generation prose edits.

- Prompt: `pre-match-brief-v2`, SHA-256
  `6b8cc1142e553661bcc4c02ad6a49e97b41928680b8da06a7f52f7f20646ed9d`.
- Context: `pre-match-context-v2`, unchanged from the earlier v2 evaluations.
- Week 2/player 3: **PASSED**, response
  `resp_07456dda5c8a9ed7016ac4f0908fac87d2af8954e575242028`;
  context SHA `fe78ded2d51bd22fc8ffa441f452e676c254d56e87ccca04fb9ae11d6a424d5a`.
- Week 5/player 2: **PASSED**, response
  `resp_037c8089cec7937d016ac4f09f877487d287b92f730b441b01`;
  context SHA `53265bf3570d84d6f19d7fb52860e6ef8f7b06eefd089f0685b547c3c48a3d05`.

Exact requests, contexts, provider responses, structured outputs, sentence-level
fact IDs, metadata and the full review remain local under
`local_data/pr25_live_eval_20261006T125853Z/`. Its `PR25_READY_REVIEW.md` contains
the full generated briefs and every sentence assessment. Raw responses are not
committed. Earlier failed evaluations remain preserved rather than replaced.

## Grounding and editorial result

**18 body sentences SUPPORTED; zero OVERSTATED, UNSUPPORTED or AMBIGUOUS.** All
four title/intro metadata units are supported. Twenty-five distinct scorer event
IDs carried by cited facts were checked against active authoritative source
player IDs/names and fixtures, including supplied fallback scoring-club IDs.

The ambiguous Sunderland sentence is now explicit:

> Sunderland beat Chelsea 2-1 on 24 May 2026, with Trai Hume scoring for Sunderland and Cole Palmer for Chelsea.

The two formerly rejected goal-minute stories now use the supported notation:

> Fábio Carvalho scored for Brentford at 90+3 to make it 2-2 against Chelsea on 13 September 2025.

> Benjamin Sesko scored for Man United at 90+4 to seal a 3-2 win over Fulham on 1 February 2026.

All specific goal stories identify the scorer and club explicitly or through an
unambiguous club antecedent in the same sentence. No internal/audit language or
unsupported recency appears. Sunderland–Fulham's “previous campaign” is supported
by the 2025/26 source season relative to the 2026/27 context and an exact date;
it does not claim the latest overall meeting.

Every fixture contains meaningful football context. Thin Forest–Coventry remains
one result sentence; zero-retained Leeds–Brentford uses two fallback results with
clear scorer attribution. Palmer, Trossard, Carvalho and Sesko lead player stories;
no entry begins with a full date. Intros remain metadata-only. No appearance-rate,
current-player, injury, tactical, prediction, invented causal or betting/fantasy
claim was found.

Remaining nonblocking style issues: three Week 5 unbeaten sentences share a
formula; Leeds' 0–0 sentence repeats that neither team scored; City naming varies
within a sentence. No prompt change or extra generation was made for these.

This small controlled evaluation does not establish a general hallucination rate
or turn lexical checks into a semantic proof. Human review remains necessary.

## Verification

**419 Python tests and 10 JavaScript tests passed** before live generation.
Both contexts reproduced byte-for-byte; source database SHA-256 remains
`73c37c2cceff71f360dbf36f55ecadc1a9ad05143ed0cbf93c369794df35bef4`.
Unrelated local files were preserved. No merge, deployment, database writes or
Railway/n8n changes.
