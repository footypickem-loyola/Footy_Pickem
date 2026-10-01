# PR12 implementation and verification

The worker's original shared-database design and configuration below are superseded by the [PR13 stateless HTTP bridge](v3_pr13_implementation.md). The remaining PR12 behavior is preserved.

Work is on `v3/pr12-live-matchweek`, fetched from `origin`. Implementation was initially left uncommitted for architecture review; committing and pushing this branch was subsequently authorized. Merging, deployment, and provisioning remain unauthorized and were not performed. Existing local database files were not opened for implementation or verification. The three pre-existing untracked files were preserved.

## Architecture for review

- `sportmonks_live.py` is the only Sportmonks boundary. It uses the all-livescores endpoint (including the short pre/post-match window), with participants, scores, state, periods, and events. Authentication is an environment-supplied Authorization header. Requests time out after ten seconds; errors omit URLs, payloads, and credentials. Incomplete pagination fails closed.
- `live_models.py` registers three new tables on the existing metadata: `fixture_provider_links`, `live_fixture_states`, and `match_events`. Core tables and football-data.org IDs are unchanged. Existing schema initialization creates the additive tables idempotently; no new ALTER TABLE or table rebuild is required.
- `live_sync.py` matches only explicit PL fixtures using exact UTC kickoff, home/away names, and a small documented alias set. Ambiguity, mismatches, and conflicting links are rejected and logged. Established links support schedule changes but validate team identity. Archived/finalized weeks are not ingested.
- Event IDs are scoped to fixture/provider. The no-ID fallback hashes type, participant, player, minute, extra minute, and sort order. A correction to fallback identity creates a replacement and inactivates the prior event. Full event snapshots reconcile withdrawals; omitted includes preserve existing events. Normalized revisions retain prior versions. Scores are never reconstructed by counting events.
- `live_sync_worker.py` is a separate, disabled-by-default process. It closes its read session before network I/O and commits the whole response in one transaction. It polls at 15 seconds while relevant matches are live, 60 seconds near kickoff, and 300 seconds outside a match window. Failures retain stored data and back off. Run exactly one worker against the same SQLite database as the web process; there is no distributed worker election in this PR.
- `live_matchweek.py` provides the read-only consequence overlay and `factual_context(db, models, week_id)` for future Correspondent use. Official Results override provisional scores; existing finalization, standings, payouts, and historical views remain authoritative. Partial official results alone still use the existing Pre-Match behavior.

The provider documentation used to check endpoint/state/event semantics is [all livescores](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/livescores/get-all-livescores), [fixture states](https://docs.sportmonks.com/v3/definitions/states), [event types](https://docs.sportmonks.com/v3/definitions/types/events), and [period timing](https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/includes/periods). No authenticated provider request was made.

## Presentation

The Live page follows the supplied mock's primary H2H, paired owned-pick columns, light stadium backdrop, blue notice, and right-hand matchup summary. It omits Key Swing Fixtures and, per the revised product request, the player-facing Matchweek timeline. Fixture cards show current score/status/minute, team-side scorers and goal minutes, own-goal/penalty and card details, selected team, and current contribution. The primary H2H explicitly shows provisional For/Against/Net and the “If Scores Hold” payout. The remaining-fixture heading uses “10 Fixtures Remaining” (with a dynamic count).

The existing local stadium/league assets and navigation are reused; club crests from the mock are not added. Thus this is a close structural adaptation, not a pixel-identical reproduction. On mobile, primary score and projected payout lead, then picks and secondary summaries.

The timeline removal is presentation-only. MatchEvent persistence, provider event ingestion, reconciliation/corrections, scorer/minute and score progression, deterministic impact calculations, and the factual query/helper layer remain intact for future Correspondent use.

GET-only HTMX refresh reads the database: 60 seconds in Pre-Match, 15 seconds in Live, none in Final or archived views. Stable refresh/history IDs support focus/history preservation. A 90-second freshness threshold flags delayed live snapshots; missing scores are explicitly labelled. VAR goal-disallowed records suppress uncertain timeline impact rather than guessing which reported goal to remove. Event details may remain marked for revision until the provider supplies a corrected full snapshot.

## Verification

- Full Python suite: **201 tests passed**.
- Focused PR12 suite: **16 tests passed**, including additive/repeated migrations with existing data, mapping, normalization, safe errors, scoring reversal, manual-result authority, event corrections/withdrawals, fallback identity, read-only rendering, archived data, timeline ordering, and worker behavior.
- JavaScript suite: **10 tests passed**.
- Worker coverage includes idle/near/live cadence, simultaneous fixtures, transient failure, restart/idempotency, rate-limit backoff, atomic rollback, and a second SQLite writer successfully acquiring a lock during mocked network I/O.
- Browser: headless Edge, **1440px, 390px, and 360px**, all ten required scenarios, **30 viewport/scenario checks** with no horizontal overflow. Desktop/mobile screenshots were visually inspected. GET refresh, reload, back navigation, scroll position, open draft-history preservation, automatic kickoff detection, and automatic official Final transition passed.
- `git diff --check` passed.

After the current-state dashboard revision, browser assertions also verify that no timeline is rendered, scorer/card details remain within owned fixtures through refresh/history navigation, and the H2H retains explicit For/Against/Net with the requested capitalization. The focused PR12 and JavaScript suites passed again. A filtered Matchweek-only Python run encountered a Windows temporary-file cleanup error in the existing `test_app` class teardown (its test assertions passed); the full-suite rerun is used for the overall Python result above.

Every database used above was newly created in a temporary directory. Provider tests use synthetic payloads and fake openers. Existing test warnings for deprecated UTC helpers are unrelated to live-provider failures.

## Reproduce locally

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py'
.venv/Scripts/python.exe -m unittest discover -s tests -p test_live_matchweek.py -v
node --test tests/bulk_picks.test.cjs tests/pick_confirm.test.cjs
git diff --check
```

For browser review:

```powershell
.venv/Scripts/python.exe scripts/review_live_matchweek.py --port 58192
# In another terminal with Playwright resolvable (installed package or NODE_PATH):
node scripts/verify_live_browser.cjs
```

The harness always creates a new temporary database and clears Sportmonks authentication. Open `http://127.0.0.1:58192`, join as Steve with `PR12LOCAL`. Its loopback-only `POST /__review/scenario/1` through `/10` selects deterministic scenarios; these routes are not registered by the application itself. Screenshot and JSON evidence is written under `%TEMP%/footy-pr12-browser` (override with `PR12_BROWSER_OUTPUT`). Browser verification needs access to the application's existing HTMX CDN dependency.

## Future worker configuration (not applied)

Entry point: `python live_sync_worker.py`.

- `LIVE_SYNC_ENABLED=1` explicitly enables the process.
- `DB_PATH` must explicitly select the shared database.
- `SPORTMONKS_API_TOKEN` is server-only; never supply it to browser code.
- `SPORTMONKS_LEAGUE_ID` defaults to PL ID `8`.
- `LIVE_POLL_SECONDS`, `LIVE_NEAR_SECONDS`, `LIVE_IDLE_SECONDS` default to `15`, `60`, `300`, with a 15-second minimum.

Operational limitations for architecture review: exact kickoff matching intentionally rejects unexplained schedule discrepancies; operator-approved provider links/aliases may be needed. The livescore endpoint is not a historical backfill, and a long worker outage can leave gaps in event history. No real entitlement/token/API integration was exercised. Timeline ordering across fixtures uses kickoff plus reported match minute, so it is approximate around halftime/stoppages; no wall-clock event timestamps are invented. Provider events and official results can temporarily disagree, but official scoring wins.
