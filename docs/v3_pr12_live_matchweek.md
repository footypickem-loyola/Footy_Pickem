# V3 PR 12 — Live Matchweek / Timeline

## Status

Implementation contract for PR #12.

Branch: `v3/pr12-live-matchweek`

Base: production `main` after PR #11, commit `6aa7616f7091bc0a5681a494bb63140739cebbb3`.

Do not merge, deploy, or touch production data as part of implementation.

---

## Product principle

> We are not building a live-score product. We are building a live Pick ’Em product that happens to require scores.

The value of Live Matchweek is not the commodity score itself. It is what the live Premier League state means for this player’s Pick ’Em matchup.

PR #12 should answer:

> What do the live Premier League results mean for my Pick ’Em matchup right now?

The implementation must also create a durable structured factual event layer that the AI Correspondent can reuse later.

---

## Provider decision

Sportmonks Football API v3 is the chosen upstream provider for live football data.

PR #12 should use Sportmonks for provisional/live match state and event ingestion.

The rest of Footy should not depend directly on Sportmonks response objects. Introduce a provider adapter / normalization boundary so future provider changes do not require rewriting Matchweek, scoring, or Correspondent code.

Do not remove the existing football-data.org integration in PR #12.

For this release:

- Sportmonks owns provisional/live football state.
- Existing football-data.org result sync remains the authoritative final-result/finalization path.
- Sportmonks live state must never overwrite a manual official result.
- No migration away from football-data.org is part of PR #12.

---

## PR #12 scope

### Initial live product

Support:

- match state: not started / live / halftime / finished where available
- current home/away score
- current match minute / stoppage minute where available
- goal events
- scorer
- event minute
- related/assist player when available
- own-goal / penalty distinction when available
- running score after the event when available

The UI should be designed so cards, substitutions and VAR can be surfaced later without changing the core data model.

### Future-ready event ingestion

If Sportmonks returns supported structured events for:

- yellow card
- red card
- second-yellow red
- substitution
- VAR

the normalization/storage layer may persist them now even if PR #12 only renders goal events prominently.

Do not build ESPN/FotMob-style commentary or minute-by-minute play-by-play.

---

## Architecture

Preferred flow:

```
Sportmonks API
    ↓
Sportmonks adapter
    ↓
normalized live fixture state + normalized match events
    ↓
Footy SQLite database
    ↓
live Pick ’Em consequence engine
    ↓
Matchweek Live UI
    ↓
stored factual context available to Correspondent
```

The browser must never call Sportmonks directly.

Frontend page views must never produce one upstream Sportmonks request per user.

Poll Sportmonks centrally and serve all players from Footy’s stored state.

---

## Provider adapter

Create a small isolated Sportmonks client/adapter module rather than embedding provider parsing inside Flask routes or templates.

Responsibilities should include:

- authentication from environment
- livescore/in-play retrieval
- requested includes needed by this release
- parsing provider fixture IDs
- score/state/minute parsing
- event parsing
- normalization into Footy-owned structures
- bounded timeout/error handling
- rate-limit metadata handling where useful
- no secret/token logging

Suggested normalized structures are illustrative, not mandatory:

```python
NormalizedLiveFixture(
    provider_fixture_id,
    state,
    is_live,
    home_score,
    away_score,
    minute,
    extra_minute,
    provider_updated_at,
)

NormalizedMatchEvent(
    provider_event_id,
    event_type,
    minute,
    extra_minute,
    team_provider_id,
    team_name,
    player_provider_id,
    player_name,
    related_player_provider_id,
    related_player_name,
    detail,
    running_score,
    sort_order,
)
```

Templates and Pick ’Em calculations should consume our normalized/database models, not provider JSON.

---

## Provider fixture identity

Do not reuse `fixtures.external_match_id` for Sportmonks because it currently represents football-data.org identity.

Add a provider-neutral mapping mechanism.

Preferred shape:

### FixtureProviderLink

- id
- fixture_id
- provider
- external_fixture_id
- optional provider league/team metadata if genuinely useful
- created_at / updated_at optional

Constraints:

- unique(provider, external_fixture_id)
- unique(fixture_id, provider)

If an equally clean provider-link model already fits the codebase better, Codex may choose it.

Sportmonks fixture matching/import must be deterministic and testable.

Do not silently attach a Sportmonks fixture to a Footy fixture on weak/fuzzy evidence.

