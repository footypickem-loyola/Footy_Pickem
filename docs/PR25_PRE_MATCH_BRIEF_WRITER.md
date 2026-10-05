# PR25 — AI Pre-Match Brief Writer

This adds a local, opt-in writing layer over the existing fixture-intelligence
engine. It does not research football facts. The model selects and paraphrases
supplied facts for a player's five committed picks. There is no production entry
point, automatic generation, delivery, UI or persisted brief table.

## Architecture and trust boundary

`pre_match_brief.build_pre_match_context` reads one SQLite transaction using
`mode=ro` and `PRAGMA query_only=ON`, without importing the Flask application.
It reads the requested player's persisted `picks` rows only, then calls the
unchanged PR24 fixture-intelligence engine. Five rows, five distinct fixtures in
one player matchup/week, valid selected teams, known kickoff times and commit
timestamps at or before `as_of` are required. A partial slate raises
`BriefNotReady`. Bulk confirmation locks an auto-draft preference queue; it does
not establish which five fixtures were allocated. Neither confirmed nor
unconfirmed preferences are read, and no opponent's picks enter the context.

The explicit as-of must precede the first kickoff of the entire week. This is
stricter than merely preceding each selected match. Target-week results are
excluded before invoking the engine; earlier official results still require
their existing `Result.updated_at` cutoff. Historical kickoff/gap handling is
unchanged. No timestamps are backdated or reconstructed. The intended 12+ hour
lead time is a future scheduling policy, not an implemented scheduler or cutoff
relaxation. Naive timestamps follow the repository's UTC convention.

Entries are ordered by kickoff then fixture ID. `pick_order` independently records
the player's commit sequence, ordered by `created_at` then pick ID. Only the first
five retained candidates per fixture are supplied (configurable down to one).
Filtered raw facts are never restored. Empty candidate lists are valid.

`correspondent.pre_match_writer` uses the existing `CorrespondentError`,
`DEFAULT_MODEL`, `OPENAI_MODEL`, `OPENAI_API_KEY`, lazy OpenAI import and Responses
API pattern. The SDK uses a 90-second timeout and two transport retries, matching
Correspondent V2. There is no semantic repair/retry pass. Existing weekly recap
code is unchanged. The request explicitly has `tools=[]`, `store=False` and a
strict JSON schema. No API calls occur during imports, tests or context preview.

## Exact context contract

Top-level keys (all required; unknown keys rejected):

```text
context_version: "pre-match-context-v1"
prompt_version: "pre-match-brief-v1"
season: {id: integer, code: string, name: string}
week: {id: integer, number: integer}
player: {id: integer, name: string}
as_of: UTC ISO timestamp
earliest_kickoff: earliest selected fixture's UTC ISO timestamp
week_first_kickoff: earliest kickoff of the entire matchweek
candidate_limit: integer 1..5
versions: {fixture_intelligence: "fixture-intelligence-v3",
           historical_intelligence: "historical-fixture-intelligence-v2"}
slate_status: "committed"
availability: string[] of snapshot/sample limitations
picks: [exactly five Pick objects]
```

Each Pick has exactly:

```text
pick_id: positive integer
committed_at: UTC ISO timestamp
pick_order: integer 1..5, unique across the slate
fixture_id: positive integer, unique across the slate
home, away, picked_team, opponent: strings
kickoff: UTC ISO timestamp
picked_team_venue: "home" | "away"
packet_version: "fixture-intelligence-v3"
candidates: Candidate[] (0..candidate_limit, original retained order)
```

Each Candidate has exactly `id`, `signal_type`, `family`, `subject_team`, `claim`,
`editorial_score`, `scope`, `evidence`, `sample`, `provenance`, `confidence`,
`recomputability`. The first fields identify the original fact; the score is the
original integer score, at least 60. The last five are structured objects from the
engine. `evidence` preserves player identities, goals, distinct scoring meetings,
lower bounds and other detector-specific facts. `sample` preserves size, fixture
IDs, window and start/end dates. `provenance` keeps source, fixture/result rows and
reference/external event IDs where present; provider event annotation blobs are
omitted. `recomputability` retains version, cutoff and evidence fingerprints.
These fields are data, never model instructions.

