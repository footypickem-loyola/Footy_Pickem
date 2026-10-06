# PR25 final editorial refinement — 6 October 2026

**Not ready for acceptance sign-off: Week 2 passed; Week 5 was rejected.**
The final change adds 23 lines to the v2 prompt, emphasizing translation of
internal terminology, varied football-led openings, and removal of redundant
season/date and scoring-total phrasing. No validator, context, ranking, fallback,
scorer, provenance, cutoff or database code changed.

## Controlled evaluation

Exactly one real generation per slate, `gpt-5.6-luna`, with SDK transport retries
disabled for this evaluation. No silent retries, AI repairs or regeneration.
Versions remain `pre-match-brief-v2` and `pre-match-context-v2`; the exact revised
prompt is identified by SHA-256:
`684c3d5866436c47854f288f9c84ab3b315b71573dbb5f70891580e7099a7f83`.

- Week 2/player 3: **PASSED**. Response
  `resp_0dab4098ed1559cf016ac4e60aa6dc87d294f0509d2409ef43`.
  Context SHA `fe78ded2d51bd22fc8ffa441f452e676c254d56e87ccca04fb9ae11d6a424d5a`.
- Week 5/player 2: **REJECTED**. Response
  `resp_0a13c02c0558b5ff016ac4e61ae6ec87d2891e74afe8962ef2`.
  Context SHA `53265bf3570d84d6f19d7fb52860e6ef8f7b06eefd089f0685b547c3c48a3d05`.

The rejected response used “90+3rd minute” for Fábio Carvalho and “90+4th-minute”
for Benjamin Sesko. Their football meaning matches the named 90+3 and 90+4
events, but the unchanged validator interprets the ordinal suffix as a standalone
third/fourth minute and rejects it. Validation was not weakened or edited.

Full exact requests, contexts, provider responses, structured outputs, citations,
metadata and sentence-level review remain locally under
`local_data/pr25_live_eval_20261006T121359Z/`. The complete report is
`PR25_FINAL_EDITORIAL_REVIEW.md` in that directory. Raw responses are not committed.

## Acceptance checks

- Both pass validation: **NO**, as detailed above.
- Meaningful football in every fixture: **YES**, including both zero-retained
  fixtures using unchanged fallback context.
- Factual grounding: **16 SUPPORTED, 1 AMBIGUOUS**, zero OVERSTATED/UNSUPPORTED
  body sentences. All four title/intro metadata units are supported.
- Named specific goal events and fixture-local citations: **YES**. Twenty-five
  distinct scorer event IDs in cited facts were verified directly against source
  player IDs/names and supporting reference fixture IDs.
- No internal/audit terminology: **YES**, neither output uses it.
- No unsupported recency language: **YES**, claims stay dated or bounded.
- Reasonable variety: **improved**, though three Week 5 sentences repeat the
  “unbeaten in four league meetings” construction.

The ambiguous sentence is: “Sunderland’s 2-1 win over Chelsea on 24 May 2026
featured goals from Trai Hume and Cole Palmer.” Both scored in that match, but
Palmer scored for Chelsea. The sentence can imply both were Sunderland scorers;
explicit club attribution would prevent that reading. This is a human editorial
finding even though Week 2 passed runtime validation.

## Editorial findings

Palmer, Trossard and Sesko lead entries with player stories; only one entry starts
with a full date. The Trossard sequence no longer repeats its aggregate goal total.
No redundant season-plus-full-date pairings or technical appearance disclaimers
were found. Thin fixtures remain concise, and no invented football detail fills
an empty entry. Forest's two-club scorer list is accurate but would also read more
clearly with each club named.

Chelsea–Brighton describes De Cuyper's 90+2 goal as sealing the win. The cited
event is the decisive goal for 1–2, with Welbeck later making it 1–3; the wording
is supported as a description of the winner, not the final goal. No injuries,
tactical claims, predictions, current-player assumptions, betting/fantasy copy
or invented causal claims were found.

Keep the PR draft and unmerged. The requested all-pass outcome is not achieved.
A further authorized refinement could require literal `90+N` notation and explicit
club attribution when naming scorers from both teams. Neither change nor another
generation was made after this review.

## Verification

**416 Python tests and 10 JavaScript tests passed.** The application diff for this
refinement is only the prompt. Contexts and source snapshot are unchanged; source
SHA-256 is `73c37c2cceff71f360dbf36f55ecadc1a9ad05143ed0cbf93c369794df35bef4`.
No merge, deployment, database writes, Railway/n8n or unrelated local changes.