A safe first mapping strategy may use:

- competition/league
- fixture date/time
- home team
- away team
- explicit normalization/aliases where needed

Ambiguous or unmatched fixtures should be logged/reported, not guessed.

---

## Live fixture state persistence

Add an additive persistent model for provisional live state.

Suggested logical model:

### LiveFixtureState

- fixture_id, unique
- provider
- provider_fixture_id or relation through provider link
- state/status
- is_live
- home_score
- away_score
- minute
- extra_minute
- period if useful
- provider_updated_at if supplied
- last_synced_at
- optional last_error/staleness metadata only if it clearly belongs here

This is provisional state.

It must not be treated as the official finalized `Result` row.

Official historical standings, payouts and finalized-week logic must continue using existing authoritative result behavior.

---

## Match event persistence

Create an additive persistent normalized event table.

Suggested logical model:

### MatchEvent

- id
- fixture_id
- provider
- external_event_id
- event_type
- minute
- extra_minute
- team provider ID/name as useful
- player provider ID/name
- related player provider ID/name
- detail/subtype
- running score if supplied
- sort_order
- is_active or equivalent reconciliation marker
- provider_updated_at if supplied
- created_at
- updated_at

Important requirements:

- event ingestion is idempotent
- the same provider event must not duplicate on every poll
- updated provider events should update the existing row
- event corrections/removals must be handled safely
- do not assume a goal can never be corrected or withdrawn
- preserve enough event history for later Correspondent factual context
- live consequence calculations should use current authoritative live score state, not infer the score solely by counting goal rows

A provider event ID is preferred for identity. If Sportmonks does not provide a stable event ID in a particular payload, use a documented deterministic fallback key rather than timestamps/random IDs.

---

## Central live-sync worker

Do not poll Sportmonks from browser requests.

Do not run the live polling loop independently in each Gunicorn worker.

Add a dedicated long-running worker entry point suitable for a separate Railway service, for example:

`python live_sync_worker.py`

The PR should add code/configuration required for the worker, but must not create/deploy the Railway service without explicit approval after review.

### Polling behavior

At our current scale, prefer a simple conservative centralized strategy.

Sportmonks provides livescore endpoints that support events and current score/state. Use the appropriate v3 livescore endpoint rather than repeatedly fetching every historical fixture.

Recommended starting behavior:

- when no relevant PL fixture is near/live: sleep longer / avoid aggressive polling
- during relevant match windows but nothing currently live: approximately 60 seconds
- while one or more relevant PL fixtures are live: approximately 15 seconds

Exact values can be configuration constants/environment variables, not magic numbers scattered through code.

The worker must:

- be restart-safe
- be idempotent
- continue after transient provider/network errors
- use bounded request timeouts
- avoid blind rapid retries
- expose useful logs without secrets
- not hold database locks across network calls
- commit provider responses atomically enough that users do not see internally inconsistent score/event writes
- handle multiple simultaneous Premier League fixtures in one central cycle

A single livescore request with appropriate includes is preferred when possible over one upstream request per match.

---

## Live Pick ’Em consequence engine

Create deterministic Python logic separate from templates.

The provider tells us what happened in the football match.

Footy calculates what that means for Pick ’Em.

### Provisional contribution per drafted fixture

For a fixture that has started and has current live/final provisional score:

- selected team currently leading: +1
- selected team currently trailing: -1
- current score level: 0

A not-yet-started fixture contributes 0 to the live snapshot.

When an official `Result` exists, existing official scoring remains authoritative for that fixture.

### Live matchup snapshot

For each H2H, calculate:

- each player’s current provisional For
- current provisional Against
- current provisional Net = For - Against
- current payout if scores held
- fixture-by-fixture current contribution
- number/status of fixtures live, finished and not started

Use explicit language such as:

- “If scores hold”
- “Live”
- “Provisional”

Never present a live calculation as an official final result.

When the week is officially finalized, existing Final presentation remains authoritative.

### Event impact

For timeline goal events, compute Pick ’Em-specific impact where useful.

Examples:

- the selected team moves from 0 to +1
- the selected team moves from +1 to 0
- the selected team moves from 0 to -1
- the H2H live net changes from +2 to +4

Do not hard-code narrative strings deep inside provider parsing.

The consequence engine should expose deterministic before/after or current impact data; presentation can phrase it.

---

## Matchweek lifecycle

V3 conceptual lifecycle remains:

