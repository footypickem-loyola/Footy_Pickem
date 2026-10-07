# PR27 controlled live evaluation — 7 October 2026

**NOT READY FOR MERGE. NOT READY FOR ACTIVATION.** This evaluation found two materially incorrect sentences in accepted briefs and four supported briefs rejected by lexical scorer checks. No prompt, detector, ranking, validator or application behavior was changed to improve the result.

## Outcome

- Contexts: **20/20**, five retained editorial candidates each; no fallback facts, blocked entries or empty contexts.
- Live calls: **20**, one per canonical fixture/team perspective; 20 distinct provider responses, SDK retries zero, no repair or regeneration.
- Persisted: **16 succeeded, 4 failed validation, 0 uncertain, 0 blocked**. Failed raw responses are preserved locally and were reviewed too.
- Sentence grounding: **45 SUPPORTED, 2 UNSUPPORTED, 0 OVERSTATED, 0 AMBIGUOUS**, across all 47 sentences. A partly false sentence is counted once as UNSUPPORTED; its supported clauses are identified below.
- Accepted briefs contain 35 sentences: 33 supported and two unsupported. The four rejected briefs contain 12 supported sentences. Thus lexical acceptance is explicitly **not** semantic grounding.
- Reuse, repeated preparation, normal/HTMX rendering and scheduler dry-run: **zero additional provider calls**, all 20 attempt counters remain one, response IDs and content rows unchanged.

## Source and frozen preparation

A production SQLite read-only connection (`mode=ro`, `query_only=ON`) backed up to an in-memory SQLite database through an existing Railway SSH connection. Serialized bytes were saved only locally. There were no production writes, remote temporary database files, timestamp rewrites, configuration changes, deployment or activation.

- Snapshot/cutoff: **2026-10-07T12:48:03.056711+00:00** (08:48:03 EDT).
- Source SHA-256: `2c96d94e2ecf7ff1d388d0470a13c60805109292744c72c45bc8968aaa792c64` (9,613,312 bytes), unchanged after evaluation.
- Canonical provider/competition/season: `football-data / PL / 2026`; matchweek **6**. League-local week ID 44 is not the shared identity.
- Official sync succeeded at **2026-10-07T12:45:20.286569+00:00**, about 163 seconds before capture; no error, zero unmatched matches. Fresh at preparation.
- Reference sync completed for 2024/25 and 2025/26 on October 5, and 2026/27 on October 7 at 11:19:30 UTC. No import or backfill was performed for this evaluation.
- Model, both requested and returned: **`gpt-5.6-luna`**, the configured production model.
- Content version: `pr27-live-20261007T124803Z`; context `shared-team-context-v1`; prompt `shared-team-brief-v1`.
- SHA-256 of exact request instructions: `c79222e4953c6ed7905f7a88cb0345d7ea921f67109096764385adf096c3193f`. Per-entry context hashes and response IDs are recorded below and in local metadata.
- All contexts were inspected and the inventory was reported before the first call. There were 100 retained-candidate assignments (the same five per fixture in both perspectives), representing 50 unique candidates. None was artificially unblocked.

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

First kickoff is Arsenal–Leeds, **10 October at 11:30 UTC** (07:30 EDT).

## Material issues and diagnosis

### 1. An accepted sentence backdates a current league ranking

Hull sentence 2 says: “By 19 September 2026, Everton had conceded three goals in five league matches, joint fewest with Leeds United.” The cited `560594:Everton:overall:BEST_DEFENCE` establishes the ranking **at the October 7 cutoff**, not on September 19. The same fact’s league rows show City had conceded **two** by September 19 (fixtures 560548, 560555, 560563, 560580); Everton and Leeds had conceded three. City conceded three more on September 20 (560590). Everton’s three-in-five clause is supported; “joint fewest” at the generated historical date is false. A valid fact ID and date tokens do not validate the temporal comparison.

### 2. An accepted sentence reverses wins and draws

Villa sentence 2 gives Brentford “three wins and two draws.” Its cited `560601:Brentford:overall:UNBEATEN_SEASON_START` contains **W, D, D, D, W: two wins and three draws**. Scoring in all five, the date range and unbeaten status are supported. The extra W-D-L assertion is false. The validator binds the fact ID but does not check arithmetic entailment.

### 3. Four supported briefs fail scorer-actor parsing

All fail with `Unknown/uncited scoring actor` in `correspondent/pre_match_writer.py`:

- Palace sentence 2: “Palace scored…” is a supported team aggregate, but the actor check recognizes the exact team name Crystal Palace, not its abbreviation.
- Coventry sentence 2: “They scored…” refers unambiguously to Coventry in sentence 1; the actor check treats capitalized “They” as an unknown scorer.
- United and Tottenham sentence 3: the capitalized-name regex breaks at lowercase **de**, captures **Ligt**, and fails exact comparison with authoritative **Matthijs de Ligt**. The name is explicitly present, correctly attributed to United and backed by the cited event. Trailing whitespace in the source name does not explain this rejection; normalized full-name matching succeeds.

These are availability failures, not four hallucinated goals. The rejected output was not edited or forced into the store.

### Follow-up scope, not changes made in this evaluation

Before release, diagnose/fix temporal scope and W-D-L validation with focused regressions for these exact false acceptances; improve actor binding for verified club aliases, unambiguous team aggregates and full names containing particles without permitting uncited players. Preserve existing scorer, minute, citation and cutoff protections. Do not change PR24 ranking or broadly tune the prompt for stylistic preferences. A further live pass would be a separate explicitly authorized evaluation; none was attempted here.

