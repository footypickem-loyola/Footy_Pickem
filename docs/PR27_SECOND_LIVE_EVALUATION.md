# PR27 second controlled twenty-brief live evaluation

Historical run at `2aed35e`, reported in `3b896fc`. The subsequent
[final offline corrections](PR27_FINAL_CORRECTIONS.md) close the three observed
validator/grounding issues and recommend merge review without a third paid run.
The original outputs and evaluation findings below remain unchanged.

**NOT READY FOR MERGE. NOT READY FOR ACTIVATION.** The second run improved to 18/20
validator passes, but two supported briefs are still rejected and an accepted
Liverpool sentence introduces an unsupplied stadium name. These are respectively
availability and grounding blockers under the strict writer contract.

This run used unchanged code/prompt at `2aed35e`, one fresh source snapshot and a
new content version. No AI repair, retry, selective regeneration, manual prose
editing or reused prior response was performed. The prompt, validator, intelligence
engine and fallback logic were not changed during the run.

## Source and pre-generation checks

- Snapshot/cutoff: **2026-10-07T18:32:28.894335+00:00** (14:32:28 EDT).
- SHA-256: `ca71f8a43670fde15aeb4f8999a65ae40cb9f34257a1d7fa5432f881fa4df5fb`.
- Size: 9,613,312 bytes. Source hash remains unchanged after verification.
- Official sync: **2026-10-07T18:30:23.161109+00:00**, approximately 126 seconds
  before capture; no sync error and zero unmatched fixtures. Fresh at preparation.
- Canonical season/competition/provider: `2026 / PL / football-data`; matchweek **6**.
- **20/20 canonical perspectives**, none blocked; each has five retained editorial
  candidates, no fallback facts. No zero-/one-signal context was available in this
  live sample. No safeguard was weakened to reach twenty.
- Content version: `pr27-second-live-20261007T183228Z`.
- Context version: `shared-team-context-v1`; prompt version: `shared-team-brief-v1`.
- Exact request-instructions SHA-256:
  `98fe63f57896f382ca9b4ab66d8a5e7f838bfb7afb0d37f6aed31afa15e698d8`.
- Intended production model, requested and returned: **`gpt-5.6-luna`**.

The source was copied by the prior safe SSH procedure: SQLite `mode=ro` plus
`query_only=ON`, backup into remote memory, serialized bytes saved only locally.
No production writes, remote temporary database file, timestamp rewrites or
Railway/n8n configuration changes occurred. Twenty context hashes, the prompt
hash and retained-fact inventory were recorded and reported **before paid calls**.
All context hashes and provider IDs are included with the reviews below.

### Official upcoming fixtures (UTC)

- `560592` — Sunderland–Brighton Hove: 2026-10-10 14:00 UTC.
- `560593` — Arsenal–Leeds United: 2026-10-10 11:30 UTC.
- `560594` — Hull City–Everton: 2026-10-11 13:00 UTC.
- `560595` — Chelsea–Bournemouth: 2026-10-10 14:00 UTC.
- `560596` — Crystal Palace–Nottingham: 2026-10-11 13:00 UTC.
- `560597` — Coventry City–Newcastle: 2026-10-12 19:00 UTC.
- `560598` — Liverpool–Man City: 2026-10-11 15:30 UTC.
- `560599` — Ipswich Town–Fulham: 2026-10-10 14:00 UTC.
- `560600` — Man United–Tottenham: 2026-10-10 16:30 UTC.
- `560601` — Aston Villa–Brentford: 2026-10-10 14:00 UTC.

First kickoff: **Arsenal–Leeds, October 10 at 11:30 UTC** (07:30 EDT).

## Generation and sentence-review totals

- **20 attempts / 20 distinct provider responses**, one per canonical fixture/team.
- SDK retries **zero**; no repair or subsequent provider calls.
- **18 succeeded and persisted / 2 failed validation / 0 uncertain / 0 blocked**.
- **43 sentences reviewed: 42 SUPPORTED, 1 UNSUPPORTED, 0 OVERSTATED, 0 AMBIGUOUS**.
- Accepted briefs contain 39 sentences: 38 supported and the one unsupported
  Liverpool sentence. Rejected Chelsea/Nottingham contain four supported sentences.

Each sentence was checked clause by clause against its exact cited frozen facts;
the per-sentence classification is conservative if any clause lacks support.
Counts, scores and date ranges were inspected against source rows, not inferred
from lexical acceptance. Every cited row's kickoff precedes both the cutoff and
upcoming fixture; official update times and reference snapshot sync times also
precede the cutoff. No post-cutoff information was introduced by the context.

