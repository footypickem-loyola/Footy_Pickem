# PR21 — Historical fixture intelligence

This extends PR19's pure packet/candidate/ranking pipeline with SELECT-only
retrieval from PR20's Sportmonks reference tables. No official game data,
reference data, schema, importer, startup hook, UI, or deployment is changed.

## Retrieval and identity

`fixture_history.load_fixture_history(connection, fixture=..., as_of=...)` runs
inside the caller's read transaction. The developer inspection command opens
SQLite with `mode=ro` and `query_only`, without importing Flask. Missing reference
tables or unmatched clubs produce an empty history block; current-season PR19
logic remains available.

Club resolution uses the existing verified `data/sportmonks_2026_27_mapping.json`
for explicit Footy/Sportmonks aliases (e.g. `Man United` → provider team 14).
An exact reference-team name can also resolve a historical club not in the current
mapping. Colliding aliases or unrecognized spellings fail closed. There is no
case-folding, fuzzy match, inferred transfer or name-based fixture join. All
fixture joins use the resulting provider team IDs and PL league 8.

The history block is independent of ranking and contains:

- `exact_prior_season_fixture`: nearest earlier stored PL season with the same
  home/away orientation and a completed scored result.
- `most_recent_meeting`: newest completed scored meeting in either orientation.
- `recent_h2h`: up to five newest completed meetings, newest first.
- `venue_h2h`: up to five meetings with the exact target home/away orientation.
- Available sample counts, identity/cutoff/status diagnostics and evidence gaps.

Every returned match includes internal reference and external Sportmonks fixture
IDs, season identity/name, UTC kickoff, score, club IDs, snapshot timestamp and
active, present goal/red-card events with internal/external event IDs. Unfinished,
unscored or unsynchronized historical meetings are recorded as gaps. Runs and
player aggregates stop at the latest gap instead of silently skipping it; an
undated gap withholds these sequence-based signals conservatively.

Season ordering parses consecutive `YYYY/YYYY` or `YYYY/YY` names. Unknown names
still support dated H2H but cannot establish prior-season claims. Target season
defaults to the Premier League July-to-June convention, or can be supplied via
`--target-season-start`. A gap in stored seasons is never described as “last
season”: the candidate names the actual season and has an
`immediately_previous_season` flag. PR21 does not require 2026/27 reference data.

## Time semantics

Only reference fixtures strictly before `min(as_of, target kickoff)` are eligible.
Equal and later kickoffs are excluded before event retrieval. Event evidence can
only come from those eligible parent fixtures. Backfill/sync timestamps are
provenance, not match dates; applying them as match cutoffs would make newly
backfilled old seasons unusable.

PR20 stores the latest corrected snapshot, not a revision archive. Retrospective
packets therefore mean **facts from matches before the cutoff in this supplied
snapshot**, not proof of what a writer could have known at that historical instant.
The packet states this limitation. Reproduction requires the same snapshot,
alias map, target-season convention and engine version. Each historical candidate
includes a history-snapshot hash and explicit cutoff. Current official-result
availability filtering from PR19 is unchanged.

## Detectors and fact limits

`historical_fixture_intelligence.py` reuses `fixture_intelligence.Candidate`:

- `detect_exact_prior_season_fixture`: ordinary prior score is a strong anchor.
- `detect_h2h_streak`: winning/unbeaten/losing/winless runs of at least three.
- `detect_venue_h2h`: equivalent target-home runs of at least two.
- `detect_hat_trick_or_brace`: 3+ normal/scored-penalty goals is a hat-trick;
  exactly two is a brace. Own goals never count toward normal player scoring.
  The goal-event count must reconcile to the final total and normal scorers
  must have IDs/names; incomplete evidence cannot establish an exact brace.
- `detect_late_decisive_goal`: threshold is base minute **85**, versioned in
  `historical-fixture-intelligence-v1`. All goal running scores must form an
  unambiguous ordered sequence from 0–0 to the final score. A late draw→lead or
  deficit→draw must persist through the rest of the match. Insurance goals,
  temporary leads, partial/malformed timelines and ambiguous goal order do not
  qualify. The full supporting goal sequence is retained as evidence.