## Editorial review

All 20 raw outputs contain meaningful football facts and stay within 2–3 sentences and the 90-word limit. The three late-goal stories and Palace/Forest H2H sequences are the most distinctive material. Form comparisons supply legitimate context for Hull, Coventry and Ipswich without inventing promoted-team records or deeper history.

The batch still reads more like a statistical digest than a varied football column. “Began/opened…five matches,” long August–September ranges and opponent-form second sentences recur. Leeds and Everton use particularly similar structures. Some sentences combine a season label with a date range, or restate overlapping scoring totals/patterns. “2026 season” is interpreted in the supplied 2026/27 league context; using the full season label would avoid calendar-year ambiguity. These are optional editorial improvements, not grounds for relaxing validation.

Perspective differences are often selection/reordering of the same five facts rather than substantially different stories. Ipswich leads twice with Fulham facts before its own record; Liverpool devotes two sentences to City. Those are supported opposition angles, but weaker orientation toward the selected club. The per-entry notes below distinguish these cases.

No internal/audit terminology, personal pick commentary, betting/fantasy/SEO filler, unsupported recency superlatives, predictions, tactics, injuries, causal explanations or current-player/appearance assumptions were found. All six specific-goal sentences name the scorer and scoring club and use exact `at 90+N` notation. They describe three authoritative events: Haaland/City at 90+3, Jiménez/Fulham at 90+1, de Ligt/United at 90+6. Result, venue and date boundaries match the cited event records.

No output makes a club top-scorer claim, and no live context uses fallback facts or has only zero/one retained signal. Thin-context restraint, fallback rendering and scorer-total logic therefore remain regression coverage, **not verified by this live sample**. Facts were checked against the frozen pre-cutoff official/reference evidence, not treated as externally audited match history.

## Persistence, UI and scheduler

The existing preparation and frozen-resume workflow was rerun with a writer stub that raises if called, with failed/uncertain retries disabled. It made zero calls. All content rows, successful structured responses, IDs and attempt counters were unchanged. Each successful body was loaded twice identically; failed entries return unavailable. Canonical provider/competition/season/fixture/team/version keys match their frozen contexts. An adapter probe with local fixture IDs offset from 1 to 5000 returned identical saved bodies, including Arsenal, demonstrating the lookup does not depend on local league IDs.

Week 6 had no picks in the production snapshot. Only a **second disposable local game copy** received synthetic selections for this UI test. Flask used that copy, the evaluation content/version and automation disabled. Players 1 and 6 had the same five selections in distinct matchups; player 5 exercised the other five fixtures. Normal and HTMX GETs returned the correct five labeled bullets, with exact saved text where available and the existing unavailable placeholder for rejected perspectives. The same Arsenal text was reused across players. All three routes returned 200; the home shell also returned 200. The actual Training Ground fragment was rendered into the full application shell and saved alongside normal/HTMX HTML. This is Flask/template integration verification, **not a browser screenshot or pixel-layout review**. No screenshots were taken.

A separate rendering probe confirmed `<script>` in a lookup value remains HTML-escaped; this did not modify any generated body or persisted content. Protected game rows (seasons, weeks, fixtures, results, picks, matchups) were unchanged after local synthetic setup; content rows and the original source hash also stayed unchanged. GET paths were guarded against workflow/writer invocation. Four failed perspectives still have no publishable brief; UI plumbing success does not make the batch complete.

Scheduler dry-run clock: **2026-10-07T13:01:58.241926+00:00**, using the real clock and unchanged snapshot. Status: **official_data_stale**; heartbeat present and fresh immediately after the tick; counts **16 succeeded / 4 failed**, each attempt count one. It made zero generation calls. The frozen local snapshot had aged beyond 15 minutes; this is an expected stale-copy report, not a claim that production sync stopped.

Eligibility opens **2026-10-09 11:30 UTC** (Friday 07:30 EDT), 24 hours before first kickoff. Target/deadline is **2026-10-09 17:30 UTC** (13:30 EDT), 18 hours before kickoff. The current evaluation is outside that window. No clock or freshness timestamp was rewritten to exercise paid automation. In-window timing, overlap/restart and failure handling are simulated regression tests; this dry-run does not demonstrate a production worker completing 20 entries inside the real window.

The local authenticated status endpoint also returned HTTP 200, `Cache-Control: no-store`, automation disabled, a fresh heartbeat, and the correct 16/4 counts and stale-source diagnosis. The unauthenticated request returned 403. A second actual-clock dry-run refreshed only the local operational heartbeat and made zero calls.

Remaining activation prerequisites include resolving the material quality/availability issues, an approved fresh in-window operational verification, intended content/operations volume initialization and backup policy, opt-in worker/start-command approval, and delivered external monitoring alerts/always-on behavior. Production activation remains off; no monitor, Railway or n8n configuration was changed.

## Local audit artifacts and reproducibility

Raw artifacts remain outside Git under `local_data/pr27_live_20261007T124804Z/`: `source.db`, `source_manifest.json`, `content.db`, `context_summary.json`, `pre_generation_inspection.md`, and for each canonical fixture/team prefix `.context.json`, `.request.json`, `.attempt.json`, `.provider.json`, `.output.txt`, `.metadata.json`, plus `.brief.json` for successes. They preserve exact output, exact per-sentence `used_fact_ids`, provider response ID, requested/returned model, prompt/context versions and hashes. There is no separate model-returned `used_candidate_ids` field in the single-team schema; each cited fact is explicitly identified as editorial/fallback in its frozen context (all editorial here).