## Material findings and diagnosis

### Accepted but unsupported: Liverpool's stadium name

“On 8 February 2026 **at Anfield**, Erling Haaland scored for Man City at 90+3 to
complete a 2-1 win over Liverpool.”

The cited `560598:history:LATE_DECISIVE_GOAL:617bd4d1d3ea3103` establishes Liverpool
as home team, Manchester City as away team, the date/result and the named event.
It contains **no stadium name**; Anfield appears nowhere in the frozen Liverpool
context. The other clauses are supported. This is an ungrounded addition, not a
claim that Anfield is factually the wrong stadium. External football knowledge
cannot supply a missing citation under this contract. Existing guards do not
check newly introduced venue names, so this is a semantic false negative.

### Supported but rejected: Chelsea, sentence 2

“Bournemouth have scored in four of their five opening league matches but remain
without a win, with two defeats and three draws.”

The two cited Bournemouth samples establish all those values. The parser splits
at **but**, loses the Bournemouth subject in the first part of the same sentence,
then looks at the preceding sentence about Chelsea. It reports `Ambiguous W-D-L
subject`. This is a false positive; the human antecedent is unambiguous.

### Supported but rejected: Nottingham, sentence 1

“Nottingham were unbeaten in four league meetings with Crystal Palace between
21 October 2024 and 1 February 2026, including three draws and a 1-0 win.”

The four cited H2H fixtures are exactly a 1-0 win and three 1-1 draws. The parser
sees two club names in one clause and reports `Ambiguous W-D-L subject`, although
Crystal Palace is clearly the opponent. A second limitation is visible from code
inspection: these outcomes live in `provenance.fixtures` home/away scores, whereas
the arithmetic checker handles normalized `provenance.rows` or explicit summary
counts. No alternate prose, validator bypass or hypothetical fixed output was
substituted for the rejected response.

These two failures leave Chelsea and Nottingham unavailable in the content store.
They should not be counted as incorrect football claims. Diagnose narrowly before
another release decision; do not broadly weaken the actor/arithmetic safeguards.
The venue addition needs a grounded writer/validation decision rather than silent
acceptance of outside knowledge. **No corrective code/prompt changes were made in
this evaluation.**

### Previously identified classes

No false W-D-L values or historically backdated league rankings were found.
Leeds/Everton keep the current ranking separate from their dated sample; Hull's
ranking is stated for the current season without a September date. Villa does
not invent a W-D-L breakdown. All explicit W-D-L values match their cited samples.

Both de Ligt briefs pass and use the full name, United attribution and exact
`at 90+6` notation. All six specific-goal sentences name scorer and club with
correct minutes: Haaland/City 90+3, Jiménez/Fulham 90+1, de Ligt/United 90+6.
Dates and final scores match. No own goals or top-scorer claims were generated.
The Palace abbreviation and “They scored” team-aggregate wording did not recur
in this sample, so those specific fixes retain offline regression coverage rather
than new live proof. Leeds/Everton do use “They” correctly for season/W-D-L prose.

No unsupported tactics, injuries, availability, appearances, current-player
assumptions, predictions or speculative causal claims were found. “Secure a draw”
and “complete a win” describe a cited equaliser/winner with the same final score.
“2026 season” is interpreted as the supplied 2026/27 season; the full season label
would be clearer, but no calendar-year record is asserted. No unbounded/all-time
H2H claim, unsupported recency superlative or internal/database language appears.

## Editorial findings

Every raw output contains meaningful football information, with 1–3 sentences
and within the 90-word cap. Brighton's one-sentence brief is compact. Scoring
contrasts provide legitimate recent-form material for Coventry and Hull without
inventing promotion history. Three historical goal events and the Palace/Forest
H2Hs offer more interesting hooks than generic form.

The batch still tends toward a statistical digest. August–September ranges,
“five matches,” opening-season constructions and mirrored opponent comparisons
recur. Sunderland starts both sentences with Brighton; Hull devotes much of its
space to Everton. Ipswich is better oriented to itself. Many pairs differ mainly
in ordering/selection from the same five candidates. United repeats 2-2 in one
sentence. These are **nonblocking editorial improvements**, separate from the
missing publishable entries and the unsupplied venue detail.

There is no generic filler, personal pick commentary, betting/fantasy/SEO framing
or technical audit disclaimer. No zero-/one-fact or fallback context was tested
live; restraint for genuinely thin entries and top-scorer/own-goal cases remains
regression coverage. No stylistic tuning was performed.