> Draft → Pre-Match → Live → Final

PR #12 should introduce a true Live state when:

- the draft is complete, and
- at least one relevant Matchweek fixture is in play / has live state indicating the week is underway, and
- the week is not officially finalized.

After kickoff begins, Matchweek should become a live-following experience rather than retaining the static Pre-Match presentation.

Do not break archived/historical week navigation.

---

## Matchweek Live UI

Stay within the established V3 Premium Sports Desk design system.

The live screen should prioritize Pick ’Em consequence over generic football information.

### Primary H2H card

Show prominently:

- player names
- current provisional Pick ’Em score / For values
- provisional Net
- “If scores hold” payout
- clear LIVE / HT / provisional state
- concise fixture progress summary

Do not make the Premier League score grid more visually important than the H2H.

### Owned picks / fixture state

For each player’s five picks, show:

- selected team
- opponent
- current match score
- live/final/not-started status
- current contribution (+1 / 0 / -1)
- minute when live

The user should quickly understand which picks are helping or hurting.

### Timeline

Add a chronological Matchweek timeline.

For PR #12, goals are the primary rendered event.

A goal item should support:

- minute
- scorer
- fixture / teams
- current/running score
- which drafted pick owns the fixture/team
- resulting Pick ’Em consequence where meaningful

The timeline should feel like a Pick ’Em feed, not a generic scores feed.

Cards/substitutions/VAR may remain hidden or subdued in PR #12 even if stored.

### Other matchups

Preserve visibility into the other two league H2Hs, but at lower hierarchy than the logged-in player’s matchup.

A compact live snapshot is sufficient.

---

## UI refresh

The live Matchweek UI may poll Footy’s own endpoint/partial using HTMX.

Suggested initial cadence: approximately 15 seconds while Live state is active.

This frontend polling reads our database only.

It must not call Sportmonks.

Requirements:

- GET-only refresh
- no duplicate event side effects
- no pick/result writes
- preserve scroll/focus reasonably
- stop/reduce polling outside Live state
- display stale-data indication if provider sync has not updated within a reasonable threshold

Do not add WebSockets/SSE in PR #12 unless absolutely necessary. They are not necessary for current scale.

---

## Correspondent integration boundary

PR #12 does not need to rewrite Correspondent generation.

It must, however, make the normalized stored match events easy for Correspondent code to consume later.

Add a deterministic helper/query layer that can provide factual Matchweek event context such as:

- goals
- scorer
- minute
- assist when available
- score progression
- late winner/equalizer derivable from stored sequence
- fixture final/live score

Do not have the LLM query Sportmonks directly.

Do not send raw Sportmonks payloads into the LLM.

The desired future information model is:

- Sportmonks / stored event data = factual match layer
- X sources = commentary, analysis, reaction, narrative
- Footy DB = proprietary Pick ’Em context
- Correspondent = editorial combination

---

## Relationship to What to Look For

PR #12 should not implement the full pre-match What to Look For product.

But its provider abstraction and permanent data model must not block later use of Sportmonks for:

- H2H
- historical fixtures
- form
- player-v-opponent history
- team/player statistics
- injuries/suspensions
- lineups

Do not prematurely build those analytics in this PR.

Do not couple the live-event schema to only 2026/27.

---

## Existing football-data.org behavior

Preserve:

- existing fixture schedule
- current football-data.org polling/service
- existing final Result import
- manual Result authority
- week finalization behavior
- Correspondent automation
- Auto-Draft/Bulk Picks
- standings/history/statistics/admin behavior

The new live layer is additive.

If both providers disagree during a live match, Sportmonks may drive the provisional display.

Once an official existing `Result` is present, official result/scoring behavior wins.

---

## Configuration

Expected new server-side configuration may include:

- `SPORTMONKS_API_TOKEN`
- `SPORTMONKS_LEAGUE_ID` or similarly explicit PL competition configuration
- live poll interval(s)
- optional live sync enable flag

Secrets must never be rendered, committed, logged, or returned to browser code.

Local/tests must run without a real Sportmonks token.

All provider calls in tests must be mocked/faked.

---

## Migration safety

All schema changes must be:

- additive
- idempotent
- safe against the existing production SQLite database
- covered by migration tests
- non-destructive
- backed up before ALTER TABLE operations where the existing migration convention calls for it

Do not rebuild core production tables merely to add live functionality.

Existing production picks/results/recaps/Auto-Draft data must remain untouched.