Local `sentence_review.json`, `verification.json`, `scheduler_dry_run.json`, saved HTML and suite logs supplement that audit trail. No raw response, database, giant candidate artifact, helper script or credential is committed. Re-reading these files is safe; **do not rerun the paid generation helper**. An exclusive attempt marker prevents accidental re-generation in that helper. The committed workflow/CLI and operational guide retain the reproducible preparation path; a new source/version/provider run would be a new evaluation, not exact reproduction of nondeterministic prose.

## Complete sentence review and context inventory

The following quotations preserve every generated sentence without corrections. `SUPPORTED` means the cited fact’s structured values/provenance support the sentence; all clauses were considered. The two false clauses are specifically diagnosed above. For each entry, the five available retained IDs and the sentence-level used IDs make selection and omissions reviewable. Metadata hashes below refer to the original frozen files, not rewritten contexts.

### Sunderland — Sunderland vs Brighton Hove

Canonical fixture/team: `560592/3`. Validation: **PASSED**. Context SHA-256: `2da338e4ace05104772d2c1459b10ec00d4e846780718f9fcb3aee5b8eb07d90`. Response: `resp_045dac470259c19f016ac64022051087d2aba028e40cbfccaf`.

Available retained candidates (fallbacks: 0):

- `560592:Brighton Hove:overall:SCORING_LAST_5`
- `560592:Brighton Hove:overall:RECENT_GOALS`
- `560592:Sunderland:overall:SCORING_LAST_5`
- `560592:Sunderland:overall:RECENT_GOALS`
- `560592:Brighton Hove:overall:CLEAN_SHEET_LAST_5`

1. **SUPPORTED** — Sunderland scored in four of five matches between 22 August and 20 September 2026, with six goals scored and 10 conceded across that span.

   Cited: `560592:Sunderland:overall:SCORING_LAST_5`; `560592:Sunderland:overall:RECENT_GOALS`.

2. **SUPPORTED** — Brighton Hove found the net in all five matches between 23 August and 19 September 2026, scoring 16 goals, while keeping three clean sheets.

   Cited: `560592:Brighton Hove:overall:SCORING_LAST_5`; `560592:Brighton Hove:overall:RECENT_GOALS`; `560592:Brighton Hove:overall:CLEAN_SHEET_LAST_5`.

Source/editorial assessment: Six scored, ten conceded; scoring in four of five. Useful contrast with Brighton’s 16 goals and three clean sheets; dense but concise.

### Brighton Hove — Sunderland vs Brighton Hove

Canonical fixture/team: `560592/78`. Validation: **PASSED**. Context SHA-256: `5c672750c3d5ee23e8950e6204f1b6fa060a37f65dd77a31a67f145ef680a606`. Response: `resp_08dac6e9babb106d016ac64027dd2087d2a296dc8cbd0fefaa`.

Available retained candidates (fallbacks: 0):

- `560592:Brighton Hove:overall:SCORING_LAST_5`
- `560592:Brighton Hove:overall:RECENT_GOALS`
- `560592:Sunderland:overall:SCORING_LAST_5`
- `560592:Sunderland:overall:RECENT_GOALS`
- `560592:Brighton Hove:overall:CLEAN_SHEET_LAST_5`

1. **SUPPORTED** — Brighton Hove scored in all five matches from 23 August to 19 September 2026, totaling 16 goals while conceding five.

   Cited: `560592:Brighton Hove:overall:SCORING_LAST_5`; `560592:Brighton Hove:overall:RECENT_GOALS`.

2. **SUPPORTED** — Three of those five matches ended with clean sheets for Brighton Hove.

   Cited: `560592:Brighton Hove:overall:CLEAN_SHEET_LAST_5`.

Source/editorial assessment: Five scoring matches, 16 for/five against, three clean sheets. Clear attacking story, though the second sentence repeats the five-match sample.

### Arsenal — Arsenal vs Leeds United

Canonical fixture/team: `560593/19`. Validation: **PASSED**. Context SHA-256: `1b704c2baf45cd7198d174f7ef65b767e1d5f6635a3a839f438fc8790428057d`. Response: `resp_02f404b1af3ab8ff016ac6402e5a7c87d2aa369f9f5a916d20`.

Available retained candidates (fallbacks: 0):

- `560593:Leeds United:overall:BEST_DEFENCE`
- `560593:Leeds United:overall:UNBEATEN_SEASON_START`
- `560593:Arsenal:overall:SCORING_LAST_5`
- `560593:Arsenal:overall:WINNING_LAST_5`
- `560593:Leeds United:overall:SCORING_LAST_5`

1. **SUPPORTED** — Arsenal have won four of their five league matches from 21 August to 19 September, scoring in four of them.

   Cited: `560593:Arsenal:overall:WINNING_LAST_5`; `560593:Arsenal:overall:SCORING_LAST_5`.

2. **SUPPORTED** — Leeds United began their league campaign unbeaten in five matches from 22 August to 20 September, conceding three goals across that run.

   Cited: `560593:Leeds United:overall:UNBEATEN_SEASON_START`; `560593:Leeds United:overall:BEST_DEFENCE`.

Source/editorial assessment: Four wins from five is correctly a rolling total, not a four-game current winning streak. Leeds’ unbeaten start and three conceded provide a useful contrast.

### Leeds United — Arsenal vs Leeds United

