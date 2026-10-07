# PR26 architecture decision — shared Premier League briefs

Recorded before implementing orchestration/persistence.

## Trigger

Use an explicit operator CLI batch, normally about 12 hours before the first
kickoff, after the official schedule/reference refresh. The batch enumerates ten
official PL fixtures and both participating clubs: exactly twenty content entries
per matchweek/version, even with no picks. It can be invoked again to retry failed
entries. No scheduler, Railway/n8n wiring or production execution in this PR.
Training Ground GET/refresh only reads saved content and can never call OpenAI.

## Canonical mapping

Use existing official `Season.api_competition_code`, `api_season_year` and
`Fixture.external_match_id` (football-data identity), plus verified Sportmonks
club IDs from the existing explicit mapping. Never key shared content by a Footy
league/season/week/fixture database primary key, player, matchup or pick. Canonical
key: `(football-data, PL, season_year, external_match_id, team_id, content_version)`.
Source-local IDs are used only to join existing official rows, then translated
before building intelligence. Unknown/ambiguous mappings fail closed. The existing
verified club map supports 2026/27; do not invent mapping for other seasons.

## Storage and retries

Use a separate explicitly initialized SQLite content database shared by all local
league consumers. It has no game tables/foreign keys and refuses initialization
over an unrelated database. Source game/reference databases are opened read-only.
One batch manifest freezes all twenty deterministic contexts and their cutoff,
prompt/model/version identity. Entry rows contain context hash, status, lease/token,
attempt/error metadata, exact validated prose/citations and provider response ID.
Unique canonical entry keys and transactional claims prevent parallel workers
from generating the same pending entry. Successful entries are immutable for the
version. No database transaction stays open during provider I/O. Generation and
acceptance must precede that fixture's kickoff; schedule changes fail closed.

Known failures may be explicitly retried; expired in-flight claims become uncertain
and require an explicit operator retry choice. A remote API cannot guarantee
exactly-once execution across a lost response/crash. Do not claim otherwise or
silently regenerate an uncertain success. Fencing prevents stale workers from
overwriting newer claims or completed content.

All web workers/leagues must use the same content store, not separate copies.
This implementation targets the existing SQLite deployment model. Multiple hosts
without a shared transactional store require a central database/service before
activation; independent per-league stores cannot satisfy global deduplication.

## PR25 reuse and presentation

Extract the existing fact projection/validation and per-entry output validation
into reusable functions without changing their behavior. Add an independent
single-fixture/team context and writer, using PR25's editorial safeguards with
only the output/cardinality and shared-team framing adapted. Preserve the PR25
five-pick API/tests. Intelligence, retention, ranking, fallback facts, scorer
identities, recency checks and strict citation/minute checks remain unchanged.

Training Ground maps each of the selected player's five picks to canonical
fixture/team identity and reads the matching successful version. Render a compact
five-bullet list, with a neutral availability state for missing content. Incomplete
picks do not block global generation. Missing/corrupt/unconfigured content never
affects picks, official results, scoring or payouts.

## Implemented boundaries and operation

- `shared_brief_context.py` enumerates official canonical matchweeks without
  selecting from players, matchups or picks. Conflicting duplicate schedule/result
  copies fail closed. Both team perspectives have canonical fact-ID namespaces.
- `correspondent/shared_team_writer.py` adapts PR25's actual editorial rules and
  reuses its exact single-entry validator. The five-pick API remains available.
- `shared_brief_store.py` owns only its separate content database. Initialization
  rejects game/reference databases. Successful entry rows are immutable. Reads
  open `mode=ro`; missing or invalid content is simply unavailable.
- `shared_brief_workflow.py` freezes the first batch cutoff/context, claims work in
  short SQLite transactions, checks the official mapping/kickoff before and after
  I/O, validates output and stores it only before kickoff. SDK retries are disabled.
- `shared_brief_view.py` maps the displayed five picks to saved canonical entries.
  Full page and HTMX refreshes never enqueue or generate. Jinja escapes saved prose.

Explicit local commands (examples only; never run against production here):

```powershell
.venv\Scripts\python.exe scripts/generate_shared_briefs.py --store local_data/shared_briefs.db --init-store
.venv\Scripts\python.exe scripts/generate_shared_briefs.py --store local_data/shared_briefs.db --source local_data/official_snapshot.db --season-year 2026 --matchweek 8 --content-version shared-v1 --prepare
.venv\Scripts\python.exe scripts/generate_shared_briefs.py --store local_data/shared_briefs.db --source local_data/official_snapshot.db --season-year 2026 --matchweek 8 --content-version shared-v1 --generate
```

The source must contain the authoritative current official schedule/results and
reference tables. Credentials must be deliberately provided in the generation
process environment; the CLI does not load `.env`. `--prepare` never calls OpenAI.
`--generate` returns nonzero (2) unless all twenty entries are successful; source/
configuration failures return 1. JSON status counts distinguish missing context,
failure, uncertainty and expiry rather than claiming a partial batch is complete.

Repeat with `--retry-failed` for known rejected/invalid output. A transport timeout
or expired ten-minute claim becomes `uncertain`; inspect it before explicitly
using `--retry-uncertain`, which can repeat a remotely completed but lost response.
Successful entries are skipped regardless of retry flags. Old claim tokens cannot
overwrite the result. Two concurrent initial preparations with different cutoffs
can cause one caller to fail closed; rerunning adopts the committed manifest.

Use a new content version when changing prompt/model, refreshing already prepared
facts, or changing the schedule. The version also freezes its cutoff. Previously
blocked context can become pending on retry if the refreshed source supports facts
under that same cutoff; successful contexts never silently refresh. A fixture that
has kicked off cannot be regenerated, even if other fixtures in the round have not.

For read-only presentation, configure **all consumers to the same**
`SHARED_BRIEF_STORE` path and `SHARED_BRIEF_CONTENT_VERSION`. This PR does not set
either in Railway or attach a scheduler. An unconfigured/missing store displays
availability text, with no generation side effect. Schedule mismatches hide stale
content and require a new pre-kickoff version. There is no per-player saved copy.

## Verification and material activation constraints

Full validation: **435 Python tests and 10 JavaScript tests passed**. New coverage
includes twenty perspectives without any picks/player tables; equivalent local-ID
and league copies; failure-only retry; two concurrent workers; uncertain claims
and stale-worker fencing; kickoff gates; schedule changes during I/O; isolated
storage; and read-only five-bullet full-page/HTMX refreshes. PR25's committed Week 2
and Week 5 contexts reproduce byte-for-byte after extracting shared functions.

Read-only preview of the real reference snapshot:

- Week 2: twenty canonical slots, eighteen supported contexts. Coventry–Hull has
  no supported football facts for either team under the strict replay cutoff.
  Both slots remain `blocked`; inventing prose to reach twenty is forbidden.
- Week 5: twenty canonical slots and twenty supported contexts.

This is context/orchestration validation, **not a live shared-writer prose test**.
No real OpenAI call was made in PR26. Fake providers verified all-twenty persistence,
reuse, concurrency and retries. Raw replay data/databases are not committed.

Before activation, the operator needs one shared transactional store accessible to
all participating leagues and enough authoritative pre-cutoff facts for all twenty
teams. Multi-host deployments with isolated files require central storage rather
than copying this database. Only the existing verified 2026/27 mapping is enabled.
These are explicit limitations, not reasons to generate per player or relax cutoffs.

The source snapshot remains SHA-256
`73c37c2cceff71f360dbf36f55ecadc1a9ad05143ed0cbf93c369794df35bef4`.
No deployment, Railway/n8n changes or production/game database writes were performed.
