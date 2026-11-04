# Private Beta Stage 2 discovery/design

Stage 2 should be implemented as an offline, manifest-driven backfill with strict refusal gates. Repository evidence supports the design, but it cannot establish all historical season, club, recipient, or in-flight-work mappings without an approved local copy and reviewed evidence.

**Observed** means verified in repository code, documentation, or test source. **Inferred** means a proposed conclusion or design based on that evidence. **Unresolved** means production-copy evidence or Steve’s decision is required. Explicit architecture decisions supplied by Steve after the discovery report are identified as **Approved decision**; they are design requirements, not claims about existing implementation.

Discovery baseline: branch `beta/stage2-backfill`, HEAD `3b18e8f23285539780571fbae13fd7868024766e`.

No application imports, tests, database operations, or external calls were performed during discovery. This document records the approved design and clarifications; it does not authorize implementation or production execution.

## 1. Existing physical schema relevant to Stage 2

**Observed:** Stage 1 declares 18 new tables, 24 nullable additions across nine legacy tables, and 17 explicit indexes. Added legacy columns have no defaults. Foundation tables are outside application ORM metadata.

Sources: [foundation_schema.py](../foundation_schema.py), [application models](../pickem_flask_htmx_tabs.py), [foundation review](PRIVATE_BETA_FOUNDATION_REVIEW.md).

**Unresolved:** These are source-defined schemas. Actual constraints, triggers, additional tables, and populated values must eventually be verified on an approved copy. The handoff’s 49 tables / 80 indexes records Steve’s supplied production post-state, not a measurement made here.

### Legacy game and identity tables — Observed

Unless stated otherwise, these tables have an integer `id` primary key.

- **`players`** — Existing player identities become Memberships. Global unique, non-null `name` remains. Stage 1 adds `league_id`, `user_id`, `name_key`, `role`, `status`, `joined_at`, `left_at`, and `recap_opt_in`. League/User references are FKs. Partial unique indexes enforce `(league_id,user_id)` and `(league_id,name_key)` when populated. Role, status, and opt-in values have checks.
- **`seasons`** — Existing League Seasons; globally unique `code`, plus active/archive flags and optional football-data competition/year metadata. Adds `league_id`, `football_season_id`, `rules_version`, `stake_minor_units`, and `currency`. Stake must be nonnegative when populated. Final per-league season uniqueness is not yet implemented.
- **`weeks`** — FK to `seasons`; unique `(season_id,number)`. Stores room code, status, and first finalization timestamp. Adds FK `football_matchweek_id`. Existing Week identity and number remain authoritative for historical relationships.
- **`fixtures`** — FK to `weeks`; unique `(week_id,match_number)` and unique non-null football-data `external_match_id`. Stores historical home/away names, venue, kickoff, and API status. Adds FKs `football_season_id`, `football_matchweek_id`, `home_club_id`, and `away_club_id`. Stage 1 does not enforce cross-row season/round/participant consistency.
- **`matchups`** — FK to Week and three Player references: participant A, participant B, first picker. No Stage 1 additions. Existing FKs alone do not prove first-picker membership, distinct opponents, or season participation.
- **`picks`** — FKs to Matchup, Player, Fixture; unique `(matchup_id,fixture_id)`. Preserves historical `team` text and creation time. Adds `selected_club_id`. Stage 1 does not enforce that its Club is a fixture participant.
- **`auto_draft_settings`** — FKs to Matchup and Player; unique `(matchup_id,player_id)`. Enabled/confirmation state remains unchanged. No additions.
- **`auto_draft_preferences`** — FKs to Matchup, Player, Fixture; unique `(matchup_id,player_id,fixture_id)`. Stores team text and priority. Adds `selected_club_id`. Preferences need the same mapping scrutiny as picks.
- **`results`** — Unique Fixture FK; official outcome, optional scores, source, update time. No additions. Outcome-only historical results are supported. Current model declarations do not enforce all proposed final score/outcome checks.
- **`api_sync_states`** — Unique legacy Season FK, provider, attempts/success/error timestamps, rate-limit information and counters. Adds `football_season_id`. Existing uniqueness is by legacy Season, not canonical season/provider.

### Legacy live/cache/task tables — Observed

Sources: [live_models.py](../live_models.py), [pick_insight_enrichment.py](../pick_insight_enrichment.py), [pick_insight_tasks.py](../pick_insight_tasks.py).