Canonical fixture/team: `560593/71`. Validation: **PASSED**. Context SHA-256: `c120ff4bc1d57dead54550d1a2f466d33e548f17c2e261feeffb8fddf09ab422`. Response: `resp_0303eb33fdc5bac6016ac640367e5487d2b8857d858a1b6ad3`.

Available retained candidates (fallbacks: 0):

- `560593:Leeds United:overall:BEST_DEFENCE`
- `560593:Leeds United:overall:UNBEATEN_SEASON_START`
- `560593:Arsenal:overall:SCORING_LAST_5`
- `560593:Arsenal:overall:WINNING_LAST_5`
- `560593:Leeds United:overall:SCORING_LAST_5`

1. **SUPPORTED** — Leeds United began the 2026 league season unbeaten across five matches from 22 August to 20 September, recording two wins and three draws.

   Cited: `560593:Leeds United:overall:UNBEATEN_SEASON_START`.

2. **SUPPORTED** — Across that five-match run, Leeds United conceded just three goals, joint fewest in the league alongside Everton.

   Cited: `560593:Leeds United:overall:BEST_DEFENCE`.

Source/editorial assessment: Two wins/three draws; three conceded, tied with Everton at the frozen cutoff. Precise, but season plus date range is redundant.

### Everton — Hull City vs Everton

Canonical fixture/team: `560594/13`. Validation: **PASSED**. Context SHA-256: `cf8f4ab254fdbe889e9dcd6ca23ec9e8df2f27f7d58a0b281be1d644f6a9e73b`. Response: `resp_0f6f830150e72df6016ac6403c1efc87d2b480da59b95e989e`.

Available retained candidates (fallbacks: 0):

- `560594:Everton:overall:BEST_DEFENCE`
- `560594:Everton:overall:UNBEATEN_SEASON_START`
- `560594:Everton:overall:SCORING_LAST_5`
- `560594:Hull City:overall:SCORING_LAST_5`
- `560594:Everton:overall:CLEAN_SHEET_LAST_5`

1. **SUPPORTED** — Everton opened the 2026 league season unbeaten across five matches from 22 August to 19 September, with two wins and three draws.

   Cited: `560594:Everton:overall:UNBEATEN_SEASON_START`.

2. **SUPPORTED** — They conceded three goals in that five-match sequence, joint fewest in the league with Leeds United, while scoring in four of the five games.

   Cited: `560594:Everton:overall:BEST_DEFENCE`; `560594:Everton:overall:SCORING_LAST_5`.

Source/editorial assessment: Two wins/three draws; three conceded and four scoring matches. The ranking is read at the context cutoff; unlike Hull’s sentence, it does not explicitly backdate the ranking. Similar structure to Leeds.

### Hull City — Hull City vs Everton

Canonical fixture/team: `560594/22`. Validation: **PASSED**. Context SHA-256: `1a7fb615597de24d206aa59537ea41be7c7024e649352e5261d7866303a0da58`. Response: `resp_046ecd73fdf8d41c016ac640417e1087d295269ccdff4e7ef2`.

Available retained candidates (fallbacks: 0):

- `560594:Everton:overall:BEST_DEFENCE`
- `560594:Everton:overall:UNBEATEN_SEASON_START`
- `560594:Everton:overall:SCORING_LAST_5`
- `560594:Hull City:overall:SCORING_LAST_5`
- `560594:Everton:overall:CLEAN_SHEET_LAST_5`

1. **SUPPORTED** — Hull City scored in four of their five league matches between 22 August and 19 September 2026.

   Cited: `560594:Hull City:overall:SCORING_LAST_5`.

2. **UNSUPPORTED** — By 19 September 2026, Everton had conceded three goals in five league matches, joint fewest with Leeds United.

   Cited: `560594:Everton:overall:BEST_DEFENCE`.

Source/editorial assessment: Four scoring matches is supported. Sentence 2 incorrectly moves a cutoff-time league ranking back to 19 September; see blocker 1. Brief remains factually unsafe despite passing lexical validation.

### Chelsea — Chelsea vs Bournemouth

Canonical fixture/team: `560595/18`. Validation: **PASSED**. Context SHA-256: `49035bdbd08a5d5af8a29cd257075af7512a1201a12589443b31a3419c6876af`. Response: `resp_0c37ed7f002c2b8c016ac640480d8087d28384ffa3bd7b8329`.

Available retained candidates (fallbacks: 0):

- `560595:Chelsea:overall:WORST_DEFENCE`
- `560595:Bournemouth:overall:NO_CLEAN_SHEET_SEASON_START`
- `560595:Bournemouth:overall:WINLESS_SEASON_START`
- `560595:Chelsea:overall:NO_CLEAN_SHEET_SEASON_START`
- `560595:Bournemouth:overall:SCORING_LAST_5`

1. **SUPPORTED** — Chelsea have conceded 12 goals across their opening five league matches in 2026, the highest total in the league at this stage.

   Cited: `560595:Chelsea:overall:WORST_DEFENCE`.

2. **SUPPORTED** — Neither Chelsea nor Bournemouth kept a clean sheet in their opening five league matches, while Bournemouth scored in four of those five games.

   Cited: `560595:Chelsea:overall:NO_CLEAN_SHEET_SEASON_START`; `560595:Bournemouth:overall:NO_CLEAN_SHEET_SEASON_START`; `560595:Bournemouth:overall:SCORING_LAST_5`.

Source/editorial assessment: Twelve conceded is the league maximum at cutoff; both clubs have zero clean sheets in five, Bournemouth scored in four. A meaningful defensive comparison. “In 2026” is imprecise season shorthand; prefer 2026/27.