The builder is the authority boundary. A separate local validator checks shape,
identity, ordering, caps, versions, timestamps and finite JSON serialization
before any provider call. It cannot authenticate a caller-forged claim or prove
that someone hand-editing a context retained only ranked candidates. Callers must
use the builder; no external context upload endpoint exists.

## Exact model output contract

`response_schema(context)` is the schema passed to Responses under
`text.format`, `type=json_schema`, `strict=true`. Objects disallow additional
properties; all listed fields are required:

```json
{
  "title": "string",
  "intro": "string",
  "fixtures": [
    {
      "fixture_id": 771,
      "picked_team": "Man City",
      "heading": "Crystal Palace vs Man City",
      "body": "string",
      "used_candidate_ids": ["supplied candidate ID"]
    }
  ]
}
```

The array above illustrates one item's shape: actual responses must contain
exactly **five**. The schema uses the supplied fixture IDs as an integer enum and
limits citations to at most three strings per item. Runtime validation additionally
requires the complete unique fixture set in context order, exact picked teams,
exact `home + " vs " + away` headings, unique citations belonging to that fixture,
and nonempty text. Missing/extra properties, boolean fixture IDs, duplicate JSON
object keys, nonfinite constants, malformed JSON and incomplete responses fail.
Unknown or cross-fixture candidate IDs fail; nothing is silently repaired.

Title is at most 12 words, intro 35, each body 90, total 400 excluding headings/IDs.
Editorial targets are softer: roughly 250–350 overall and 40–70 per fixture, usually
2–3 sentences. Thin context may legitimately produce much less. These whitespace
word caps are a size guard, not a measure of prose quality or reading speed.

The returned `GeneratedPreMatchBrief` adds `model`, `provider_response_id` when
available, `prompt_version`, `context_version`, and a SHA-256 of the exact serialized
context. `to_dict()` exposes JSON-ready output; these fields are added by Python,
not trusted model assertions. No output is saved unless the CLI receives `--output`.

## Prompt and grounding protections

The versioned prompt treats every embedded string as data, including malicious
instructions in names or claims. It allows editorial selection and paraphrase
but forbids outside knowledge, tools, injuries, tactics, roster assumptions,
appearance rates, invented causal links, predictions, all-time extrapolation and
unsupported records. Historical samples must stay described as stored meetings.
Pick personalization must not reverse a fact's subject. Thin entries identify the
selection neutrally rather than pad the paragraph. No sportsbook copy, pick
recommendations, generic excitement or post-match recap roasting.

**Validation guarantees structure and citation membership, not semantic entailment.**
A response could cite a valid candidate while adding an unsupported assertion.
That is still invalid editorially but is not reliably detected by this ID validator.
The prompt is a preventive instruction, not proof against hallucination or prompt
injection. No live model quality evaluation was run for this PR. Human review of
each assertion against its cited evidence remains necessary before production use.

Responses format follows the [official Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses);
the local validator adds the fixture-specific consistency checks.

## Inspect exact input locally — no API key required

Use the supplied snapshot or a disposable copy. Nothing imports Flask, initializes
tables or writes SQLite. These commands use actual complete persisted slates from
the verified snapshot, not mixed-week or fabricated picks:

```powershell
.venv\Scripts\python.exe scripts/review_pre_match_brief.py --db local_data/pickem_with_reference_snapshot.db --season 2 --week 2 --player-id 3 --as-of 2026-08-27T19:00:00Z --context-only --output local_data/pr25_week2_context.json
.venv\Scripts\python.exe scripts/review_pre_match_brief.py --db local_data/pickem_with_reference_snapshot.db --season 2 --week 2 --player-id 3 --as-of 2026-08-27T19:00:00Z --request-only --output local_data/pr25_week2_request.json
.venv\Scripts\python.exe scripts/review_pre_match_brief.py --db local_data/pickem_with_reference_snapshot.db --season 2 --week 5 --player-id 2 --as-of 2026-09-17T19:00:00Z --context-only --output local_data/pr25_week5_context.json
```