- **`fixture_provider_links`** — Integer PK; Fixture FK; unique `(provider,external_fixture_id)` and `(fixture_id,provider)`. No additions. Existing links are mapping evidence, not permission to remap a fixture.
- **`live_fixture_states`** — Fixture FK is the PK. Preserves provider, provisional state/scores, match clock, and synchronization timestamps. No additions.
- **`match_events`** — Integer PK; Fixture FK; unique `(fixture_id,provider,external_event_id)`. Stores provider team/player identifiers, side, activity state, ordering and revision JSON. Adds `club_id`.
- **`club_season_enrichment`** — Composite PK `(season_id,sportmonks_team_id)`; Season FK; unique `(season_id,footy_club)`. Adds `football_season_id` and `club_id`. Its existing `league_id` is a **Sportmonks competition identifier**, not Pick ’Em league ownership.
- **`pick_insight_refreshes`** — Week FK is PK; separate Season FK. Stores pending/running/completed state and claims. No additions. Cache publication and task completion are committed together; claims serialize refreshes within a season.

### Legacy Correspondent tables — Observed

All have integer `id` PKs and **no Stage 1 column additions**:

- **`weekly_recaps`** — Week FK; unique `(week_id,revision)` and unique automation key. Stores every revision, context, generated text, hashes, model metadata and lifecycle state.
- **`correspondent_sources`** — Week FK, optional Fixture and submitting Player FKs; unique `(week_id,provider,content_hash)`. Preserves content, metadata and submission notes.
- **`correspondent_source_classifications`** — Source FK; unique `(source_id,prompt_version,pass_number)`. Preserves classification history and provider response IDs.
- **`correspondent_classification_jobs`** — Season/Week FKs; unique OpenAI batch ID. Stores batch/file identifiers, counts and progress.
- **`correspondent_classification_job_items`** — Job/Source FKs; unique `(job_id,source_id)`, `custom_id`, and non-null reservation keys when present. These preserve resumable import and duplicate-submission protection.
- **`correspondent_x_collection_states`** — Unique Week FK; unique lease token. Owns collection window, pagination, counts, caps and completion state.
- **`correspondent_x_page_receipts`** — Collection FK; unique claim token. Records already-paid/checkpointed pages.
- **`correspondent_email_deliveries`** — Unique Recap FK; unique delivery marker and claim token. Stores recipient snapshot, Gmail acknowledgement and send state.
- **`correspondent_notifications`** — Week FK; unique `(week_id,event_type)`, delivery marker and claim token. Stores administrative notification payload and delivery state.
- **`recap_source_usages`** — Recap/Source FKs; unique `(recap_id,source_id)`.
- **`weekly_recap_selections`** — Unique Week FK and Recap FK; preserves selected revision and selection timestamp.

Their durable ownership remains derivable through existing Week IDs. Cross-week consistency still needs explicit validation.

### Shared reference/content/operations stores — Observed

Sources: [reference schema](../football_reference_schema.py), [brief storage](../shared_brief_store.py), [scheduler storage](../shared_brief_operations.py).

- **`football_reference_seasons`** — Integer PK; unique `(provider,external_season_id)`; provider competition, metadata and synchronization state.
- **`football_reference_fixtures`** — Integer PK; Reference Season FK; unique `(provider,external_fixture_id)`. Stores provider team IDs/names, kickoff, scores and state. **No round/matchweek field is persisted.**
- **`football_reference_events`** — Integer PK; Reference Fixture FK; unique `(provider,external_event_id)`. Preserves event facts, presence/activity/withdrawal information.
- **`football_reference_syncs`** — Integer PK; Reference Season FK; synchronization history and count JSON.
- **`football_reference_tasks`** — Composite PK `(provider,external_season_id)`; leases, freshness, cooldown and success/error state.
- **`brief_batches`** — Composite PK `(provider,competition,season_year,matchweek,content_version)`; frozen cutoff/model/prompt metadata.
- **`team_briefs`** — Composite PK `(provider,competition,season_year,external_fixture_id,team_id,content_version)`; frozen context, generated content, status, attempts and claims.
- **`scheduler`** — Separate operations store; singleton PK constrained to `id=1`; lease, heartbeat and report.

None receives Stage 2 columns. Separate-store boundaries and existing keys must remain intact.

### New Stage 1 tables — Observed

All declared FKs use `ON DELETE RESTRICT`.