## Persistence, reuse and local Training Ground

Each entry has exactly one attempt; all 18 saved outputs match their raw JSON,
canonical identity, context hash and provider response ID. Twice loading each
saved body returns exact text; the two failed entries return unavailable. Repeated
preparation and frozen resume, with a provider stub that raises if called and all
retry flags off, made **zero calls**. All content rows, attempts and response IDs
remained unchanged. An adapter probe using fixture IDs starting at 1 versus 5000
returned identical shared content, including Arsenal; canonical identity rather
than league-local IDs determines the lookup.

The fresh source had three incomplete Week 6 picks. It remained unchanged. Only
disposable **second local game copies** had Week 6 picks replaced by clearly
synthetic complete test selections. Flask used those copies, the new evaluation
store/version, and automation disabled. No test choice was written to production
or to the frozen source.

Normal and HTMX Training Ground responses for players **1, 6 and 5** each rendered
the correct five saved briefs, selected teams and fixtures. Players 1 and 6 shared
the same five selections across different matchups, including the exact saved
Arsenal body. The complete-slate probe selected Bournemouth rather than the failed
Chelsea perspective; the earlier failure-path probe separately verified Chelsea's
existing unavailable placeholder. It did not hide or repair the failed response.

The `Pre-Match Brief` heading and five-`li` compact template structure were checked;
the actual fragment was also rendered in the full application shell. Home, normal
and HTMX requests returned 200, and generation/workflow functions were guarded
against invocation. A separate lookup-string probe verified HTML escaping without
editing any generated content. Protected game tables stayed unchanged after local
test setup; source and content remained unchanged. Saved HTML is available locally.
These are Flask/template integration checks, **not browser/pixel screenshots**;
no screenshots were captured. Passing UI checks does not turn Liverpool's accepted
but ungrounded clause into valid content or complete the two missing perspectives.

## Actual-clock timing and status

Local scheduler dry-run at **2026-10-07T18:37:57.451580+00:00** used the real clock and unchanged source. Official sync was still within 15 minutes. It reported **waiting**, 18 succeeded/2 failed, `generation_allowed=false`, `readiness_target_missed=false`, and `intervention_required=false`. It made zero calls. Known validation failures can recover in the future authorized window; they do not yet constitute a missed timing target.

- Eligibility: **October 9, 05:30 UTC / 01:30 EDT** (−30h).
- Readiness: **October 9, 11:30 UTC / 07:30 EDT** (−24h).
- Hard stop: **October 9, 17:30 UTC / 13:30 EDT** (−18h).

The authenticated local status endpoint returned 200 with `no-store`, automation
disabled, a fresh heartbeat and the same counts/window/status fields; an unauthenticated
request returned 403. The live clock was outside eligibility. No fake clock or
freshness rewrite was used to claim in-window readiness. Other lifecycle states
(`generating_before_target`, `ready_on_time`, `readiness_target_missed_recovering`,
`completed_late`, `hard_window_missed`) are verified by the offline simulated-clock
suite, not by this real-clock run. No production automation was activated.

## Full suites and artifact preservation

**480 Python tests passed** in 59.202 seconds; **10 JavaScript tests passed**.
Commands: `.venv/Scripts/python.exe -m unittest discover -s tests` and
`node --test tests/bulk_picks.test.cjs tests/pick_confirm.test.cjs`. Expected negative
test diagnostics appear in the Python log; the suite ended `OK`, exit code zero.

Raw artifacts stay local under `local_data/pr27_live_20261007T183230Z/`:
source manifest/database, content store, twenty frozen contexts/requests,
exclusive attempt markers, exact provider responses, exact output text, metadata
and successful brief objects. They preserve all exact per-sentence `used_fact_ids`.
Additional local files contain sentence reviews, reuse/UI checks, scheduler/status
results, saved HTML and test logs. The content version is distinct from run one;
old outputs were not reused. Neither raw JSON nor databases/helpers are committed.
Do not rerun the paid helper; no further generation is authorized by this report.

## All twenty briefs: exact sentences, citations and review

Each quoted sentence below is the unedited generated text. All contexts use the
same prompt/model/version recorded above; context hashes and response IDs are
individual. Every entry attempted once with SDK retries zero. All used IDs refer
to retained editorial facts; there were no fallback facts.

### Sunderland — Sunderland vs Brighton Hove

