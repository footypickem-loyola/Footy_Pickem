# PR25 — local pre-match brief writer

Opt-in football prose for five committed picks, using PR24 retained intelligence
and a separate deterministic fallback layer. No production route, scheduler,
delivery, UI, database migration, deployment, Railway/n8n integration or changes
to Pick Insight and weekly recap.

## Read-only context and cutoff

`pre_match_brief.build_pre_match_context` opens one SQLite transaction with
`mode=ro` and `PRAGMA query_only=ON`, without importing Flask. Five persisted picks
for one player/matchup/week, distinct fixtures, valid teams, known kickoffs and
commit timestamps at or before `as_of` are required. Preference queues and other
players' picks are not read. Partial slates raise `BriefNotReady`.

As-of must precede the first kickoff of the entire week. Historical fixtures must
precede cutoff; official results additionally require `Result.updated_at` before
cutoff. Target-week results are excluded. No timestamps are reconstructed.
Reference data remains a corrected snapshot, not proof of exactly what was known
historically. October-refreshed official results remain unavailable for September.

Entries follow kickoff then ID; `pick_order` separately preserves commit order.
Versions: `pre-match-context-v2` / `pre-match-brief-v2`; intelligence versions
remain `fixture-intelligence-v3` / `historical-fixture-intelligence-v2`.

## Primary facts and separate fallbacks

Each pick contains `candidates` and `fallback_facts`. The builder takes at most
five PR24 retained candidates (configurable cap 1–5). Original IDs, scores,
evidence, sample, confidence, provenance IDs and recomputability stay intact.
Writer-only `writing` metadata supplies authoritative scorer names/event IDs,
distinct scoring-meeting counts and explicit comparison scopes. Anonymous goal
stories are ineligible for writing. Raw engine packets, ranking, suppression and
retention thresholds do not change.

`pre_match_facts.py` fills toward two usable facts when fewer than two eligible
editorial facts remain, with at most two fallbacks in this order:

1. Most recent available pre-cutoff H2H result.
2. Picked team's most recent eligible completed league result.
3. Current-season leading scorer(s), only with a complete named pre-cutoff goal
   ledger for that club in the available reference season; joint leaders stay joint.
4. Another result anchor for the opponent.

Duplicate anchors already represented by primary facts are skipped. Reference
queries exclude dated fixtures at/after cutoff before reading their events.
Unknown kickoff/incomplete rows cannot supply results and prevent exact scorer
totals. Official results retain the updated-at cutoff. Missing/inconsistent goal
ledgers permit team results without scorer claims. Own goals are excluded from
player totals. No filtered PR24 candidates are promoted.

Fallback fields: `id, signal_type, claim, evidence, provenance, writing, cutoff`.
IDs are deterministic and fixture-bound; there is **no editorial score**.
`writing.role` is `fallback` rather than `editorial`. Recency scope is explicitly
limited to available eligible history; prefer dates in prose.

If any fixture has neither eligible editorial facts nor an authoritative fallback,
the whole brief is `BriefNotReady`. It must not fill an empty entry with a selection
or missing-data explanation. The builder is the authority boundary: shape/cutoff
validation cannot authenticate caller-forged facts. No context-upload endpoint exists.

## Writer and sentence citations

The Responses request follows existing model/environment conventions: lazy SDK,
90-second timeout, two transport retries, `tools=[]`, `store=False`, strict JSON
Schema. There is **no second AI repair pass**. Imports, previews and tests make
no API calls. The original v1 prompt is preserved; v2 is the active prompt.

Title and intro are exact metadata-only enums. Each of five fixture variants has
exact fixture/team/heading enums and 1–3 sentence objects:

```json
{
  "fixture_id": 778,
  "picked_team": "Brentford",
  "heading": "Leeds United vs Brentford",
  "sentences": [
    {"text": "Football prose", "used_fact_ids": ["exact fact ID"]}
  ]
}
```