- **`schema_migrations`** — Version-text PK; checksum, application time, before/after schema hashes. Existing verifier accepts exactly the Stage 1 row; the approved eventual extension is described in section 5.
- **`competitions`** — Integer PK; unique nonblank code; nonblank name.
- **`competition_provider_links`** — PK `(provider,external_competition_id)`; Competition FK; unique `(competition_id,provider)`.
- **`football_seasons`** — Integer PK; Competition FK; unique `(competition_id,season_key)` and partial unique `(competition_id,start_year)`. Date-order check; synchronization defaults disabled.
- **`football_season_provider_links`** — PK `(provider,external_season_id)`; Football Season FK; unique `(football_season_id,provider)`.
- **`football_matchweeks`** — Integer PK; Football Season FK; unique `(football_season_id,number)`; positive number.
- **`clubs`** — Integer PK; nonblank name; active boolean check. Names deliberately are not globally unique identities.
- **`club_provider_links`** — PK `(provider,external_club_id)`; Club FK; unique `(club_id,provider)`.
- **`football_season_clubs`** — Composite PK and FKs `(football_season_id,club_id)`.
- **`club_aliases`** — PK `(football_season_id,normalized_name)`; composite FK to `football_season_clubs`. An alias cannot identify two Clubs within one season.
- **`users`** — Integer PK; optional favorite Club FK; nonblank display name; unclaimed/active/disabled status; admin boolean. Normalized email has partial uniqueness; email fields must be jointly present/absent; verification requires email.
- **`user_identities`** — Integer PK; User FK; unique `(issuer,subject)`. No identity should be fabricated during backfill.
- **`leagues`** — Integer PK; unique lowercase trimmed slug; nonblank name; setup/active/archived status, timezone and Correspondent flag.
- **`league_invitations`** — Integer PK; League, optional Membership and accepted User FKs; unique token hash; acceptance timestamp/User pairing check. No invitations should be created.
- **`league_notification_recipients`** — PK `(league_id,kind,email_normalized)`; League and optional User FKs; kind restricted to recap/admin-alert and enabled boolean.
- **`league_season_memberships`** — PK `(season_id,membership_id)`; legacy Season/Player FKs; optional participation dates with ordering check.
- **`league_week_fixtures`** — PK `(week_id,fixture_id)`; legacy Week/Fixture FKs; unique `(week_id,display_order)`; positive display order.
- **`football_refresh_jobs`** — PK `(football_matchweek_id,job_kind)`; Matchweek FK; currently only `club_enrichment`; pending/running/completed/failed status and claim fields.

**Inferred:** Stage 2 must validate target relationships itself. Stage 1’s FK and uniqueness checks do not implement the final cross-row ownership, roster, slate or participant constraints.

## 2. Exact data sources available for each Stage 2 mapping

### Player → User

- **Observed:** `players.id` and `players.name` exist; no legacy account identity or verified email is stored on Player.
- **Inferred — deterministic:** One new unclaimed User per existing Player, ordered by Player ID, with an explicit persisted Player→User manifest. Copy the existing name as display text; never merge users by name.
- **Approved decision:** Do not infer emails, claimed identities, favorite clubs, platform-admin rights, commissioner role, recap opt-in, or recipient permission from names or mere historical presence. Leave optional unknown fields null unless a reviewed manifest or explicit product decision supplies them. Required fields must follow an already-approved policy or remain a blocker; schema defaults are not evidence of permission.

### Player → League #1

- **Observed:** The approved architecture assigns existing Players to Loyola, preserving their IDs.
- **Inferred — deterministic:** Set the new `league_id=1` only after verifying League #1’s full approved identity and all preexisting ownership values. Populate normalized Membership names with collision checks.
- **Approved decision:** League membership does not itself establish commissioner role, recap opt-in, recipient permission, or any claimed account identity. Optional unknown values remain null.

### League Season → Football Season

- **Observed:** Evidence includes `api_competition_code`, `api_season_year`, fixture `external_match_id`, existing provider links, enrichment `sportmonks_season_id`, and reference fixture→season relationships.
- **Observed:** The football-data importer uses competition/year to request fixtures but does not persist the provider’s season-object ID. A year must not be inserted as that ID.
- **Observed:** The legacy migration creates a generic archived “Year 1.” The CSV importer stores round, match number, names and venue but not the CSV date or provider IDs.
- **Inferred — reviewed manifest:** Reconcile all available evidence per Season. The checked-in [epl_2025.csv](../epl_2025.csv) can corroborate an exact schedule comparison; its filename alone proves nothing.
- **Unresolved:** Actual historical-season matches and evidence coverage.