### Bournemouth — Chelsea vs Bournemouth

Canonical fixture/team: `560595/52`. Validation: **PASSED**. Context SHA-256: `6bbcc668e04ac128ed14cebbbc5669f8d3b938650b8ca344b14f5b3f506be293`. Response: `resp_06b8ce8780758ac5016ac6404d1e0487d2877d0b9a2d3c3623`.

Available retained candidates (fallbacks: 0):

- `560595:Chelsea:overall:WORST_DEFENCE`
- `560595:Bournemouth:overall:NO_CLEAN_SHEET_SEASON_START`
- `560595:Bournemouth:overall:WINLESS_SEASON_START`
- `560595:Chelsea:overall:NO_CLEAN_SHEET_SEASON_START`
- `560595:Bournemouth:overall:SCORING_LAST_5`

1. **SUPPORTED** — Bournemouth began the 2026 league campaign without a win in five matches between August 23 and September 20, scoring in four of those games.

   Cited: `560595:Bournemouth:overall:WINLESS_SEASON_START`; `560595:Bournemouth:overall:SCORING_LAST_5`.

2. **SUPPORTED** — Chelsea conceded 12 goals across their first five league matches, played between August 24 and September 18.

   Cited: `560595:Chelsea:overall:WORST_DEFENCE`.

Source/editorial assessment: Zero wins in five, four scoring matches; Chelsea conceded 12. Useful opposition context, but largely another dated form summary.

### Crystal Palace — Crystal Palace vs Nottingham

Canonical fixture/team: `560596/51`. Validation: **FAILED**. Context SHA-256: `33574045b71b543da098643697cba5f7406907d2ebd2a347d2c5c4a813479be6`. Response: `resp_019bffd62404de67016ac64053e51c87d28fcb6558c28a5ae1`.

Available retained candidates (fallbacks: 0):

- `560596:history:H2H_UNBEATEN_RUN:07697f590434d9ea`
- `560596:Crystal Palace:overall:RECENT_GOALS`
- `560596:history:H2H_UNBEATEN_RUN:d6e5a3718856d274`
- `560596:Crystal Palace:overall:SCORING_LAST_5`
- `560596:Nottingham:overall:NO_SCORING_LAST_5`

1. **SUPPORTED** — Crystal Palace went unbeaten in three league meetings with Nottingham from 5 May 2025 to 1 February 2026, drawing each match.

   Cited: `560596:history:H2H_UNBEATEN_RUN:d6e5a3718856d274`.

2. **SUPPORTED** — Across five matches from 22 August to 20 September 2026, Palace scored six goals but conceded 11.

   Cited: `560596:Crystal Palace:overall:RECENT_GOALS`. Lexical check: Unknown/uncited scoring actor.

3. **SUPPORTED** — Nottingham failed to score in three of its five matches between 22 August and 19 September 2026.

   Cited: `560596:Nottingham:overall:NO_SCORING_LAST_5`.

Source/editorial assessment: Three draws (all 1-1) on 5 May 2025, 24 August 2025 and 1 February 2026 establish the bounded unbeaten sequence. Six for/11 against and Nottingham’s three blanks are supported. Club abbreviation causes lexical rejection.

### Nottingham — Crystal Palace vs Nottingham

Canonical fixture/team: `560596/63`. Validation: **PASSED**. Context SHA-256: `fbd6a49e7f9603ffdcda17252de74c548577d22bfbe9086ef85b9b45cbd0e44b`. Response: `resp_0c0729ec0d834304016ac6405a3e8487d286ae3985df8bdcda`.

Available retained candidates (fallbacks: 0):

- `560596:history:H2H_UNBEATEN_RUN:07697f590434d9ea`
- `560596:Crystal Palace:overall:RECENT_GOALS`
- `560596:history:H2H_UNBEATEN_RUN:d6e5a3718856d274`
- `560596:Crystal Palace:overall:SCORING_LAST_5`
- `560596:Nottingham:overall:NO_SCORING_LAST_5`

1. **SUPPORTED** — Nottingham went unbeaten in four league meetings with Crystal Palace between 21 October 2024 and 1 February 2026.

   Cited: `560596:history:H2H_UNBEATEN_RUN:07697f590434d9ea`.

2. **SUPPORTED** — They failed to score in three of five matches played between 22 August and 19 September 2026.

   Cited: `560596:Nottingham:overall:NO_SCORING_LAST_5`.

Source/editorial assessment: A 1-0 win on 21 October 2024 followed by the three draws supports four unbeaten meetings. Three blanks in the five-match current sample. A worthwhile, differently bounded H2H angle.

### Coventry City — Coventry City vs Newcastle

Canonical fixture/team: `560597/117`. Validation: **FAILED**. Context SHA-256: `b6619aeee9ec575862d3667d00ea0e9aff477780088064fd7e9bdc77aeeb64de`. Response: `resp_0091f3dfe7cbb13f016ac64063098c87d29e5def0490266c11`.

Available retained candidates (fallbacks: 0):

- `560597:Newcastle:overall:SCORING_LAST_5`
- `560597:Coventry City:overall:NO_SCORING_LAST_5`
- `560597:Coventry City:overall:RECENT_GOALS`
- `560597:Newcastle:overall:RECENT_GOALS`
- `560597:Coventry City:overall:SEASON_START_RECORD`

1. **SUPPORTED** — Coventry City opened the season with one win, no draws and four defeats from five matches.

   Cited: `560597:Coventry City:overall:SEASON_START_RECORD`.