---

## Error/degraded behavior

Live football data is enhancement, not permission to make Matchweek unusable.

If Sportmonks is unavailable:

- keep the site usable
- show the most recently stored live state with a stale indicator when appropriate
- do not fabricate live data
- do not reset provisional scores to 0 solely because one poll failed
- official final results remain unaffected
- log provider failures cleanly

If fixture mapping is missing:

- report/flag it
- do not guess
- do not create incorrect timeline data

---

## Tests

At minimum add focused coverage for:

### Provider adapter

- livescore response parsing
- score/status/minute normalization
- goal event normalization
- cards/subs/VAR normalization if persisted
- missing optional fields
- provider error/rate-limit behavior
- no secret leakage

### Fixture mapping

- exact valid mapping
- alias/normalization cases if supported
- ambiguous mapping rejected
- wrong teams/date rejected
- repeated sync idempotent

### Persistence

- additive migration from current PR #11 schema
- repeated migration
- live state upsert
- event upsert/idempotency
- event correction/update
- event removal/inactivation if applicable
- other seasons/history preserved

### Pick ’Em live consequence engine

- selected team leading = +1
- trailing = -1
- draw = 0
- not started = 0
- official Result overrides provisional state
- correct For / Against / Net sign
- payout if scores hold
- multiple simultaneous fixtures
- score changes reverse/update contributions correctly
- no mutation of official standings/final results

### Lifecycle/UI

- Pre-Match → Live
- Live → official Final
- live primary matchup
- other matchup compact state
- timeline ordering
- goal impact rendering
- stale state
- archived/history read-only
- HTMX polling GET is side-effect free

### Worker

- no live fixtures
- one/multiple live fixtures
- transient provider failure
- restart/idempotency
- no DB lock during network request
- appropriate polling/backoff behavior

Run:

- full Python suite
- all JavaScript tests
- focused PR #12 tests
- `git diff --check`

---

## Browser verification

Use only a synthetic temporary/local database.

Test at minimum:

- desktop 1440px
- mobile 390px
- mobile 360px

Create deterministic fake live states that demonstrate:

1. Pre-Match before kickoff
2. one live fixture at 0–0
3. goal changes one pick to +1 and H2H net
4. equalizer moves it back to 0
5. opponent-selected team leads and hurts that player
6. simultaneous live fixtures
7. halftime
8. late goal
9. provider data stale
10. official Final transition

Verify:

- no horizontal overflow
- timeline readable
- Pick ’Em impact visually outranks generic score
- mobile hierarchy remains clear
- refresh does not replay or duplicate events
- direct URL / HTMX / refresh / back-forward behavior

---

## Explicit non-goals

Do not include in PR #12:

- full What to Look For
- H2H analytics UI
- player-v-opponent historical analysis
- Match Facts add-on
- xG
- betting odds
- predictions
- news API
- expected lineups
- generic minute-by-minute commentary
- push notifications
- WebSockets/SSE unless implementation proves HTMX polling inadequate
- replacing football-data.org final-result sync
- PostgreSQL migration
- frontend framework rewrite
- Correspondent prompt rewrite
- automatic production Railway service creation

---

## Acceptance criteria

PR #12 is ready for review when:

1. Sportmonks live data is isolated behind a provider adapter.
2. Sportmonks fixture identity does not overwrite football-data.org IDs.
3. Live fixture state is persisted separately from official Results.
4. Match events are persisted idempotently and correction-safe.
5. A centralized worker can poll live PL state without browser-driven upstream calls.
6. Live Pick ’Em scoring is deterministic and correctly calculates provisional For/Against/Net/payout.
7. Matchweek has a true Live presentation centered on Pick ’Em consequences.
8. Goal timeline includes scorer/minute and Pick ’Em impact.
9. Existing official scoring/finalization remains authoritative.
10. Stored event data is queryable for future Correspondent use.
11. Schema migration is additive/idempotent and preserves existing data.
12. Full/focused/JS tests pass.
13. Desktop/mobile browser verification passes.
14. No production data is touched.
15. Nothing is merged or deployed without explicit approval.

---

## Implementation philosophy

Keep the provider layer factual and boring.

Keep the Pick ’Em consequence engine deterministic.

Keep the UI centered on what the live football means to the user.

Store useful factual history once so the Correspondent and future What to Look For features can reuse it rather than reconstructing the match from social media later.