### Week → Football Matchweek

- **Observed:** API import derives `weeks.number` from provider `matchday`; CSV import derives it from `Round Number`.
- **Inferred — deterministic after season validation:** Map `(verified football season, reviewed round number)` to a canonical Matchweek, preserving Week ID and number.
- **Unresolved:** Legacy/manual/test weeks or conflicting round evidence. Reference tables cannot supply missing round numbers because they do not store them.

### Fixture → canonical season/round/home/away Club

- **Observed:** Legacy Week/Season joins establish game provenance. `external_match_id` is football-data identity; existing `fixture_provider_links` can connect to Sportmonks reference fixtures.
- **Inferred — reviewed manifest:** Resolve season/round from reviewed provenance; resolve participant IDs through verified provider evidence and aliases. Keep every legacy field.
- **Unresolved:** Unlinked fixtures, participant disagreements, duplicate identities, or rescheduling that makes current provider round differ from historical slate.

### Historical team strings → Club

- **Observed:** [Sportmonks mapping](../data/sportmonks_2026_27_mapping.json) records 20 reviewed 2026/27 team mappings. Reference fixtures contain additional exact provider IDs/names.
- **Observed:** `fixture_history.resolve_club_ids` refuses name collisions; `live_sync.team_key` also contains normalization aliases.
- **Inferred — reviewed manifest:** Reuse these as evidence, not universal identity authority. Approve season-scoped aliases, normalization and provenance explicitly.
- **Unresolved:** Earlier-season aliases, collisions, and clubs outside the reviewed mapping.

### Pick → selected Club

- **Observed:** Draft validation accepts exact fixture home/away strings. Scoring also uses exact string comparisons.
- **Inferred — deterministic:** First require `pick.team` to equal exactly one historical fixture participant; then use that participant’s reviewed Club ID. Apply the same rule to auto-draft preferences.
- **Unresolved:** A nonmatching string, including `"Draw"`, is a blocker. An alias that appears semantically correct could change existing scoring semantics and must not silently rescue the row.

### League Week → fixture slate

- **Observed:** Existing selections use `fixtures.week_id` and ascending `match_number`.
- **Inferred — deterministic:** Preserve original Fixture IDs and ordering. Prefer `display_order=match_number` where positive and unique; retain global numbering rather than unnecessarily renumbering each week.

### Season participation → League Season Membership

- **Observed:** Matchup participants provide direct evidence. Picks and relevant source submissions provide additional edges. Current `season_players` falls back to all Players if no Matchups exist.
- **Inferred — deterministic with validation:** Build a provenance-bearing union of actual participation, validate picks/preferences/submissions against their Week and Matchup, and deduplicate by Season/Player.
- **Unresolved:** Empty/unstarted-season rosters. The existing global fallback is not authoritative roster evidence.
- **Approved decision:** Historical participation does not imply permission, opt-in, or privileged role.

### Sync/cache/task state → shared football identity

- **Observed:** Sync state points to legacy Season; enrichment includes Sportmonks Season/Club IDs; refresh tasks point to Week and Season.
- **Inferred — deterministic after identity validation:** Populate new sync/cache columns; produce a mapping from legacy refresh receipts to canonical Matchweeks; preserve completed work in corresponding new jobs.
- **Observed:** Reference tasks are already provider-season keyed. Shared briefs are already global.
- **Observed:** Brief keys combine `provider='football-data'` and football-data fixture IDs with **Sportmonks team IDs** from the reviewed mapping. They cannot be interpreted as a uniform provider namespace.
- **Unresolved:** Multiple old tasks mapping to one new job, conflicting statuses, and unresolved claims.

### Notification recipient → League #1

- **Observed:** Current configuration names are `CORRESPONDENT_RECAP_RECIPIENTS` and `CORRESPONDENT_ADMIN_ALERT_RECIPIENTS`. Delivery rows contain historical recipient snapshots.
- **Inferred — reviewed manifest:** Explicitly approve recipients by kind for League #1. Keep `user_id` null unless verified account association exists.
- **Approved decision:** Never infer recipient permission from a name, Player membership, or mere historical presence.
- **Unresolved:** Current approved recipients. Historical delivery snapshots prove past addressing, not current permission. No environment values were read.

## 3. Preservation requirements

**Observed:** The architecture requires new-table/new-column population only. Its preservation contract extends beyond row counts.

**Inferred:** The implementation should enforce:

