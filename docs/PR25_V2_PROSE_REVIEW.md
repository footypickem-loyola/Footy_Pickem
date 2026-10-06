# PR25 v2 controlled live review

This records the initial v2 run. The subsequent prompt-only refinement and its
one-call-per-slate evaluation are recorded in `PR25_FINAL_EDITORIAL_REVIEW.md`.

Two real calls on 2026-10-06, one per required context, using `gpt-5.6-luna`,
`pre-match-brief-v2` and `pre-match-context-v2`. No AI repair pass or selective
regeneration. The local `.env` was loaded into the evaluation process only;
credentials and complete raw outputs are not committed.

**Week 5 passed. Week 2 was rejected for internal vocabulary (“stored”).**
The first validation also exposed a false positive for “Fulham’s Raúl Jiménez”.
The possessive-name parser was corrected and regression-tested. Offline
revalidation of that exact saved response still rejects “stored”; no second
provider call, prose edit or prompt change was used to make it pass.

## Preserved run identities

- Week 2 / player 3: context SHA
  `fe78ded2d51bd22fc8ffa441f452e676c254d56e87ccca04fb9ae11d6a424d5a`;
  provider response `resp_0e50abf924736139016ac4e00f529487d29a7f44afdc929e01`.
- Week 5 / player 2: context SHA
  `53265bf3570d84d6f19d7fb52860e6ef8f7b06eefd089f0685b547c3c48a3d05`;
  provider response `resp_0dae8c60e21e57aa016ac4e01bb1e087d2b1318564d8262414`.

Local artifact directory: `local_data/pr25_live_eval_20261006T114827Z/`.
It preserves exact context/request/provider/structured output, model, prompt and
context versions, hashes, response IDs and per-sentence `used_fact_ids`.
`PR25_V2_LIVE_PROSE_REVIEW.md` and `grounding-review.json` contain full prose and
sentence assessments, including the derived original candidate-ID subset.
The directory is deliberately not checked into Git.

## Grounding and product compliance

Human review classified all **19 body sentences**, including their factual
clauses, as **SUPPORTED**: zero OVERSTATED, UNSUPPORTED or AMBIGUOUS. Four title/
intro units are supported metadata. Twenty-three distinct scorer event IDs
referenced by the used facts were verified directly against active authoritative
SQLite events, including player identity and supporting fixture. No new score,
minute, scorer, appearance, injury, tactical or causal claim was invented.

These are two responses, not a statistically meaningful hallucination rate.
Supported football claims do not imply full product compliance: Week 2's
Chelsea–Brighton sentence uses “three stored league meetings”, so the whole brief
remains rejected. Week 5 satisfies the local contract. Both omit the false
“latest/most recent” assertions seen in v1 and keep introductions metadata-only.

## Requested before/after examples

These are selected excerpts from the preserved responses, not edited replacements.
Week 2 excerpts below belong to a **rejected** brief and are shown for review.

### Leeds–Brentford

Before: “You’ve picked Brentford away at Leeds United.” The remainder explained
that no historical candidates were attached.

After: “Leeds United and Brentford drew 0-0 on 21 March 2026.” It then adds
Brentford's 1–1 at Liverpool on 24 May 2026, naming Kevin Schade as its scorer.

Both facts are SUPPORTED fallback/context facts. Zero PR24 retained candidates
remain zero; no score or threshold was changed to obtain this entry.

### Fulham–Manchester United

Before: the late winners were described by minute without scorer identity.

After: “Manchester United beat Fulham 3-2 on 1 February 2026 after Benjamin Sesko
scored the winner at 90+4.” A separate sentence names Joshua Zirkzee's 87th-minute
winner on 16 August 2024.

Both are SUPPORTED, correctly dated and separately cited to the authoritative
late-goal facts. Names reproduce the provider identities rather than assumptions
about current players.

### Villa–Arsenal

Before: “three goals ... across the last four stored ... meetings” followed by an
appearance disclaimer; the Villa 90+5 winner was wrongly called the latest meeting.

After: “Leandro Trossard scored in three separate league meetings with Aston Villa
between August 2024 and December 2025, registering three goals across those matches.”
The second sentence dates Villa's win to 6 December 2025 and names Emiliano Buendía.

SUPPORTED distinct-meeting recurrence and goal identity. The goal-total clause
is slightly repetitive but does not overstate appearances or roster status.

### Brentford–Chelsea

Before: September 2025's 2–2 was incorrectly called the most recent meeting;
Chelsea's January 2026 win existed in the same context.

After: “Brentford drew 2-2 with Chelsea in the 2025/2026 meeting on 13 September
2025, with Fábio Carvalho scoring the equaliser at 90+3.”

SUPPORTED date, score, player and event. No unqualified recency claim. Season plus
full date is redundant editorially, but factually sound.

## Editorial assessment and recommendation

All ten fixtures now offer football context. Forest–Coventry stays at one sentence;
Leeds–Brentford uses two simple results without invention. No repeated “you picked /
you backed”, betting/fantasy/SEO copy, predictions, tactics, injuries or current-player
assumptions. Metadata intros do not infer season stage.

Selection is not mechanical: Villa leads with recurrence, Sunderland leads with a
fallback result, and Chelsea–Brighton combines Palmer with the three-match pattern.
Remaining repetition is mainly dated-result openings, “four meetings between…”
formulas, and occasional redundant season/date or goal-total wording. United's
three-fact entry could be tighter. These do not justify silently repairing prose.

Keep the v2 architecture and fail-closed protections. **Do not treat this as two
successful accepted briefs or merge yet.** Review the rejected Week 2 with the
user. A possible next prompt refinement is to explicitly paraphrase legacy claim
wording instead of copying audit terms; a second improvement would reduce repeated
season/date templates. Neither was applied after this evaluation.

The source snapshot remains SHA-256
`73c37c2cceff71f360dbf36f55ecadc1a9ad05143ed0cbf93c369794df35bef4`.
No source database, deployment, Railway/n8n or production state was changed.
The final revision passed 416 Python tests and all 10 JavaScript tests.