Canonical fixture/team `560592/3`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `3dbbccc30f0a0fe02ff3df362c7fb3d1ec2806a488949a86b4852039fd97ce08`. Response ID: `resp_06ec2ebccc31e6d0016ac690a8839c87d28d9d5ac0bf2c99ba`.

1. **SUPPORTED** — Brighton Hove scored in all five matches from 23 August to 19 September, while Sunderland scored in four of five from 22 August to 20 September.

   Cited: `560592:Brighton Hove:overall:SCORING_LAST_5`; `560592:Sunderland:overall:SCORING_LAST_5`.

2. **SUPPORTED** — Across those five-match spells, Brighton Hove scored 16 goals and conceded five; Sunderland scored six and conceded 10.

   Cited: `560592:Brighton Hove:overall:RECENT_GOALS`; `560592:Sunderland:overall:RECENT_GOALS`.

Source/editorial assessment: Both scoring frequencies, date ranges and 16/5 versus 6/10 totals match the two five-match samples. Supported, but Brighton leads both sentences; weak orientation toward Sunderland.

### Brighton Hove — Sunderland vs Brighton Hove

Canonical fixture/team `560592/78`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `6317cf8ce2370ec2392dbcde48cdeaa767da75f781c30b3fd6cf365e2fcb6882`. Response ID: `resp_0389f7044b6d0106016ac690aef56487d29c25e816e5de7289`.

1. **SUPPORTED** — Brighton Hove scored in all five of their league matches between 23 August and 19 September 2026, totaling 16 goals while conceding five.

   Cited: `560592:Brighton Hove:overall:SCORING_LAST_5`; `560592:Brighton Hove:overall:RECENT_GOALS`.

Source/editorial assessment: Five scoring matches and 16 for/five against are correct. A concise one-sentence attacking story; three available clean sheets are omitted without factual loss.

### Arsenal — Arsenal vs Leeds United

Canonical fixture/team `560593/19`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `a84b646f03358c6a547d47ebddc666e61fb29f576f56535c4e3cfa9e1405e6ec`. Response ID: `resp_05b6b21603f510fa016ac690b272e087d29c9ab0c622e64400`.

1. **SUPPORTED** — Arsenal won four of their five league matches from August 21 to September 19, scoring in four of them.

   Cited: `560593:Arsenal:overall:WINNING_LAST_5`; `560593:Arsenal:overall:SCORING_LAST_5`.

2. **SUPPORTED** — Leeds United opened the current league season unbeaten across five matches, conceding only three goals in that span.

   Cited: `560593:Leeds United:overall:UNBEATEN_SEASON_START`; `560593:Leeds United:overall:BEST_DEFENCE`.

Source/editorial assessment: Four wins and four scoring matches from five, not a current four-win streak. Leeds’ unbeaten five and three conceded are supported. A useful own-team/opponent contrast.

### Leeds United — Arsenal vs Leeds United

Canonical fixture/team `560593/71`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `43cc67b280da1cc848fe44bc0b817e53af9717acd7418e2f91af028a83c22bc7`. Response ID: `resp_08c11da1a4e0f45b016ac690b9631087d2b0bf16d69fda919e`.

1. **SUPPORTED** — Leeds United have conceded three goals in the current league season, joint fewest with Everton.

   Cited: `560593:Leeds United:overall:BEST_DEFENCE`.

2. **SUPPORTED** — They opened the season unbeaten across five matches between 22 August and 20 September 2026, recording two wins and three draws.

   Cited: `560593:Leeds United:overall:UNBEATEN_SEASON_START`.

Source/editorial assessment: Three conceded, tied with Everton at cutoff; five unbeaten comprising two wins/three draws. The ranking is separate from the dated sample. “They” correctly refers to Leeds.

### Everton — Hull City vs Everton

Canonical fixture/team `560594/13`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `76a3189d0fbdc7e0e74237e612ea97003f5e306caf2a5f1a98d41f036a9c7b06`. Response ID: `resp_06f2bd103236b006016ac690bdd1f087d281d5e88dfc86a8a9`.

1. **SUPPORTED** — Everton have conceded three goals in the current league season, joint fewest in the division.

   Cited: `560594:Everton:overall:BEST_DEFENCE`.

2. **SUPPORTED** — They began the 2026 season unbeaten across five league matches from 22 August to 19 September, winning twice and drawing three.

   Cited: `560594:Everton:overall:UNBEATEN_SEASON_START`.