- Exact legacy PK sets, FK edges and allocator state; no new legacy Player/Fixture/Week rows.
- Every original legacy column value, including nulls, names, fixture strings, venue, kickoff, match numbers, status and timestamps.
- Results exactly as stored: outcomes, optional scores, source and update time. No replacement from reference scores or live states.
- Pick ownership, Fixture/Matchup relationships, team text, timestamps, auto-draft priority and confirmation state.
- All Correspondent text/JSON, classifications, revisions, selected recaps, hashes, source usages, batch/file/custom IDs, reservations and response acknowledgements.
- Every X receipt, pagination token, counter, cap, lease and collection window.
- Delivery markers, recipient snapshots, Gmail IDs, claims and sent/error state.
- Reference identities/content and brief keys, frozen contexts, generated content, attempts and scheduler state.
- Existing indexes, triggers, views, and unexpected application tables discovered during future inspection.

**Approved decision:** Preserve the Stage 1 migration-ledger row exactly. The eventual Stage 2 completion receipt is a second row; it must not rewrite Stage 1 history.

**Observed:** Scoring gives +1 for the selected winning participant, −1 for a losing selection, and 0 for draws or missing outcomes. Finalized-week filtering, opponent totals, tie ranking and $5/net-point payouts are implemented in the [scoring functions](../pickem_flask_htmx_tabs.py).

**Inferred:** Compare weekly totals, for/against/net, pick records, standings/ranks, payouts, H2H, club records, draft order and available fixtures before/after against identical Results and a fixed clock. Preserve current global-player fallback behavior during Stage 2; changing loaders belongs to Stage 3.

Logical preservation means exact stored text/BLOB values and relational equality, not an unchanged whole database-file checksum after legitimate additions.

## 4. Known ambiguity and refusal cases

**Inferred:** Stage 2 should refuse application on any of the following:

1. League ID 1 conflicts with approved Loyola identity; approved slug belongs to another row.
2. Existing ownership/User bindings disagree with the manifest.
3. Foundation state is partially populated without a recognized exact plan.
4. Stage 1 checksum, schema fingerprint, ledger history, PK shape, integrity or FK validation fails.
5. Input data or evidence changes between inspection and application.
6. Blank Player names or collisions after Membership-name normalization.
7. Multiple Players unexpectedly map to one User in League #1.
8. Missing, mixed or contradictory season evidence; treating a year as a provider season ID.
9. Invalid/unproven round, including manually created or test weeks; fixture round conflicts with reviewed Week provenance.
10. One provider identity maps to multiple canonical objects, or one canonical object has incompatible IDs for the same provider.
11. Duplicate canonical fixtures or League Seasons that would require merging historical rows.
12. Ambiguous aliases, unsupported participant identities, same Club on both sides, or home/away reversal.
13. Pick/preference string matches neither or both fixture participants.
14. Pick/preference belongs to the wrong Matchup participant or references a fixture outside its Week slate.
15. Invalid slate ordering or duplicate order values.
16. A Matchup references identical opponents or an unrelated first picker.
17. Roster evidence conflicts, including cross-season submissions or a proposed empty-season roster based only on global fallback.
18. Event team ID/name/side evidence disagrees. A genuinely non-team event may retain null `club_id`; contradictory team evidence must not be treated as harmless absence.
19. Cache/provider season IDs disagree with reviewed game-season mapping.
20. Several legacy task receipts collapse to one canonical job with incompatible completion/state evidence.
21. Running, expired-but-unresolved, or uncertain external claims require reconciliation. Expiry alone does not prove no external work occurred.
22. Correspondent source/job/item/usage/selection relationships cross their expected Weeks or Seasons.
23. Recipient authorization is missing or requires guessing a User from a name.
24. A trigger would modify protected columns/tables, even indirectly.
25. Before/after preservation or scoring differs.
26. A rerun finds additional rows, changed mappings, changed manifests, or nonmatching populated values.

**Approved decision:** Optional unknown identity/permission fields remain null; do not invent values to satisfy the plan. If an unresolved required policy value prevents a valid insert, stop for the reviewed manifest or explicit product decision.

**Observed:** Stage 1’s verifier requires exactly one ledger row. Blindly adding a Stage 2 row would break existing verification.

**Approved decision:** Deliberately extend and test that verifier later for the approved Stage 1-only state and approved Stage 1+Stage 2 history. No verifier change is implemented by this document.

**Observed:** The older review allows an isolated unresolved legacy season key for analysis; the current handoff says unresolved mapping is a STOP condition.