2. **SUPPORTED** — They scored once across five matches between 21 August and 19 September 2026, with four games ending without a Coventry goal.

   Cited: `560597:Coventry City:overall:RECENT_GOALS`; `560597:Coventry City:overall:NO_SCORING_LAST_5`. Lexical check: Unknown/uncited scoring actor.

3. **SUPPORTED** — Newcastle scored in all five matches between 23 August and 19 September 2026, recording nine goals while conceding nine.

   Cited: `560597:Newcastle:overall:SCORING_LAST_5`; `560597:Newcastle:overall:RECENT_GOALS`.

Source/editorial assessment: One win/four defeats; one total goal and four blanks. Newcastle scored in all five with nine for/nine against. Strong promoted-club context without invented promotion history; pronoun causes lexical rejection.

### Newcastle — Coventry City vs Newcastle

Canonical fixture/team: `560597/20`. Validation: **PASSED**. Context SHA-256: `eee802ad10a5e6fe8087600d467a4c5285308a2b0fa1d55cec2339dfa7572384`. Response: `resp_06b729f84bd9b146016ac6405f094c87d2a042de855a89e939`.

Available retained candidates (fallbacks: 0):

- `560597:Newcastle:overall:SCORING_LAST_5`
- `560597:Coventry City:overall:NO_SCORING_LAST_5`
- `560597:Coventry City:overall:RECENT_GOALS`
- `560597:Newcastle:overall:RECENT_GOALS`
- `560597:Coventry City:overall:SEASON_START_RECORD`

1. **SUPPORTED** — Newcastle scored in all five matches played between 23 August and 19 September 2026.

   Cited: `560597:Newcastle:overall:SCORING_LAST_5`.

2. **SUPPORTED** — Coventry City failed to score in four of their five matches between 21 August and 19 September 2026, scoring just once across that run.

   Cited: `560597:Coventry City:overall:NO_SCORING_LAST_5`; `560597:Coventry City:overall:RECENT_GOALS`.

Source/editorial assessment: Scored in all five; Coventry’s four blanks and one goal are supported. Good contrasting scoring patterns, although Coventry’s one goal/four blanks partly repeats the same information.

### Liverpool — Liverpool vs Man City

Canonical fixture/team: `560598/8`. Validation: **PASSED**. Context SHA-256: `b618b49784051fb81fd3741ffd8e1f90357dd09015d87a531ef1428d849a473a`. Response: `resp_08975b339952358d016ac6406bd87c87d2a5bd50c41885e909`.

Available retained candidates (fallbacks: 0):

- `560598:Man City:overall:SEASON_START_RECORD`
- `560598:Man City:overall:SCORING_LAST_5`
- `560598:history:LATE_DECISIVE_GOAL:617bd4d1d3ea3103`
- `560598:Liverpool:overall:UNBEATEN_SEASON_START`
- `560598:Man City:overall:RECENT_GOALS`

1. **SUPPORTED** — Liverpool began the 2026 season unbeaten through five league matches, while Man City won all five of theirs.

   Cited: `560598:Liverpool:overall:UNBEATEN_SEASON_START`; `560598:Man City:overall:SEASON_START_RECORD`.

2. **SUPPORTED** — Man City scored in each of those five matches, totaling 13 goals and conceding five.

   Cited: `560598:Man City:overall:SCORING_LAST_5`; `560598:Man City:overall:RECENT_GOALS`.

3. **SUPPORTED** — In the 2025/2026 meeting at Liverpool, Erling Haaland scored for Man City at 90+3 to make it 2-1.

   Cited: `560598:history:LATE_DECISIVE_GOAL:617bd4d1d3ea3103`.

Source/editorial assessment: Liverpool unbeaten in five; City won all five, scored 13/conceded five. Haaland’s goal for City at 90+3 in the 8 February 2026 away 2-1 win is supported. Two sentences concentrate on the opposition.

### Man City — Liverpool vs Man City

Canonical fixture/team: `560598/9`. Validation: **PASSED**. Context SHA-256: `9e5fc1d79008112dd409b065b39fbfd3c6efd6c73558812a875abcaef0a52266`. Response: `resp_0978c5966742a3a8016ac64073253487d2b69a32392031acec`.

Available retained candidates (fallbacks: 0):

- `560598:Man City:overall:SEASON_START_RECORD`
- `560598:Man City:overall:SCORING_LAST_5`
- `560598:history:LATE_DECISIVE_GOAL:617bd4d1d3ea3103`
- `560598:Liverpool:overall:UNBEATEN_SEASON_START`
- `560598:Man City:overall:RECENT_GOALS`

1. **SUPPORTED** — Man City won all five of their league matches from 23 August to 20 September 2026, scoring in every one.

   Cited: `560598:Man City:overall:SEASON_START_RECORD`; `560598:Man City:overall:SCORING_LAST_5`.

2. **SUPPORTED** — Erling Haaland scored for Manchester City at 90+3 to make it 2-1 away to Liverpool on 8 February 2026.

   Cited: `560598:history:LATE_DECISIVE_GOAL:617bd4d1d3ea3103`.

Source/editorial assessment: Five wins, scoring in every match; Haaland’s named 90+3 goal and 8 February 2026 Liverpool venue/result are supported. One of the clearer combinations of current form and a memorable event.

### Fulham — Ipswich Town vs Fulham