3. **SUPPORTED** — Everton scored in four of five league matches between 22 August and 19 September; Hull City did likewise across its five matches from 22 August to 19 September.

   Cited: `560594:Everton:overall:SCORING_LAST_5`; `560594:Hull City:overall:SCORING_LAST_5`.

Source/editorial assessment: Three conceded is joint lowest at cutoff; two wins/three draws and both sides scoring in four of five are supported. Ranking and date range are separate sentences; the third sentence is longer and less distinctive.

### Hull City — Hull City vs Everton

Canonical fixture/team `560594/22`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `8801ed3f327c5d5d063c7816a9a6a386e8f094d26a9e83093c0f32284af66686`. Response ID: `resp_08503f2744850939016ac690c50fcc87d29f8a85dd719a6af4`.

1. **SUPPORTED** — Hull City scored in four of their five league matches between 22 August and 19 September, while Everton also found the net in four of their opening five.

   Cited: `560594:Hull City:overall:SCORING_LAST_5`; `560594:Everton:overall:SCORING_LAST_5`.

2. **SUPPORTED** — Everton began the 2026 league season unbeaten through five matches and had conceded only three goals, joint fewest in the league.

   Cited: `560594:Everton:overall:UNBEATEN_SEASON_START`; `560594:Everton:overall:BEST_DEFENCE`.

Source/editorial assessment: Both four-of-five scoring samples and Everton’s unbeaten opening five are supported. Joint fewest is presented for the current 2026 league season, not backdated to September 19 as in run one. Opposition-heavy but meaningful.

### Chelsea — Chelsea vs Bournemouth

Canonical fixture/team `560595/18`; validation **FAILED**; persisted **failed**.

Context SHA-256: `a22d6bd6504839ee02313ded72fd8def824416d4347d113a156e38ff3c99f445`. Response ID: `resp_0f69eec234f37097016ac690cbb5ec87d2955f7d27ff9db993`.

1. **SUPPORTED** — Chelsea have conceded 12 goals in five league matches, the most in the current league season.

   Cited: `560595:Chelsea:overall:WORST_DEFENCE`.

2. **SUPPORTED** — Bournemouth have scored in four of their five opening league matches but remain without a win, with two defeats and three draws.

   Cited: `560595:Bournemouth:overall:SCORING_LAST_5`; `560595:Bournemouth:overall:WINLESS_SEASON_START`. Validator: Ambiguous W-D-L subject.

Source/editorial assessment: Chelsea 12 conceded is the current league maximum. Bournemouth scored in four of five and were winless with two defeats/three draws. Both sentences are supported; sentence 2 is a parser false positive, not incorrect arithmetic.

### Bournemouth — Chelsea vs Bournemouth

Canonical fixture/team `560595/52`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `b5e7009ba55328a846b055542af7bac6ba653b9274c2c34dd8394b4ceafbde32`. Response ID: `resp_070cf24a17047c61016ac690d0210c87d2b71e42a39da67707`.

1. **SUPPORTED** — Bournemouth opened the current season with five winless matches, scoring in four of them and conceding in all five.

   Cited: `560595:Bournemouth:overall:WINLESS_SEASON_START`; `560595:Bournemouth:overall:SCORING_LAST_5`; `560595:Bournemouth:overall:NO_CLEAN_SHEET_SEASON_START`.

2. **SUPPORTED** — Chelsea had the league’s highest goals-against total at 12 and had not kept a clean sheet in their first five matches.

   Cited: `560595:Chelsea:overall:WORST_DEFENCE`; `560595:Chelsea:overall:NO_CLEAN_SHEET_SEASON_START`.

Source/editorial assessment: Five winless, four scoring games, no clean sheets; Chelsea’s 12 conceded/current league maximum and no clean sheets are correct. Useful defensive context without attaching a historical date to the ranking.

### Crystal Palace — Crystal Palace vs Nottingham

Canonical fixture/team `560596/51`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `f863616fc3a8c935b9acb7410f394990125793eef55762366386a20e9272ffc3`. Response ID: `resp_074510c71f8d4f9b016ac690d6f7f087d2881540def27b9389`.

1. **SUPPORTED** — Across five matches from 22 August to 20 September 2026, Crystal Palace scored six goals and conceded 11.

   Cited: `560596:Crystal Palace:overall:RECENT_GOALS`.

2. **SUPPORTED** — Crystal Palace went unbeaten in three league meetings with Nottingham between 5 May 2025 and 1 February 2026, with all three ending level.

   Cited: `560596:history:H2H_UNBEATEN_RUN:d6e5a3718856d274`.