**Inferred:** Discovery may report unresolved candidates, but the executable backfill should not commit placeholder football identities under the current scope.

## 5. Proposed deterministic backfill algorithm

The algorithm below is **Inferred**, subject to the explicitly **Approved decisions** stated here. It describes future implementation; no execution is authorized.

1. **Explicit target and evidence selection.** Require an existing approved local-copy path, reviewed manifest and expected fingerprints. No environment-selected target, app import, provider client, worker or implicit database creation.

2. **Read-only inspection transaction.** Validate Stage 1, inventory all schema objects/stores, inspect constraints and triggers, and produce protected-value/identity manifests. Include columns beyond the static legacy minimum; exclude only explicitly approved Stage 1 additions from the legacy-value comparison.

3. **Build a complete plan before writing.** Resolve every required mapping, collect provenance, classify optional nulls, and report blockers. No “best effort” partial population. Leave optional unknown identity/permission fields null unless a reviewed manifest or explicit product decision supplies them.

4. **Freeze the reviewed manifest.** Include source fingerprints, tool/manifest version, normalization rules, canonical identities, explicit ID allocations, Player→User mappings, roster/slate mappings, task disposition, recipients, and expected changes. Freeze creation timestamps for new rows; do not invent historical join dates.

5. **Prepare the approved execution target and checkpoint before the write transaction.** For eventual production execution, first explicitly fence/hold relevant writers, then create a unique checkpoint and independently verify it. Do not overwrite an existing checkpoint. Maintain coherent, separately verified reference/content/operations evidence as applicable. This operational preparation requires separate explicit authorization.

6. **Acquire one bounded game-database write transaction and revalidate.** Enable and verify FKs before `BEGIN IMMEDIATE`. Under that reservation, revalidate schema/data/manifest fingerprints, existing foundation population and the reviewed baseline. Any mismatch stops application. **Do not create the production checkpoint from inside the active migration transaction.**

7. **Apply in dependency order:**
   - Competitions and provider links.
   - Football Seasons/provider links and Matchweeks.
   - Clubs/provider links, season participation and reviewed aliases.
   - League #1 and unclaimed Users.
   - Player and League Season additions.
   - Week and Fixture canonical references.
   - Fixture slates and historical season rosters.
   - Pick/preference selected Clubs; supported event Club references.
   - Sync/cache canonical references and reviewed new refresh-job rows.
   - Explicit League #1 recipients.

8. **Restrict every write.** Allow inserts only to approved new tables and updates only to approved nullable additions. Fill nulls or verify exact matches; refuse contradictory non-null values. No `REPLACE`, destructive upsert, legacy deletion or hidden DDL.

9. **Validate before commit.** Check exact protected hashes/PK/FK sets, schema equality, domain invariants, expected mapping completeness, scoring parity and unchanged read-only-store fingerprints.

10. **Record completion atomically and commit once.** The preferred eventual design inserts the approved Stage 2 receipt in `schema_migrations` in the same transaction as the data population. Any failure rolls back the entire population and its receipt. Do not commit entity families independently.

11. **Verify rerun.** The same reviewed plan must produce no additional rows, changed IDs, renewed timestamps or reset claims. Verify mappings rather than accepting a completion marker alone.

### Approved decision — completion receipt

- The preferred eventual design is an atomic second row in `schema_migrations`, for example `private_beta_002_backfill`.
- Preserve the Stage 1 row exactly.
- Stage 2 is data-only: its `before_schema_hash` and `after_schema_hash` must be equal.
- The Stage 1 verifier must later be deliberately extended and tested to accept the approved Stage 1-only state and the approved Stage 1+Stage 2 history.
- Preserve Stage 1 checksum semantics and reject unknown, partial, conflicting or drifted histories.
- Do not implement this yet.

**Inferred:** A manifest must be durable before application. If the database commit succeeds but writing an external completion report fails, a retry should verify the atomic receipt and exact expected database post-state, then regenerate the report without allocating anything.

### Approved decision — eventual production checkpoint sequence

1. Explicitly fence/hold relevant writers.
2. Create and independently verify a unique checkpoint.
3. Acquire the bounded write transaction.
4. Revalidate schema/data/manifest fingerprints under that reservation.
5. Apply and validate.
6. Commit once.

Do not create the production checkpoint from inside the active migration transaction. This sequence records an intended design, not authorization to fence writers, checkpoint production, or execute Stage 2.