`--context-only` emits just the deterministic authoritative JSON. `--request-only`
emits the **exact API kwargs**, including instructions, serialized input, schema,
selected model and tool/storage settings. Neither calls OpenAI. Omit `--output`
to print. Keep the same immutable snapshot, as-of, candidate cap and model when
comparing the preview with generation. Explicit empty model overrides are errors;
an absent override/environment setting uses the existing Correspondent default.

Only when deliberately supplying credentials locally, change the mode to
`--generate` to make a real call. Do not commit the resulting API output:

```powershell
.venv\Scripts\python.exe scripts/review_pre_match_brief.py --db local_data/pickem_with_reference_snapshot.db --season 2 --week 2 --player-id 3 --as-of 2026-08-27T19:00:00Z --generate --output local_data/pr25_generated_brief.json
```

The CLI requires an explicit mode and permits only a `.json` output distinct from
the database, including a hard-link/symlink identity check. It does not load `.env`
files or schedule calls. Missing credentials/package, invalid context, not-ready
slates and invalid model responses are visible errors with a nonzero exit status.

## Representative fixtures and mocked validation

Two committed human-readable test contexts are exact builder output:
`tests/fixtures/pre-match-week2-context.json` and `pre-match-week5-context.json`.
Together they cover all requested cases:

- Week 2, player 3: Palace–City 771 (notable prior 0–3, unbeaten four, Haaland
  brace); Chelsea–Brighton 777 (Palmer four goals, late winner, exact three-goal
  sequence); Villa–Arsenal 780 (late winner and Trossard scoring in three distinct
  stored meetings); Leeds–Brentford 778 (zero retained candidates); Sunderland–Fulham
  776 (one retained brace). Those are all five actual picks for this player/week.
- Week 5, player 2: Brentford–Chelsea 801 supplies three retained candidates,
  with the duplicate home-winless formulation absent. The player's other four
  committed picks remain included; Forest–Coventry 808 is another thin entry.

Both keep PR23's strict September cutoffs. October-refreshed official results
remain unavailable for current-season claims; historical facts retain their
corrected-snapshot limitation. These contexts are grounding inputs, not proof of
what the engine knew at that historical instant.

`tests/fixtures/pre-match-mock-output.json` is explicitly **ILLUSTRATIVE MOCK**:
no model call, no writing-quality claim. It mechanically copies one supplied fact
per rich fixture and identifies the selection for the zero-signal fixture.
The test validates it against the Week 2 context. Examples of deliberately rejected
mutations are a Palace candidate cited under Chelsea–Brighton, an invented candidate
ID, a picked-team swap, duplicate fixture/citation IDs, or a missing fifth fixture.

For a later live editorial review, check every sentence, not just citations: correct
date/venue and subject, preserved lower bounds, no assumed appearances/roster,
nonredundant selection, natural personalization, concise thin entries and no
unsupported causal linkage. Record model/prompt/context hash with any assessment.

## Validation and scope

**407 Python tests and 10 JavaScript tests passed**, including 28 new offline
pre-match tests. Repeated Week 2 context generation was byte-identical to the
committed example. No real OpenAI API calls were made.

Focused tests cover committed-slate readiness, ordering, deterministic context,
top-retained caps, provenance, zero facts, cutoff/future-result exclusion, query-only
reads, CLI modes/output protection, exact request inspection, all schema/ID failure
paths, prompt loading, missing configuration/package, mock transport, SDK timeout
and retries, and no Flask/OpenAI import side effects.

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests/bulk_picks.test.cjs tests/pick_confirm.test.cjs
```

The verified database SHA-256 remains
`73c37c2cceff71f360dbf36f55ecadc1a9ad05143ed0cbf93c369794df35bef4`.
Existing fixture detectors/rankings, PR23/PR24 artifacts, weekly recap, Pick Insight
and all runtime routes are unchanged. No production deployment, database changes,
Railway, n8n, UI, scheduler, external ingestion, email or secrets were added.