Source/editorial assessment: Six goals for/11 against across the dated five; the three bounded H2Hs were all 1-1 draws. Correct, though it begins with form rather than the more distinctive H2H. Uses the full club name, so Palace-alias acceptance is not exercised live here.

### Nottingham — Crystal Palace vs Nottingham

Canonical fixture/team `560596/63`; validation **FAILED**; persisted **failed**.

Context SHA-256: `a608b0f48c558e91ff8ce344f318a901791c3c6722e25d88a9b1de612836cc70`. Response ID: `resp_067c72ea4ab28e9c016ac690de456887d2900bd03f5f55c38a`.

1. **SUPPORTED** — Nottingham were unbeaten in four league meetings with Crystal Palace between 21 October 2024 and 1 February 2026, including three draws and a 1-0 win.

   Cited: `560596:history:H2H_UNBEATEN_RUN:07697f590434d9ea`. Validator: Ambiguous W-D-L subject.

2. **SUPPORTED** — Across Nottingham’s five current-season matches from 22 August to 19 September 2026, they failed to score in three.

   Cited: `560596:Nottingham:overall:NO_SCORING_LAST_5`.

Source/editorial assessment: The four bounded H2Hs are a 1-0 win on 21 October 2024, then 1-1 draws on 5 May 2025, 24 August 2025 and 1 February 2026. Three current-season blanks are correct. All supported; the comparative H2H sentence is rejected by subject parsing.

### Coventry City — Coventry City vs Newcastle

Canonical fixture/team `560597/117`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `82a499f3bcffbca13d3962d17589b79b788c06b69c62f8ee4de8a4db304d5135`. Response ID: `resp_00ac1acacb906061016ac690eab3d487d2a5d60ffc12e49540`.

1. **SUPPORTED** — Coventry City scored in only one of five matches between 21 August and 19 September 2026, while Newcastle scored in all five of its matches between 23 August and 19 September.

   Cited: `560597:Coventry City:overall:NO_SCORING_LAST_5`; `560597:Newcastle:overall:SCORING_LAST_5`.

2. **SUPPORTED** — Across those five-match sequences, Coventry City scored once and conceded 10, while Newcastle scored nine and conceded nine.

   Cited: `560597:Coventry City:overall:RECENT_GOALS`; `560597:Newcastle:overall:RECENT_GOALS`.

Source/editorial assessment: Four blanks means scoring in one of five; one goal for/ten against. Newcastle scored in all five, nine for/nine against. Natural recent-form context without invented promotion history, albeit repetitive scoring-sample phrasing.

### Newcastle — Coventry City vs Newcastle

Canonical fixture/team `560597/20`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `8ca2bb3f136a7ecd832edf949ac61228ff939767867d703f9aa17463dddfa95c`. Response ID: `resp_0d0f53f4d3395534016ac690e4227087d2960cab1ec12d19c8`.

1. **SUPPORTED** — Newcastle scored in all five matches between 23 August and 19 September 2026.

   Cited: `560597:Newcastle:overall:SCORING_LAST_5`.

2. **SUPPORTED** — Coventry City failed to score in four of their five matches between 21 August and 19 September 2026.

   Cited: `560597:Coventry City:overall:NO_SCORING_LAST_5`.

Source/editorial assessment: Five scoring games versus Coventry’s four blanks, with both exact sample date ranges. Concise and relevant; a statistical comparison rather than a distinctive historical story.

### Liverpool — Liverpool vs Man City

Canonical fixture/team `560598/8`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `3fc6434ee2759fc5e8aafe416589226287ff8dc5f507b24b13cc5e159b15cb48`. Response ID: `resp_0818c133c146e6d3016ac690f1af2087d2a7f6338f07056bf2`.

1. **SUPPORTED** — Liverpool began this season unbeaten through five league matches, including two wins and three draws.

   Cited: `560598:Liverpool:overall:UNBEATEN_SEASON_START`.

2. **SUPPORTED** — Man City won all five of their opening league matches and scored in each one.

   Cited: `560598:Man City:overall:SEASON_START_RECORD`; `560598:Man City:overall:SCORING_LAST_5`.

3. **UNSUPPORTED** — On 8 February 2026 at Anfield, Erling Haaland scored for Man City at 90+3 to complete a 2-1 win over Liverpool.

   Cited: `560598:history:LATE_DECISIVE_GOAL:617bd4d1d3ea3103`.