**Inferred — task boundary:** Preserve old task/claim rows. Draining, fencing or acknowledging live work is a separately scoped operation, not something the backfill silently performs. Do not enable a new job consumer during Stage 2.

## 6. Proposed implementation structure

All names below are **Inferred proposals**, not created files.

- **`foundation_backfill.py`** — Stdlib-only maintenance entry point with explicit inspect/plan/verify operations; add apply only after the planner and contract are reviewed.
- **`foundation_backfill_manifest.py`** — Typed/versioned manifest parsing, deterministic serialization, checksums, evidence references and normalization.
- **`foundation_backfill_validation.py`** — Read-only inventory, protected-value hashes, cross-row validation, refusal diagnostics and expected-post-state verification.
- **`tests/test_foundation_backfill.py`** — Synthetic database tests, failure injection, reruns and side-effect guards.
- **`tests/fixtures/foundation_stage2_*.json`** — Synthetic reviewed mapping examples; no real recipients, credentials or private database evidence.

Recommended major interfaces:

- `inspect_source`
- `validate_stage1_baseline`
- `load_reviewed_manifest`
- `resolve_seasons_and_rounds`
- `resolve_clubs_and_participants`
- `build_identity_plan`
- `build_rosters_and_slates`
- `reconcile_task_plan`
- `validate_preservation`
- `verify_expected_poststate`
- Later: `apply_plan`

**Inferred:** Keep the production maintenance path entirely stdlib-only (`sqlite3`, `json`, `hashlib`, `pathlib`, `unicodedata`, dataclasses). Reuse carefully reviewed pure Stage 1 helpers without invoking its migration.

SQLAlchemy can support future **synthetic test-only** comparisons against existing scoring functions. The maintenance CLI should not import the Flask module: its engine connection hook changes SQLite journal/synchronous settings, and runtime paths contain side effects.

Private mapping manifests, recipient approvals and evidence should live outside Git. Public diagnostic output should contain IDs/counts/reason codes and hashes, not room codes, tokens, addresses or private content.

**Approved decision:** The eventual verifier extension and atomic Stage 2 ledger receipt are deliberate maintenance-tool changes with explicit compatibility tests. They are not app-startup behavior and are not implemented in this documentation task.

## 7. Test plan

**Observed:** Existing test source provides useful foundations:

- [Stage 1 migration tests](../tests/test_foundation_migrations.py): all 26 legacy ORM tables, outcome-only Results, exact JSON/BLOB preservation, extra triggers/indexes, WAL backup, idempotency, lock contention and rollback.
- [Application tests](../tests/test_app.py): historical preservation, finalized-only scoring, ties, payouts, season isolation, API import and manual Result precedence.
- [Task tests](../tests/pick_insight_task_cases.py): cache/task atomicity, repeat completion, competing claims and expired-worker fencing.
- [Shared brief tests](../tests/test_shared_briefs.py): stable identities across local IDs, frozen content, uncertain outcomes and duplicate prevention.
- [Reference task tests](../tests/test_football_reference_tasks.py): rollback, leases and preservation after failed refresh.
- Historical reference tests distinguish missing evidence, ambiguous aliases and incomplete event histories.

**Observed:** Stage 1 documentation records 518 full-suite and 19 targeted tests passing at that stage. Those tests were not rerun during discovery.

**Inferred proposed coverage:**