Every sentence cites 1–3 distinct facts from that fixture via schema enums.
Runtime rejects wrong order/fixtures/teams/headings, extra fields, malformed JSON,
duplicate keys/IDs, unknown/cross-fixture IDs and incomplete responses. Specific
goal facts require the supplied full scorer name in that sentence. Known-but-uncited
players and recognizable unknown scoring actors fail. Winner/equaliser labels,
numeric goal minutes and own-goal wording are checked against cited facts.
Recency wording requires explicit scope; internal vocabulary is rejected.
Football content is required, at most 90 words per body and 400 overall.

After validation, Python joins unchanged sentences into `body` and adds the
first-seen union of `used_fact_ids`. Original candidate IDs are unchanged;
fallbacks have the `:fallback:` namespace. The returned object retains sentences,
model, response ID, prompt/context versions and exact serialized context SHA-256.
Rendering never repairs model output.

The prompt requires named scorers, date/venue scope, distinct-meeting recurrence
and natural football prose. No audit jargon, appearance/roster assumptions, tactics,
injuries, predictions, causal inventions, betting copy or season-stage inference.
Thin fixtures may use one sentence; word targets are not quotas.

**Lexical checks/citations are not a general semantic-entailment proof.**
Paraphrases, arbitrary new names or swapped relationships can escape them.
Human clause-level review remains necessary. A prompt prohibition can be violated;
the live review shows validation rejecting that output without rewriting it.

## Local commands

The CLI does not load `.env`, write SQLite or import Flask. These are genuine
complete persisted slates from the reference snapshot:

```powershell
.venv\Scripts\python.exe scripts/review_pre_match_brief.py --db local_data/pickem_with_reference_snapshot.db --season 2 --week 2 --player-id 3 --as-of 2026-08-27T19:00:00Z --context-only --output local_data/pr25_week2_context.json
.venv\Scripts\python.exe scripts/review_pre_match_brief.py --db local_data/pickem_with_reference_snapshot.db --season 2 --week 2 --player-id 3 --as-of 2026-08-27T19:00:00Z --request-only --output local_data/pr25_week2_request.json
.venv\Scripts\python.exe scripts/review_pre_match_brief.py --db local_data/pickem_with_reference_snapshot.db --season 2 --week 5 --player-id 2 --as-of 2026-09-17T19:00:00Z --context-only --output local_data/pr25_week5_context.json
```

With deliberately supplied local credentials, replace the mode with `--generate`.
Do not commit raw generated output. Output must be a JSON file distinct from
SQLite, including hard-link/symlink identity checks. Missing credentials, not-ready
context and invalid output are visible errors. No automatic generation or saving.

The local evaluation wrapper additionally captures exact requests/provider output
before validation, including rejected responses. Credentials are never saved.
See `PR25_V2_PROSE_REVIEW.md` for the compact reviewed comparison; full artifacts
and sentence-level assessment remain local.
The subsequent prompt-only refinement is assessed in `PR25_FINAL_EDITORIAL_REVIEW.md`;
its Week 5 response remains rejected by the unchanged validator.

## Examples and tests

Committed Week 2/player 3 and Week 5/player 2 contexts are exact v2 builder output.
Leeds–Brentford still has zero editorial candidates, but gets the March 2026 0–0
and Brentford's May draw at Liverpool with authoritative scorers. Forest–Coventry
gets one Forest result; Sunderland's brace is supplemented by its Chelsea result.
Rich entries continue to use PR24 facts unchanged. The committed output mock is
explicitly **ILLUSTRATIVE MOCK**, testing the contract rather than prose quality.

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests/bulk_picks.test.cjs tests/pick_confirm.test.cjs
```

Tests cover readiness, unchanged engine packets/candidate projection, deterministic
fallbacks, cutoff boundaries, no future event reads, named/anonymous goals,
incomplete season totals, citations, wrong scorers/minutes, recency scope,
metadata intros, possessive names, no repair calls, read-only SQLite and CLI errors.
The complete PR19–PR24 suites are included.

Final revision validation: **416 Python tests and 10 JavaScript tests passed**.
Both committed contexts reproduced byte-for-byte from the read-only source.

Verified database SHA-256:
`73c37c2cceff71f360dbf36f55ecadc1a9ad05143ed0cbf93c369794df35bef4`.
No source snapshot, unrelated local file or production service is changed.