Source/editorial assessment: Two wins/three draws and City’s five wins/five scoring games match the cited samples. Sentence 3 has the correct date, scorer, club, 90+3 and 2-1 outcome, but adds the stadium name Anfield absent from its cited fact and the entire context. It is UNSUPPORTED for that clause only; no external fact is used to excuse the grounding breach.

### Man City — Liverpool vs Man City

Canonical fixture/team `560598/9`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `666fe0d13fb157e7b500ae6d9b7b9e6fa276698620a40eddff39b8d3c0765cb9`. Response ID: `resp_057814303051965a016ac690f9d69c87d28547dcf286838d99`.

1. **SUPPORTED** — Manchester City began this season with five straight wins between 23 August and 20 September, while Liverpool went unbeaten through its first five matches.

   Cited: `560598:Man City:overall:SEASON_START_RECORD`; `560598:Liverpool:overall:UNBEATEN_SEASON_START`.

2. **SUPPORTED** — Erling Haaland scored for Manchester City at 90+3 to complete a 2-1 win over Liverpool on 8 February 2026.

   Cited: `560598:history:LATE_DECISIVE_GOAL:617bd4d1d3ea3103`.

Source/editorial assessment: Five consecutive opening wins and Liverpool’s unbeaten five are supported. Haaland, Manchester City, 90+3, the 2-1 result and 8 February 2026 match the event. No unsupported named stadium is added.

### Fulham — Ipswich Town vs Fulham

Canonical fixture/team `560599/11`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `0abf3b47e050bcb5148335440f6173ef0546dad859bcceb27f0c557b84a487aa`. Response ID: `resp_0fee3d5ddd4d0fa9016ac691009e1c87d282aada5ca1d570c1`.

1. **SUPPORTED** — Fulham began the 2026 season without a win in five matches between 24 August and 20 September, drawing twice and losing three times.

   Cited: `560599:Fulham:overall:SEASON_START_RECORD`.

2. **SUPPORTED** — Ipswich Town also went without a clean sheet across their first five matches, from 22 August to 19 September 2026.

   Cited: `560599:Ipswich Town:overall:NO_CLEAN_SHEET_SEASON_START`.

3. **SUPPORTED** — Raúl Jiménez scored for Fulham at 90+1 to secure a 2-2 draw with Ipswich Town on 5 January 2025.

   Cited: `560599:history:LATE_DECISIVE_GOAL:8ee2aab37a28d85a`.

Source/editorial assessment: Zero wins/two draws/three defeats; Ipswich zero clean sheets in five. Jiménez’s Fulham goal at 90+1 made the final score 2-2 on 5 January 2025. “Secure a draw” describes that final-score equaliser, not a tactical or future causal prediction.

### Ipswich Town — Ipswich Town vs Fulham

Canonical fixture/team `560599/116`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `83951398c4aabe7ea24784629eb259d5e66dd0d8580b829c076a07e4fa77812f`. Response ID: `resp_0e0ba006bba78a8b016ac691060bf487d2b70ec640c401c168`.

1. **SUPPORTED** — Ipswich Town scored seven and conceded 11 across five matches from 22 August to 19 September 2026, keeping no clean sheet in that run.

   Cited: `560599:Ipswich Town:overall:RECENT_GOALS`; `560599:Ipswich Town:overall:NO_CLEAN_SHEET_SEASON_START`.

2. **SUPPORTED** — Fulham began the season without a win in five matches, drawing twice and losing three times.

   Cited: `560599:Fulham:overall:SEASON_START_RECORD`.

3. **SUPPORTED** — In the 2-2 meeting at Fulham on 5 January 2025, Raúl Jiménez scored for Fulham at 90+1 to equalise.

   Cited: `560599:history:LATE_DECISIVE_GOAL:8ee2aab37a28d85a`.

Source/editorial assessment: Seven scored/11 conceded and zero clean sheets; Fulham two draws/three defeats. The Jiménez goal, Fulham home venue, 90+1 and dated 2-2 are supported. Better selected-team orientation than run one.

### Man United — Man United vs Tottenham

Canonical fixture/team `560600/14`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `d7a31eabf1fb2e334c5717672f1bdcc0def0828f6e959bb40022239ec0e880f0`. Response ID: `resp_049e35a2073948b3016ac6911437e487d29497cc19008e1919`.

1. **SUPPORTED** — Man United conceded in each of their first five league matches between 22 August and 20 September 2026, while Tottenham went winless across the same five-match opening spell.

   Cited: `560600:Man United:overall:NO_CLEAN_SHEET_SEASON_START`; `560600:Tottenham:overall:WINLESS_SEASON_START`.

