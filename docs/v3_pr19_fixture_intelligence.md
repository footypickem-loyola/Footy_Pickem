# Fixture Intelligence Engine v1

`fixture_intelligence.build_fixture_intelligence` is a pure read-only foundation
for future Training Ground Pre-Match Briefs. It has no Flask routes, database
writes, network calls, AI generation, ingestion, backfill, or production UI.
It does not import or change `v3_pick_insight.py`.

## Input contract and time boundary

Pass `fixture`, the complete current-season `fixtures` schedule, official `results`,
`weeks`, `season_id`, and an explicit `as_of` datetime. Records can be ORM objects
or attribute-bearing snapshots. Weeks establish season membership. Supply the
Premier League ledger only; Footy's schema does not store competition IDs.
Do not pass live provider snapshots as official results.

The cutoff is `min(as_of, fixture.kickoff_utc)`. Both a historical kickoff and
the official result's `updated_at` must be strictly earlier. Equality is excluded.
Naive datetimes mean UTC. Target kickoff is required; there is no matchweek
fallback. Ordering is kickoff then fixture ID, never insertion or matchweek order.
The target result is always excluded, even for a retrospective debug request.

Missing or invalid results create gaps in the fixture sequence. Runs stop at
gaps, rolling windows containing gaps are withheld, and season-start/league-wide
claims require complete relevant evidence. An undated fixture conservatively
withholds chronological claims for both affected teams, even if it might be a
future fixture. This can reduce coverage on partially imported schedules.

Outcome-only manual results can support W/D/L facts. Goal claims require two
nonnegative integer scores consistent with the official outcome. Unknown scores
are never zeroes. Current season runs that reach the available history boundary
are lower bounds, never assertions about the previous season.

`updated_at` is the best availability evidence in the existing schema. There is
no result revision archive: a correction after the cutoff causes exclusion, not
reconstruction of its previous value. Reproduce a packet using the same schedule,
official result snapshot, explicit cutoff, and engine version. The evidence hash
covers eligible league ledger rows and gaps. Schedule completeness is a caller
contract; v1 cannot independently prove a missing imported fixture ever existed.

## Candidates and ranking

Each candidate includes the target fixture, signal type and family, subject,
claim, numerical evidence, ordered supporting fixture IDs, sample dates, venue
scope, strength, rarity basis, relevance, confidence, provenance, recomputation
metadata, editorial score and its components. Compound candidates link component
IDs. Raw candidates are always retained, including weak and redundant facts.

Detectors cover the eight outcome/goal streak and drought types; last-five W/D/L,
wins, clean sheets, scoring and scoreless frequencies; goals for/against and
averages; home and away sequences; home-away points-per-game splits; recent
overall-versus-upcoming-venue contrasts; overall winless plus venue unbeaten
`FORM_CONTRAST`; early-season W/D/L records (3–10 games); unbeaten/winning and
other season-opening runs; and conditional run extensions.

League defensive extremes use season goals-conceded totals, with explicit
played counts and all comparison evidence. They require 20 known teams, at least
five scored results per team, no gaps or unknown dates, and at most one game
difference in played counts. All-equal totals and ties involving more than three
teams are withheld. These are current-season totals, not historical records.

Editorial scoring is versioned policy, not a statistical probability:

- Strength: detector heuristic, capped at 80 (e.g. frequency `10 + 13 * count`).
- Relevance: 10 for overall/splits/upcoming venue, 3 for the other venue.
- Sample confidence: 10 for at least five supporting matches, otherwise 5.
- Selection threshold: 60. A 2-of-5 frequency tops out at 56 and remains raw.
- Ties: component count, overall scope, then stable candidate ID.

Suppression removes equivalent evidence windows and implied run facts, duplicate
W/D/L and win-frequency windows, and components already represented by a stronger
compound. Continuations for the other venue cannot be selected. Each candidate
gets a selection decision, score, reason, and suppression winner where relevant.
There is no hard top-N cap; future editors can choose their own packet size.
Rarity is explicitly heuristic with no fabricated historical percentile.

## Developer inspection

Use a local SQLite snapshot, not a production connection:

```powershell
.\.venv\Scripts\python.exe scripts/review_fixture_intelligence.py --db local_snapshot.db --fixture-id 123 --as-of 2026-10-03T18:00:00+00:00
```

The command opens SQLite with URI `mode=ro` and `query_only`, uses one read
transaction, and prints JSON containing `candidates`, `ranked_candidates`,
`ranking_decisions`, diagnostics and limitations. It never imports the Flask app
or initializes/migrates a database. No production route or UI exposes it.

## Regression checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_fixture_intelligence.py -v
```

Synthetic scenarios model the requested editorial examples; they are not claims
about real clubs. No deep historical, H2H, promoted-team or player comparisons are
supported. The raw/ranked distinction preserves weak facts for inspection while
preventing them from displacing stronger editorial candidates.