- `detect_player_vs_opponent`: normal-goal totals across the most recent five
  stored meetings, with meeting count and explicit null appearance count.
  Exact aggregate totals are withheld if any included match has incomplete
  scorer evidence; missing goal events cannot silently reduce the tally.
  Historical club attribution is retained; current roster membership is not
  asserted. Events cannot prove non-scoring appearances.
- Recent red-card context: present, active direct/second-yellow events in the
  latest meeting; no invented causal claim about their effect on the result.

A run that reaches the available sequence/window boundary is explicitly a lower
bound. No detector extrapolates to older unseen seasons or claims a career,
all-time, promoted-team, or deep historical record.

## Ranking and suppression

Historical candidates enter the existing `rank_candidates` function alongside
current-season candidates. Raw candidates, ranked candidates and decisions remain
inspectable. Historical scores add transparent components:

- Detector strength (e.g. 66 for a hat-trick, 52 for an exact prior fixture).
- Exact-fixture relevance: +8; opponent specificity: +8; venue specificity: +4.
- Recency: +8 within 365 days, +4 within 730 days, otherwise 0.
- Rarity: +18 for a hat-trick, +8 for a late decisive goal, +4 for a brace.
- Historical anchor significance: +5 for the exact prior fixture.

These are editorial weights, not probabilities or historical rarity percentiles.
The existing threshold remains 60. Historical scores may exceed 100. An ordinary
isolated player goal remains low-scoring, while a recent exact-fixture brace can
qualify. Roster uncertainty is always exposed.

Exact anchors covered by a higher-ranked hat-trick/brace/late goal, mirrored H2H
runs, implied unbeaten/winless runs and repeated player-goal evidence are
suppressed with explicit reasons/winner IDs. Reference and official fixture ID
namespaces are kept separate during suppression. Suppressing an anchor never
removes its score from the history block. No separate ordinary “latest meeting”
candidate is needed because that context is always present in history.
On otherwise equal historical run scores, winning/losing runs precede their
less-specific unbeaten/winless counterparts. This prevents a mirrored losing
run from leaving behind a redundant opponent unbeaten candidate.

## Local inspection and tests

```powershell
.\.venv\Scripts\python.exe scripts/review_fixture_intelligence.py --db local_snapshot.db --fixture-id 123 --as-of 2026-10-04T12:00:00+00:00 --target-season-start 2026
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_historical_fixture_intelligence.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Inspect `history`, candidates with `family=HISTORY`, their ranked counterparts,
and `ranking_decisions`. Synthetic tests cover orientation, chronological limits,
lower bounds, evidence gaps, scorer counts, own goals, late winners/equalizers,
non-decisive late goals, deduplication, missing reference data and read-only
behavior. Normal automated tests never contact Sportmonks.

## Verified real-data examples

`docs/pr21_real_examples.json` contains five structured examples generated by
the inspection path from the user's verified local reference snapshot with cutoff
`2026-10-05T03:00:00+00:00`. It includes target fixture IDs, reference and provider
IDs, seasons, kickoffs, scores, goal-event identities and ranking scores. The
examples cover Arsenal–Leeds, Chelsea–Bournemouth, Manchester United–Tottenham,
Liverpool–Manchester City, and Arsenal–Tottenham. They are deterministic facts,
not AI-generated match previews.

`tests/fixtures/fixture-history-real-sample.json` is a football-only extract of
18 reference fixtures and 55 goal/red-card events supporting those five cases.
It contains no picks, rooms, users, secrets, or game-state tables. The source
snapshot SHA-256 is recorded in both JSON files. Five reference-backed regression
tests exercise this extract without needing the private snapshot or any network.
The source database is read-only, excluded from commits, and checked unchanged
by SHA-256 after inspection. The PR description repeats the five concrete examples.