2. **SUPPORTED** — In the 2-2 draw at Tottenham on 8 November 2025, Matthijs de Ligt scored for Man United at 90+6 to make it 2-2.

   Cited: `560600:history:LATE_DECISIVE_GOAL:d3dc163b964e61de`.

Source/editorial assessment: United conceded in five opening matches; Tottenham were winless in their opening five. “Same five-match opening spell” is read as the opening-five league sample, not a claim that Tottenham’s final match also occurred on September 20 (it was September 19). De Ligt, United, 90+6, date, Tottenham venue and 2-2 are supported. Repeating 2-2 in one sentence is unnecessary.

### Tottenham — Man United vs Tottenham

Canonical fixture/team `560600/6`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `ba343af563bbc4a55f52ef317a9a031745b33b2f8fcce43a9042ab02ac4691f2`. Response ID: `resp_0e548947deeebf3a016ac6910d2f7887d29fd51e436e67590c`.

1. **SUPPORTED** — Between 22 August and 19 September 2026, Tottenham went without a win in their first five league matches and failed to score in four of them.

   Cited: `560600:Tottenham:overall:WINLESS_SEASON_START`; `560600:Tottenham:overall:NO_SCORING_LAST_5`.

2. **SUPPORTED** — In the 2-2 meeting at Tottenham Hotspur on 8 November 2025, Matthijs de Ligt scored for Manchester United at 90+6 to equalise.

   Cited: `560600:history:LATE_DECISIVE_GOAL:d3dc163b964e61de`.

Source/editorial assessment: Five winless/four blanks and sample dates are correct. The home-team name Tottenham Hotspur is explicitly in event provenance; de Ligt scored for Manchester United at 90+6 to equalise in the dated 2-2. Full-name particle handling passes.

### Aston Villa — Aston Villa vs Brentford

Canonical fixture/team `560601/15`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `af10fcc028e09e1720aaf5fa63a524508889e5716576f775953fd5ccda94cdaf`. Response ID: `resp_0e6912de38665d3b016ac69119bdc887d2b02ec90e5b9fb92b`.

1. **SUPPORTED** — Aston Villa scored four goals across five matches between 23 August and 19 September 2026, failing to score in three of them.

   Cited: `560601:Aston Villa:overall:RECENT_GOALS`; `560601:Aston Villa:overall:NO_SCORING_LAST_5`.

2. **SUPPORTED** — Brentford scored in all five matches between 22 August and 18 September 2026, totaling 10 goals during that run.

   Cited: `560601:Brentford:overall:SCORING_LAST_5`; `560601:Brentford:overall:RECENT_GOALS`.

Source/editorial assessment: Four goals and three blanks in five; Brentford scored in all five with ten total. Supported; no reversed W-D-L claim is generated this time.

### Brentford — Aston Villa vs Brentford

Canonical fixture/team `560601/236`; validation **PASSED**; persisted **succeeded**.

Context SHA-256: `c21048b5f81abce2d3cc903ba9da195ef66ff30347a14d542246cd8d7a3cf959`. Response ID: `resp_062c84c23b51e100016ac6911e84cc87d2ace797f9e65545c0`.

1. **SUPPORTED** — Brentford opened the 2026 league season unbeaten across five matches between 22 August and 18 September, scoring in every game.

   Cited: `560601:Brentford:overall:UNBEATEN_SEASON_START`; `560601:Brentford:overall:SCORING_LAST_5`.

2. **SUPPORTED** — Aston Villa scored four goals across five league matches from 23 August to 19 September 2026, failing to score in three of them.

   Cited: `560601:Aston Villa:overall:RECENT_GOALS`; `560601:Aston Villa:overall:NO_SCORING_LAST_5`.

Source/editorial assessment: Unbeaten opening five, scoring in every game; Villa four goals/three blanks in five. Supported, but another opening-season-plus-date-range template.

## Final recommendations

**NOT READY FOR MERGE** — two supported perspectives still fail publication, and
one accepted sentence adds an unsupplied named venue. Diagnose these narrow issues
before another release decision; the improved 18/20 validation rate alone is not
proof of semantic readiness.

**NOT READY FOR ACTIVATION** — the content blockers remain, and separate approval
is still required for production storage initialization, Railway start-command/
environment changes, worker/heartbeat verification, monitoring and deployment.
None of those steps occurred. No merge, production writes, configuration change
or automation activation was performed. Unrelated local files and changes are
preserved.