Canonical fixture/team: `560599/11`. Validation: **PASSED**. Context SHA-256: `022745c9678f9845899b71c99dc8aa4d04f15e728f610f6a54f3d4063f8d2f0c`. Response: `resp_0c183ad6a9a16fdf016ac6407a5aa487d2b63e6ef3b71ecef8`.

Available retained candidates (fallbacks: 0):

- `560599:Fulham:overall:WINLESS_SEASON_START`
- `560599:Ipswich Town:overall:NO_CLEAN_SHEET_SEASON_START`
- `560599:Ipswich Town:overall:RECENT_GOALS`
- `560599:history:LATE_DECISIVE_GOAL:8ee2aab37a28d85a`
- `560599:Fulham:overall:SEASON_START_RECORD`

1. **SUPPORTED** — Fulham began the 2026 season without a win in five matches, drawing twice and losing three times between 24 August and 20 September.

   Cited: `560599:Fulham:overall:WINLESS_SEASON_START`; `560599:Fulham:overall:SEASON_START_RECORD`.

2. **SUPPORTED** — Ipswich Town went without a clean sheet in their first five matches from 22 August to 19 September, scoring seven goals and conceding 11.

   Cited: `560599:Ipswich Town:overall:NO_CLEAN_SHEET_SEASON_START`; `560599:Ipswich Town:overall:RECENT_GOALS`.

3. **SUPPORTED** — In the 2-2 meeting on 5 January 2025, Raúl Jiménez scored for Fulham at 90+1 to equalise against Ipswich Town.

   Cited: `560599:history:LATE_DECISIVE_GOAL:8ee2aab37a28d85a`.

Source/editorial assessment: Two draws/three defeats; Ipswich zero clean sheets, seven for/11 against. Jiménez scored Fulham’s 90+1 equaliser in the 2-2 on 5 January 2025. Useful form plus event; three distinct sentences.

### Ipswich Town — Ipswich Town vs Fulham

Canonical fixture/team: `560599/116`. Validation: **PASSED**. Context SHA-256: `22f1f22ce25a86ea73abd6ee9ced616e7ffff3d3bcd6b8e712553360afbf451b`. Response: `resp_0ffa25083de31ef9016ac64080db1087d2878c41606fa32c59`.

Available retained candidates (fallbacks: 0):

- `560599:Fulham:overall:WINLESS_SEASON_START`
- `560599:Ipswich Town:overall:NO_CLEAN_SHEET_SEASON_START`
- `560599:Ipswich Town:overall:RECENT_GOALS`
- `560599:history:LATE_DECISIVE_GOAL:8ee2aab37a28d85a`
- `560599:Fulham:overall:SEASON_START_RECORD`

1. **SUPPORTED** — In Fulham's 2-2 home draw with Ipswich Town on 5 January 2025, Raúl Jiménez scored for Fulham at 90+1 to equalise.

   Cited: `560599:history:LATE_DECISIVE_GOAL:8ee2aab37a28d85a`.

2. **SUPPORTED** — Fulham began this season without a win in five matches between 24 August and 20 September 2026, drawing two and losing three.

   Cited: `560599:Fulham:overall:SEASON_START_RECORD`.

3. **SUPPORTED** — Ipswich Town conceded in each of their opening five matches between 22 August and 19 September 2026, scoring seven goals and conceding 11 overall.

   Cited: `560599:Ipswich Town:overall:NO_CLEAN_SHEET_SEASON_START`; `560599:Ipswich Town:overall:RECENT_GOALS`.

Source/editorial assessment: Same named Fulham goal, date, home venue and 2-2 result; Fulham two draws/three defeats; Ipswich conceded in all five, seven for/11 against. Supported, but two opposition-led sentences delay Ipswich’s own perspective.

### Man United — Man United vs Tottenham

Canonical fixture/team: `560600/14`. Validation: **FAILED**. Context SHA-256: `ae1af5b3d7c75ffd198ef8445ede4eeeafcef5f2dcc10a487e76bb3bd06f0759`. Response: `resp_085eadf0db68d9d5016ac64098095c87d2b6e723e165d88b38`.

Available retained candidates (fallbacks: 0):

- `560600:Man United:overall:NO_CLEAN_SHEET_SEASON_START`
- `560600:Tottenham:overall:WINLESS_SEASON_START`
- `560600:Tottenham:overall:NO_SCORING_LAST_5`
- `560600:history:LATE_DECISIVE_GOAL:d3dc163b964e61de`
- `560600:Tottenham:overall:SEASON_START_RECORD`

1. **SUPPORTED** — Man United began the 2026/27 Premier League season without a clean sheet in five straight matches from 22 August to 20 September, recording two defeats, two draws and a win.

   Cited: `560600:Man United:overall:NO_CLEAN_SHEET_SEASON_START`.

2. **SUPPORTED** — Tottenham were winless across their opening five Premier League matches from 22 August to 19 September, with three defeats and two draws, and failed to score in four of those games.

   Cited: `560600:Tottenham:overall:WINLESS_SEASON_START`; `560600:Tottenham:overall:NO_SCORING_LAST_5`.

3. **SUPPORTED** — At Tottenham on 8 November 2025, Matthijs de Ligt scored for Man United at 90+6 to equalise in a 2-2 draw.

   Cited: `560600:history:LATE_DECISIVE_GOAL:d3dc163b964e61de`. Lexical check: Unknown/uncited scoring actor.

Source/editorial assessment: One win/two draws/two defeats and no clean sheets; Tottenham zero wins, three defeats/two draws, four blanks. De Ligt’s United equaliser at 90+6 in the 8 November 2025 away 2-2 is supported. Name parsing causes lexical rejection.