1. **Synthetic happy path:** Multiple seasons, repeated Week numbers, shared Players, archived/current data, verified provider identities, drafts, preferences, outcome-only Results, completed tasks and Correspondent receipts.
2. **Determinism:** Input enumeration/order changes produce the same plan and IDs.
3. **Idempotency:** Exact rerun preserves complete logical state, timestamps and manifest.
4. **League conflict:** Wrong ID-1 identity, slug collision and conflicting preexisting ownership.
5. **User/name collision:** NFKC/casefold collisions and unexpected shared User bindings.
6. **Club ambiguity:** Alias collisions, provider-ID conflicts, reversed participants, same home/away Club.
7. **Season/round inconsistency:** Conflicting metadata, missing evidence, mixed fixtures and unsupported manual rounds.
8. **Pick refusal:** Unknown strings, `"Draw"`, wrong fixture/Matchup/player, invalid preference selection.
9. **Historical scoring:** Before/after actual legacy outputs and independent expected values for wins/losses/draws, missing scores, manual outcomes, ties, provisional/finalized weeks, payouts and archives.
10. **Roster/slate preservation:** Exact Fixture IDs/order; actual participants; empty-season refusal.
11. **Operational preservation:** Completed work stays completed; active/uncertain claims refuse without being reset; recipient snapshots and acknowledgements remain unchanged.
12. **Shared-content preservation:** Preserve mixed-provider brief keys, reference rows, frozen contexts and separate scheduler state.
13. **Hash coverage:** Extra columns/tables, BLOBs, JSON whitespace, nulls, timestamps, allocator state, indexes/views/triggers.
14. **Rollback:** Inject failures after each phase and at commit; verify no partial population or receipt.
15. **Race/stale-plan refusal:** Lock contention and evidence/data drift between plan and apply.
16. **Recovery:** Commit succeeds but report generation fails; exact-state and receipt verification avoids duplicate allocation.
17. **No side effects:** Trap network, subprocess/service starts, app imports, implicit targets, missing-file creation, startup migration and forbidden-table writes.
18. **Ledger compatibility:** Accept approved Stage 1-only and Stage 1+Stage 2 histories; preserve the Stage 1 row exactly; require equal Stage 2 before/after schema hashes; reject unknown, conflicting or incomplete histories; prove data population and the second receipt commit or roll back together.
19. **Checkpoint sequencing:** Prove writer-hold and independent checkpoint verification precede the write transaction; prohibit production checkpoint creation inside the active migration transaction; revalidate fingerprints under the reservation and refuse drift.
20. **Unknown identity/permission fields:** Preserve null optional unknown fields; never infer emails, claimed identities, favorite clubs, admin/commissioner rights, recap opt-in or recipient permission from names or historical participation; reject unresolved required policy values.

Items 18–20 explicitly cover Steve’s approved architecture clarifications. Tests remain proposed and require a later implementation scope.

## 8. Open questions before implementation

The following remain **Unresolved** until approved evidence or an explicit decision exists:

1. **Exact historical season/club mapping coverage:** Which approved coherent local snapshot and supporting fixture/provider evidence will be supplied? Does it resolve the archived Season, every historical round, and club identity coverage, particularly rows lacking provider IDs and kickoff?
2. **League #1 policy values not already decided:** Confirm Loyola slug, initial status and other required new-table policy values not already approved. Optional unknown identity/permission fields remain null unless a reviewed manifest or explicit product decision supplies them.
3. **Current League #1 recipient manifest:** Approve recap/admin-alert recipients and any independently verified User association. Historical presence or old recipient snapshots do not grant current recipient permission.
4. **Empty/unstarted-season roster exceptions:** Determine intended participation for any season that lacks sufficient historical edges.
5. **In-flight/uncertain work disposition at execution time:** Supply a reviewed disposition for claims or uncertain provider operations. Multiple legacy receipts converging on one canonical job also need explicit resolution if their states differ.

**Approved decision:** Completion receipt architecture is no longer an open choice for this design: the preferred eventual design is the atomic second ledger row and deliberate verifier extension described in section 5. Its implementation remains future work.

Canonical Results, preserved historical IDs, unclaimed Users, no League #2, and deferral of application scoping/hardening are already decided.

## 9. Recommended next engineering step

**Inferred:** After explicit implementation authorization, implement only the **stdlib read-only Stage 2 inspector and manifest validator**, with synthetic fixtures and refusal tests.

Its first deliverable should be a deterministic report of:

- Stage 1 readiness and protected baseline fingerprints.
- Mapping evidence and coverage.
- Proposed Player→User, season/round, Club, roster and slate mappings.
- Task/recipient evidence requirements.
- Exact blockers and proposed write counts.

Do not include an apply path in that first slice. This proves the evidence and refusal model before introducing backfill writes. Do not implement the ledger extension or production checkpoint workflow during documentation setup.

## Discovery-pass local verification

**Observed — recorded at the end of the original read-only discovery pass, before creating this document:**

- **Branch:** `beta/stage2-backfill`
- **HEAD:** `3b18e8f23285539780571fbae13fd7868024766e`

```text
On branch beta/stage2-backfill
Your branch is ahead of 'origin/main' by 1 commit.

nothing to commit, working tree clean
```

Staged and unstaged diffs were empty. No files changed during that discovery pass. No databases were opened or created, no tests or migrations ran, and no external services were accessed.

This follow-up task authorizes creation of this discovery document only. It does not authorize code implementation, changes to AGENTS.md or the Stage 2 handoff, commits, production/Railway/n8n access, database operations, or external-service access.