### Tottenham — Man United vs Tottenham

Canonical fixture/team: `560600/6`. Validation: **FAILED**. Context SHA-256: `5a661cdee99fa850efe9e2a88b64d5d7e3d39c5b39bd6ce3653bb94a8a2abd89`. Response: `resp_04d511c4aeeecb19016ac6408ead5c87d2a58d225f2c194dc5`.

Available retained candidates (fallbacks: 0):

- `560600:Man United:overall:NO_CLEAN_SHEET_SEASON_START`
- `560600:Tottenham:overall:WINLESS_SEASON_START`
- `560600:Tottenham:overall:NO_SCORING_LAST_5`
- `560600:history:LATE_DECISIVE_GOAL:d3dc163b964e61de`
- `560600:Tottenham:overall:SEASON_START_RECORD`

1. **SUPPORTED** — Tottenham began the 2026 season without a win in five matches, drawing two and losing three, and failed to score in four of them.

   Cited: `560600:Tottenham:overall:SEASON_START_RECORD`; `560600:Tottenham:overall:NO_SCORING_LAST_5`.

2. **SUPPORTED** — Man United also opened the season without a clean sheet across their first five matches.

   Cited: `560600:Man United:overall:NO_CLEAN_SHEET_SEASON_START`.

3. **SUPPORTED** — In the 8 November 2025 meeting at Tottenham, Matthijs de Ligt scored for Manchester United at 90+6 to make it 2-2.

   Cited: `560600:history:LATE_DECISIVE_GOAL:d3dc163b964e61de`. Lexical check: Unknown/uncited scoring actor.

Source/editorial assessment: Two draws/three defeats and four blanks; United no clean sheets. De Ligt scored for Manchester United at 90+6 in the 8 November 2025 2-2 at Tottenham. Supported, with a genuine late-goal hook; name parsing causes rejection.

### Aston Villa — Aston Villa vs Brentford

Canonical fixture/team: `560601/15`. Validation: **PASSED**. Context SHA-256: `2be3f2f8ed172417c86a4549b2b2c6b28e1ba5c56b7faab489eecabc1ca3d451`. Response: `resp_025a12b7fa50deca016ac640a3eb9887d2807d831ab2de512a`.

Available retained candidates (fallbacks: 0):

- `560601:Brentford:overall:SCORING_LAST_5`
- `560601:Brentford:overall:UNBEATEN_SEASON_START`
- `560601:Brentford:overall:RECENT_GOALS`
- `560601:Aston Villa:overall:RECENT_GOALS`
- `560601:Aston Villa:overall:NO_SCORING_LAST_5`

1. **SUPPORTED** — Aston Villa went without a goal in three of five matches between 23 August and 19 September 2026, scoring four and conceding nine across that span.

   Cited: `560601:Aston Villa:overall:NO_SCORING_LAST_5`; `560601:Aston Villa:overall:RECENT_GOALS`.

2. **UNSUPPORTED** — Brentford scored in each of five matches from 22 August to 18 September 2026 and remained unbeaten throughout, with three wins and two draws.

   Cited: `560601:Brentford:overall:SCORING_LAST_5`; `560601:Brentford:overall:UNBEATEN_SEASON_START`.

Source/editorial assessment: Three blanks, four scored/nine conceded. Brentford scored in all five and were unbeaten, but their record is two wins/three draws, not three wins/two draws; see blocker 2.

### Brentford — Aston Villa vs Brentford

Canonical fixture/team: `560601/236`. Validation: **PASSED**. Context SHA-256: `278dad2f2d13dd68af18156c0e89b45436ac887eb5652d9a7e7adff28512bd16`. Response: `resp_0b561913ec08fbde016ac640ac203887d2a6bf90af5aabc401`.

Available retained candidates (fallbacks: 0):

- `560601:Brentford:overall:SCORING_LAST_5`
- `560601:Brentford:overall:UNBEATEN_SEASON_START`
- `560601:Brentford:overall:RECENT_GOALS`
- `560601:Aston Villa:overall:RECENT_GOALS`
- `560601:Aston Villa:overall:NO_SCORING_LAST_5`

1. **SUPPORTED** — Brentford scored in all five of their opening league matches of 2026, from 22 August to 18 September, while remaining unbeaten across that run.

   Cited: `560601:Brentford:overall:SCORING_LAST_5`; `560601:Brentford:overall:UNBEATEN_SEASON_START`.

2. **SUPPORTED** — Aston Villa scored four and conceded nine across five matches from 23 August to 19 September, failing to score in three of them.

   Cited: `560601:Aston Villa:overall:RECENT_GOALS`; `560601:Aston Villa:overall:NO_SCORING_LAST_5`.

Source/editorial assessment: Scoring in all five and unbeaten; Villa four for/nine against with three blanks. Correct alternative perspective, though the prose again follows the form-plus-opponent template.

## Test verification and scope

After the controlled evaluation, **463 Python tests passed** (`.venv/Scripts/python.exe -m unittest discover -s tests`, 55.379 seconds) and **10 JavaScript tests passed** (`node --test tests/bulk_picks.test.cjs tests/pick_confirm.test.cjs`). Expected negative-test CLI diagnostics appear in the Python log; the complete suite ended `OK` with exit code zero.

Evaluation changes are documentation only; the engine, PR24 ranking, prompt, validators, persistence and production behavior remain unchanged. Unrelated local changes and `.env` are preserved. No merge or deployment occurred.
