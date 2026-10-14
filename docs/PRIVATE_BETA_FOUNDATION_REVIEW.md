# Footy Pick ’Em — Private Beta Foundation

**Architecture approved with Chief-of-Staff corrections · 14 October 2026 (Eastern)**

**Recommendation:** retain the existing game records and IDs; evolve `players` into Memberships, `seasons` into League Seasons, and `weeks` into League Weeks. Add shared football identities and explicit league/account ownership around them. League #2 references the same fixture/result/live records as League #1.

**Status:** Stage 1 additive PR preparation authorized. Schema expansion and safety tests are prepared on an isolated branch using synthetic databases only. The final full regression run passed 518 tests; details are in docs/PRIVATE_BETA_STAGE1.md. No merge, deployment, backfill, production access or migration is authorized. The approved corrections below supersede the original archive-freeze and Membership-name proposals.

## 1. Evidence and scope

Audited repository: [footypickem-loyola/Footy_Pickem](https://github.com/footypickem-loyola/Footy_Pickem). Pinned `main` commit: [`adf2117aa7630cad9a044769525a88a05dd92575`](https://github.com/footypickem-loyola/Footy_Pickem/commit/adf2117aa7630cad9a044769525a88a05dd92575), PR #29 merge. All source references below refer to that commit. This remains the source-audit baseline; Stage 1 changes are documented separately in the PR.

The audit found **26 ORM tables, five football-reference tables, two shared-brief content tables, and one brief-scheduler table: 34 application-defined tables in total**, across separately managed stores. The appendix records their source-defined columns and constraints without importing the application.

This is an exact inventory of the reviewed source, **not a claim that production DDL has been introspected**. The application has several historical, incremental schema migrations; existing production nullability and indexes can differ from fresh ORM declarations. The deployed SHA, actual database schemas, triggers/indexes, SQLite versions, row counts, foreign-key state, configured database paths, and live n8n workflow versions remain preflight evidence to collect. Do not use remembered historical row counts as current baselines.

### Fixed decisions

- Option B: one shared football data layer; no per-league copies of fixtures, results, live states, or match events.
- Existing production league becomes `leagues.id = 1`, named Loyola Pick ’Em.
- Existing `players.id` values become Membership IDs without changing historical foreign-key values.
- One account may hold one Membership in each of multiple leagues. Membership persists across that league’s seasons.
- Favorite Club is an optional, editable `users.favorite_club_id` referencing shared `clubs.id`.
- Existing picks, matchups, result records, recaps, sources, delivery receipts, live-event revisions, and historical IDs remain intact.
- First implementation PR is prepared only after review. Merge and production changes each require explicit approval.

### Proposed additions to review

1. Preserve physical table names initially; improve domain names through ORM/service terminology.
2. Add a League Week–Fixture linking table. This records each league’s actual fixture slate independently of future kickoff changes.
3. Add season-roster rows so leaving a league does not remove someone from historical standings. Official Results remain canonical for active and historical scoring; settlement-freeze snapshots are deferred.
4. Introduce verified account identity before opening League #2. The current name-plus-room-code session is insufficient for account ownership.
5. Keep result corrections under platform administration. A league commissioner cannot change shared football facts.
6. Keep Correspondent sources/classifications league-owned for this foundation; keep existing shared football briefs global. Before League #2 Correspondent activation, explicitly evaluate sharing the public X corpus while keeping league-specific classifications, contexts, submission notes and private sources isolated. This follow-up does not authorize source deduplication or rekeying now.

## 2. Domain boundaries and identity preservation

| Domain concept | Physical storage recommendation | Identity treatment |
|---|---|---|
| Competition | New `competitions` | Internal key distinct from each provider’s competition ID |
| Football Season | New `football_seasons` | One canonical season per competition; verified legacy mapping |
| Football Matchweek | New `football_matchweeks` | Designated football round, independent of actual kickoff dates |
| Fixture | Existing `fixtures` | Preserve every existing ID; add canonical season/round/club references |
| Result / Live State / Event | Existing tables | Preserve IDs, values, provider links, and revision history |
| Club | New `clubs` | Stable identity across seasons and leagues |
| User | New `users` and `user_identities` | Account identity; never inferred solely from a display name |
| League | New `leagues` | Loyola explicitly gets ID 1 |
| Membership | Existing `players` | Existing ID and `name` retained; add `league_id`, `user_id`, role/state |
| League Season | Existing `seasons` | Existing ID, code, archive flags retained; add league and football-season IDs |
| League Week | Existing `weeks` | Existing ID, number, status, finalization time retained |
| Matchup / Pick | Existing `matchups`, `picks` | Existing relationships continue to point to the same `players.id` |

Example: `players.id=1` remains the Loyola Membership with `league_id=1` and a newly assigned `user_id`. If that account joins League #2, create another `players` row pointing to the same `user_id`. The new Membership ID comes from the database allocator; it is not assumed to be 7. No historical `player_id`, `player_a_id`, `player_b_id`, or `first_picker_id` changes.

### Why a fixture-slate link is necessary

Today `fixtures.week_id` points to a game Week. That relationship cannot represent two leagues using one fixture. Add `league_week_fixtures(week_id, fixture_id, display_order)`. Backfill League #1’s rows directly from the existing fixture-week associations. League #2 gets different League Week IDs, with links to the **same fixture IDs**.

Keep `fixtures.week_id` unchanged as legacy provenance during the transition. New code must not use it to infer ownership. At hardening it becomes nullable for future shared fixtures created before any league has joined. Similarly, retain `fixtures.match_number` as legacy numbering; league display order comes from the new link.

Freeze a league’s slate once drafting begins. Rescheduled kickoff times do not move or delete committed picks. Changes to a provider’s round assignment need explicit reconciliation; neither ingestion nor a migration may silently reassign an existing league slate. A link requires matching football seasons, but need not forever equal the fixture’s current provider round after a reviewed rescheduling correction.

## 3. Exact proposed target schema contract

This is the proposed **League #2-ready target**, distinct from the smaller first additive PR. Unless listed otherwise, existing columns, values, PKs, defaults, and uniqueness rules remain as recorded in the appendix. Physical renames and deletion of compatibility columns are deferred.

Notation: `I` = integer; `T` = text; `D` = UTC datetime; `B` = integer boolean constrained to 0/1; `?` = nullable. Unmarked columns are NOT NULL. PK = primary key; UQ = unique. Foreign keys default to `ON DELETE RESTRICT`, no cascading deletion of historical data. Every FK used for lookup gets an index unless already covered by a PK/UQ prefix. New timestamps use an explicit UTC application default; no fabricated historical timestamps. Constraints are the target contract, not executable migration SQL.

### 3.1 Shared football and Club identities

| Table | Columns | Keys / constraints |
|---|---|---|
| `competitions` | `id I`, `code T`, `name T`, `country_code T?` | PK id; UQ code; code nonblank. Internal code, e.g. PL |
| `competition_provider_links` | `competition_id I`, `provider T`, `external_competition_id T` | PK(provider, external_competition_id); UQ(competition_id, provider); FK competition |
| `football_seasons` | `id I`, `competition_id I`, `season_key T`, `name T`, `start_year I?`, `starts_on T?`, `ends_on T?`, `sync_enabled B=0` | PK id; UQ(competition_id, season_key); partial UQ(competition_id, start_year) when year is not null; FK competition; valid date order when both dates exist |
| `football_season_provider_links` | `football_season_id I`, `provider T`, `external_season_id T` | PK(provider, external_season_id); UQ(football_season_id, provider); FK football season |
| `football_matchweeks` | `id I`, `football_season_id I`, `number I` | PK id; UQ(football_season_id, number); number > 0; FK football season |
| `clubs` | `id I`, `name T`, `short_name T?`, `country_code T?`, `is_active B=1` | PK id; nonblank name; name is not a universal unique identity |
| `club_provider_links` | `club_id I`, `provider T`, `external_club_id T` | PK(provider, external_club_id); UQ(club_id, provider); FK club |
| `football_season_clubs` | `football_season_id I`, `club_id I` | Composite PK; FKs football season and club |
| `club_aliases` | `football_season_id I`, `normalized_name T`, `club_id I` | PK(football_season_id, normalized_name); composite FK(football_season_id, club_id) to football_season_clubs |

Provider codes require one normalization policy. Existing `football-data.org` in sync state and `football-data` in brief identities remain valid legacy spellings through an explicit adapter; do not rehash or rewrite stored brief identities. A provider competition ID of `8` means Sportmonks Premier League, **not Pick ’Em League #8**. Club alias normalization is deterministic, with a reviewed collision report; no fuzzy auto-merging.

| Existing table | Target additions / changes | Constraints and preservation |
|---|---|---|
| `fixtures` | Add `football_season_id I`, `football_matchweek_id I`, `home_club_id I`, `away_club_id I` | All four nullable in additive stage, required at hardening after verified mapping. FK season, round, clubs; home != away. Trigger requires round’s season = fixture season and both clubs in that season |
| `fixtures` compatibility | Retain all old columns; `week_id` and `match_number` become nullable only at hardening | Preserve existing values and `external_match_id` uniqueness. Retain UQ(week_id, match_number) for legacy rows; canonical identity is fixture PK plus provider links, never match number |
| `fixture_provider_links` | No columns need changing | Existing UQ(provider, external_fixture_id), UQ(fixture_id, provider). Add football-data links for verified existing external IDs without replacing Sportmonks links |
| `results` | No new columns required | Retain one result per fixture, manual authority, outcome-only legacy results. Target CHECK outcome in Home/Away/Draw; each score null or >=0; score pair either both null or both present. If scores exist, outcome must agree. Validate old rows before enforcing |
| `live_fixture_states` | No new columns required | One row per shared fixture; official results still take precedence over provisional live state |
| `match_events` | Add `club_id I?` | FK clubs; nullable for unmapped/non-team events. Existing provider/player IDs and revisions unchanged. If side identifies home/away, club must match corresponding fixture club |
| `api_sync_states` | Add `football_season_id I`; make old `season_id` nullable at hardening | Preserve IDs/counters/timestamps. UQ(football_season_id, provider); existing season_id uniqueness remains for compatibility. Existing rows retain their old League Season reference, but global workers use the football key |
| `club_season_enrichment` | Add `football_season_id I`, `club_id I`; relax old `season_id` at hardening | Existing composite PK(season_id, sportmonks_team_id) replaced, after backfill, by PK(football_season_id, club_id); preserve row values and mapping of old composite identities. Keep sportmonks_team_id, season ID, provider league_id, names, scorer JSON and timestamps. UQ(football_season_id, sportmonks_team_id) |

The enrichment table is a refreshable cache rather than a historical game relationship. Even so, record a one-to-one old-key/new-key manifest and preserve its contents during rekeying. The physical column `club_season_enrichment.league_id` remains a **provider competition ID**; do not backfill it with 1.

New `football_refresh_jobs`: `football_matchweek_id I`, `job_kind T`, `status T='pending'`, `created_at D`, `completed_at D?`, `claim_token T?`, `lease_until D?`, `last_error T?`. PK(football_matchweek_id, job_kind); FK football_matchweeks; job_kind initially `club_enrichment`; status in pending/running/completed/failed. Used for one global enrichment job per football round. Existing `pick_insight_refreshes` remains as historical league-linked task receipts; reconcile completed work and drain/fence outstanding old jobs before switching consumers. An explicit repair can requeue a completed global job with an audited reason; adding a second league does not create a second job.

### 3.2 User accounts and leagues

| Table | Columns | Keys / constraints |
|---|---|---|
| `users` | `id I`, `display_name T`, `email T?`, `email_normalized T?`, `email_verified_at D?`, `favorite_club_id I?`, `status T='unclaimed'`, `is_platform_admin B=0`, `created_at D`, `updated_at D` | PK id; partial UQ(email_normalized) for non-null values; FK favorite club; status in unclaimed/active/disabled; email fields jointly null or jointly present; verified_at requires email. Name nonblank |
| `user_identities` | `id I`, `user_id I`, `issuer T`, `subject T`, `created_at D`, `last_authenticated_at D?` | PK id; UQ(issuer, subject); FK user. Authentication uses verified issuer/subject, never display name or a supplied user ID |
| `leagues` | `id I`, `slug T`, `name T`, `status T='setup'`, `timezone T='America/New_York'`, `correspondent_enabled B=0`, `created_at D`, `updated_at D` | PK id; UQ normalized lowercase slug; status in setup/active/archived; names nonblank; timezone validated as IANA name in service |
| `league_invitations` | `id I`, `league_id I`, `membership_id I?`, `email_normalized T`, `token_hash T`, `expires_at D`, `accepted_by_user_id I?`, `accepted_at D?`, `revoked_at D?`, `created_at D` | PK id; UQ token_hash; FKs league, players, users. accepted user/time jointly set or null. Optional Membership must belong to league. Only token hash is stored |
| `league_notification_recipients` | `league_id I`, `kind T`, `email_normalized T`, `user_id I?`, `enabled B=1` | PK(league_id, kind, email_normalized); FKs league/user; kind recap/admin_alert. User-backed recipient must have a verified email and appropriate membership; explicit external recipients require platform-admin configuration |

The auth provider is an implementation choice still to review; this contract supports a verified external identity without choosing a vendor or storing passwords. An unclaimed User is not login-enabled. User display names are globally non-unique; only normalized Membership names are unique within their own league. Seed one User per existing Player unless reliable existing identity evidence proves otherwise; link verified accounts through explicit claim/invitation, not by matching the word “Steve.” Do not invent email addresses or preselect favorite clubs.

Identity-based sessions carry `user_id`; every request rechecks user and Membership state. The selected league is request context, not authority. Use league-qualified URLs so two browser tabs in different leagues cannot silently switch each other’s writes. Legacy URLs may explicitly redirect to League #1 after authorization; missing or invalid league context must never fall back to the first league for writes.

| Existing table | Exact target additions / changes | Keys / constraints |
|---|---|---|
| `players` (Membership) | Retain `id I`, `name T`; add `league_id I`, `user_id I`, `name_key T`, `role T='member'`, `status T='active'`, `joined_at D?`, `left_at D?`, `recap_opt_in B=1` | PK unchanged; FKs league/user; UQ(league_id, user_id). Replace global UQ(name) at hardening with UQ(league_id, name_key); Membership display names must be unique within each league for beta. name_key is NFKC(trim(name)).casefold(), maintained by the future application service; User display names remain globally non-unique. Stage 1 adds a nullable key and partial per-league unique index without backfilling or removing the old stronger constraint. role member/commissioner; status active/inactive; known left_at >= joined_at |
| `seasons` (League Season) | Add `league_id I`, `football_season_id I`, `rules_version T='classic-v1'`, `stake_minor_units I=500`, `currency T='USD'` | FKs league/football season; replace global UQ(code) with UQ(league_id, code); UQ(league_id, football_season_id) for beta; partial UQ(league_id) WHERE is_active=1; is_active/is_archived booleans, cannot both be 1; stake >=0 |
| `weeks` (League Week) | Add `football_matchweek_id I` | FK football round; retain FK season and UQ(season_id, number); add UQ(season_id, football_matchweek_id); require matching football season and matching number at creation. Preserve old room_code/status/finalized_at |

Added ownership columns start nullable without default 1, are backfilled explicitly, then become required. A permanent default of league 1 would hide omitted ownership in new code. Membership league/user associations become immutable after verified binding; correcting a mistaken identity link requires a separate audited operation. `players.name` remains the league display name; changing a User’s display name does not silently rename historical league records. `weeks.room_code` is retained for history but ceases to be an account credential.

`classic-v1` preserves the current scoring and snake-draft algorithm and $5/net-point behavior. Six seats and ten fixtures remain beta service validations, not global SQL cardinality assumptions. Do not change gameplay while splitting ownership.

| New league table | Columns | Keys / constraints |
|---|---|---|
| `league_season_memberships` | `season_id I`, `membership_id I`, `joined_at D?`, `left_at D?` | Composite PK; FKs seasons/players; trigger requires same league. Backfill distinct participants from historical matchups, picks and relevant source submissions; report inconsistent edges. Explicitly review unstarted-season roster instead of including all global players |
| `league_week_fixtures` | `week_id I`, `fixture_id I`, `display_order I` | PK(week_id, fixture_id); UQ(week_id, display_order); display_order > 0; FKs weeks/fixtures; trigger requires equal football season |

**Deferred product rule:** `league_week_result_snapshots` is excluded from the private-beta foundation. Official shared Results remain canonical for all league scoring, including archived seasons. A future settlement-freeze rule needs separate product approval.

**Membership remains stable across seasons.** Season-roster membership is what historical standings use; current inactive status does not erase that participation. Reactivating a former member reuses the same Membership rather than issuing new historical identity.

### 3.3 Matchups, picks, drafts and cross-table constraints

No additional `league_id` is required on every descendant. Ownership is derived through `matchup → week → season → league`; duplicate tenant columns would themselves need consistency enforcement. The repository/service API must nevertheless demand league context on every read and write.

- `matchups`: preserve all five columns. Add CHECK player_a_id != player_b_id and CHECK first_picker_id IN (player_a_id, player_b_id). Trigger on insert/update requires both Memberships on that League Season roster. Trigger prevents any Membership appearing in two matchups in the same League Week, including across the a/b columns. Parent reparenting is prohibited once descendants exist.
- `picks`: preserve all six columns and UQ(matchup_id, fixture_id). Add `selected_club_id I?` FK clubs in expansion; after a clean preflight require it for new classic-v1 picks. Trigger requires picker to be one of matchup’s participants, fixture to appear in that matchup’s League Week slate, and club to equal fixture home/away. Preserve `team` text exactly; it is a historical snapshot, not future identity. Existing code accepts home/away clubs only; do not infer a selectable Draw option from older project notes. Any historical non-club pick is a blocker to inspect, not a row to rewrite.
- `auto_draft_settings`: keep fields/UQ. Trigger requires Membership to be a matchup participant; CHECK enabled IN (0,1). Preserve confirmed_at.
- `auto_draft_preferences`: keep fields/UQ; add `selected_club_id I?` FK clubs; use the same participant/slate/club rules as picks. CHECK priority >=0. Do not introduce priority uniqueness that breaks existing in-transaction reordering.
- Draft turn/count, expected_count, read-only state, roster eligibility and permission checks remain in the serialized command service, performed **inside its existing BEGIN IMMEDIATE transaction**, with current User and requested League supplied. Membership IDs alone are not authorization.

Implement cross-table invariants with reviewed SQLite BEFORE INSERT/UPDATE triggers using indexed parent lookups plus application validation. Add guards on parent updates and deletes so an otherwise valid child cannot be invalidated indirectly. Use native FKs/UNIQUE/CHECK wherever those suffice. Direct SQL tests must exercise these invariants, not only route tests.

### 3.4 Correspondent, shared content and preservation

No schema rekeying is proposed for the 11 existing recap/Correspondent tables. Existing Week IDs already give them durable league ownership. Preserve their complete columns and keys. Required new cross-row validations:

| Relationship | Required invariant |
|---|---|
| source → week / fixture / submitting Membership | Fixture, when present, is in the League Week slate; submitting Membership is in that season’s roster |
| classification job → season / week | Job season equals week’s season |
| classification job item → job / source | Job and source belong to the same League Week |
| recap source usage → recap / source | Recap and source belong to the same League Week |
| recap selection → week / recap | Selected recap belongs to exactly that Week |
| email delivery → recap | Recap ownership controls claim/ack and recipient resolution; tokens cannot authorize a different week |
| notification / X state / receipt | Derive league from owning Week or collection; validate claim tokens against that exact owner |

Existing recap automation keys use the globally unique Week ID and are already collision-resistant across leagues. **Do not re-key historical automation keys, claim tokens, content/context hashes, or Gmail delivery markers.** New wire requests include explicit `league_id`, `league_season_id`, and `league_week_id`, checked against each other. Any human-facing season code/week number is secondary metadata.

Move global recipient configuration into `league_notification_recipients` by explicit, verified mapping for League #1. Preserve existing delivery `recipients_json` snapshots unchanged. League #2 must have its own recipient configuration; no fallback to League #1 environment values. For new deliveries, apply account verification, opt-in and current membership rules at reservation and revalidate authorization before send. A revoked recipient on a claimed unsent message requires cancellation/re-reservation through a reviewed delivery path; never silently edit an already-sent receipt.

For rollout, old n8n requests may be supported by an explicit League #1-only adapter. Do not expose League #2 targets to old workflows. Update target preparation, collector source payloads, claim, batch, reconcile, recap, notification, Gmail preparation and acknowledgement together in a versioned contract. Preserve stable sent-marker lookup and fencing. Repository workflow JSON is evidence of code only; live workflow parity still needs verification.

`football_reference_*` tables remain global provider/reference data with their existing schema and IDs. They may overlap canonical fixtures as a provider evidence cache; they are **not** a second per-league schedule and never overwrite historical game results implicitly. Link by verified provider identities in read adapters. A later physical consolidation is out of scope.

`brief_batches`, `team_briefs`, and the separate `scheduler` table remain global with unchanged schemas, content versions, frozen contexts, hashes and provider keys. They do not receive league IDs. Shared brief production remains once per football round/content version; each league’s Training Ground selects from that content using its own committed picks. Keep separate database boundaries; do not invent cross-database foreign keys. The scheduler’s singleton is appropriate for the currently single shared worker, unlike a singleton active Pick ’Em season.

## 4. Every current table: ownership and migration mapping

The table below covers all 34 application-defined tables found in the reviewed source. SQLite internal tables and any unseen production-only tables must be added to the inventory during preflight. “Keep” always includes existing PKs, rows, timestamps and serialized payloads unless an explicit addition is stated.

| Current table | Target owner | Migration treatment |
|---|---|---|
| `players` | League: Membership | Same rows/IDs; add User + League; remove global name uniqueness later |
| `seasons` | League: League Season | Same IDs/codes; add League + Football Season; scope active/code uniqueness |
| `weeks` | League: League Week | Same IDs/status/timestamps; add Football Matchweek; keep room-code history |
| `fixtures` | Shared football | Same IDs; add canonical season/round/club refs; old week link becomes compatibility only |
| `results` | Shared football | Keep ID/fixture/value/source; preserve outcome-only results |
| `fixture_provider_links` | Shared football | Keep IDs and links; add verified additional provider mappings |
| `live_fixture_states` | Shared football | Keep fixture PK and latest state; stop depending on one league’s archive/finalization |
| `match_events` | Shared football | Keep IDs, provider IDs, revisions and withdrawal state; optional Club ref |
| `api_sync_states` | Shared football operations | Keep IDs; add Football Season; one global provider sync per football season |
| `club_season_enrichment` | Shared football cache | Add canonical season/club refs; later rekey composite PK with identity manifest |
| `pick_insight_refreshes` | Legacy league-linked task receipts for shared work | Preserve week PK/season/status/claims; drain and replace future scheduling with global jobs |
| `matchups` | League | Keep IDs and all Membership FK values; add participant/roster invariants |
| `picks` | League | Keep IDs, matchup/Membership/fixture relationships and team text; add Club ref |
| `auto_draft_settings` | League | Keep IDs, participant scope and confirmation state |
| `auto_draft_preferences` | League | Keep IDs, priorities and fixture relationships; add Club ref |
| `weekly_recaps` | League | Keep every revision, text, context JSON/hash, model metadata and automation key |
| `correspondent_sources` | League | Keep raw text, notes, submitting Membership, hash and Week ownership |
| `correspondent_source_classifications` | League via source | Keep history; Pick ’Em impact cannot be reused as another league’s classification |
| `correspondent_classification_jobs` | League | Keep IDs/batch IDs; enforce season/week agreement |
| `correspondent_classification_job_items` | League via job/source | Keep reservations/custom IDs; require shared owner |
| `correspondent_x_collection_states` | League | Keep Week checkpoint/caps/leases; no accidental global promotion |
| `correspondent_x_page_receipts` | League via collection | Keep paid-page receipts and claim tokens; no repeated purchase from migration |
| `correspondent_email_deliveries` | League via recap | Keep delivery marker/recipient snapshot/status/Gmail IDs; never replay sent rows |
| `correspondent_notifications` | League via Week | Keep event deduplication/recipient snapshots/tokens |
| `recap_source_usages` | League via recap/source | Keep IDs/edges; assert same Week |
| `weekly_recap_selections` | League | Keep selected revision and timestamp; assert selected recap’s Week |
| `football_reference_seasons` | Shared football reference | Schema/IDs unchanged; provider identities stay provider identities |
| `football_reference_fixtures` | Shared football reference | Schema/IDs unchanged; not copied for League #2 |
| `football_reference_events` | Shared football reference | Schema/IDs/active/present flags unchanged |
| `football_reference_syncs` | Shared football operations | Existing sync provenance retained |
| `football_reference_tasks` | Shared football operations | Existing provider/season leases remain global |
| `brief_batches` | Shared football content | Existing football-round/version key retained; separate content DB |
| `team_briefs` | Shared football content | Existing fixture/team/version key, frozen context and generated text retained |
| `scheduler` | Shared football operations | Separate operations DB; singleton worker lease/heartbeat retained |

There are no existing account-owned tables. New `users` and `user_identities` introduce account ownership. Shared football facts do not become league-owned because one particular league originally imported them.

## 5. Major single-league code paths and required changes

Locations are pinned-source line anchors; function names are the durable references. The audit includes ORM queries, raw SQL, scripts, workers, templates and checked-in n8n contracts. It does not claim to inventory unexported production workflows or external scripts.

| Code path | Current assumption / risk | Required target behavior |
|---|---|---|
| App `current_player` (1823), `join` (6231) | Session stores player_name; name is globally unique; room code lets user select a seat | Verified User; resolve requested League’s Membership; explicit claim/invitation; invalidate legacy name-only sessions at auth cutover |
| `is_admin_session` (499), `admin_login` (4823), all admin routes | Global boolean administrator session | Separate platform admin from commissioner role; recheck account/role on every operation |
| `active_season` (1830), `requested_season` (1840) | Global latest/active/fallback; bad explicit season may fall back silently | Require League; resolve season inside it; reject foreign/invalid explicit season; per-league active selection |
| `season_players` (1849) | Infers players from matchups, falls back to all players | Explicit League Season roster; empty roster stays empty; historical roster survives departure |
| `init_season_from_api` (1616) | Creates fixtures, players, matchups together; globally matches names/code; deactivates all other seasons | Split shared football ingestion from league enrollment/scheduling; reuse existing fixtures; deactivate only this league’s season |
| `init_weeks_from_csv` (6329), CLI `main` (6402) | Global player names and season codes; destructive reset switch | Explicit league/season input; league-owned slate creation; legacy seed/reset paths fail closed on multi-league DB |
| `_delete_season_weeks` (6266) | Deletes results/events/provider links/fixtures via that season’s weeks | Disable for beta history. Any future league deletion cannot delete shared football facts. Cleanup must account for all historical children |
| `weekly_points_map` (1884), `weekly_pick_records` (1914), `scores_partial` (5976) | Seed output with global Player query | Use week/season roster only; no zero-point strangers from League #2 |
| totals/H2H/club records/leader stats/standings (1946–2295) | Correctness depends on season/player inputs; string team comparisons | Explicit owner-scoped loaders; Club-based future calculations; preserve classic-v1 scoring with shared official Results for historical and active seasons |
| tabs/current/open/season, `shell`, `insight_context` (5824) | Global season selector; analytics helper lists all seasons | League-qualified links/forms/HTMX GETs; all lists and chart inputs restricted to selected league |
| `load_matchweek_model` (4596), `available_fixtures_for_matchup` (1879) | Fixtures selected by fixture.week_id = league week | Fetch through league_week_fixtures; retain historical display_order; projected view models carry correct League Week ID |
| `v3_fixtures.build_fixtures`, `v3_matchweek`, `v3_table_results`, `v3_analytics` | Inputs assume fixture.week_id belongs to displayed league | Scoped DTOs/loaders; do not mutate shared ORM fixture.week_id to impersonate League #2. Pure helpers consume already scoped inputs |
| `/pick` (6111), `/auto-draft` (6159), `DraftService.command/_pick/unavailable` | Participant ID and legacy fixture.week_id checks; no User/League context | Revalidate User→Membership→League inside transaction; fixture membership through slate; retain uniqueness/expected_count protections |
| `pick_insight` (6068), `shared_brief_view.selected_briefs` | Matchup ownership uses current global Player; fixture/week joins | Authorize Membership in requested League; facts from shared football; private queues never enter shared content |
| `fixture_intelligence`, `pre_match_brief.build_pre_match_context`, `fixture_intelligence_eval` | Raw schedule/results joins through game seasons/weeks | Football-only data loader keyed by Football Season; personalized picks supplied separately and league-scoped |
| `_sync_state` (1583), `sync_results_from_api` (1719), `/tasks/sync-results` (5167) | One active game season determines fetch and result updates | One fetch/update per Football Season; enumerate linked active league weeks separately; preserve manual overrides |
| `_set_week_status_without_commit` (1597), `count_results_for_week` (4522), update/finalize | Counts via fixture.week_id; finalization enqueues league-keyed enrichment | Count each League Week slate; preserve independent status/timestamps; enqueue one football enrichment job. Recompute all affected active League Weeks in result transaction |
| `/set_result` (6182), `/admin/set-results` (5752), outcome options | League admin writes shared Result after old week check | Platform permission for corrections; authorize selected fixture, validate slate where relevant; auditable correction; all active consuming leagues refresh |
| `live_sync.resolve_fixture`, `ingest`, `polling_window` | Joins Week/Season to identify PL; ignores ingest when owning Week finalized/archived | Canonical competition/season/fixture lookup; no dependency on one league’s status. Football lifecycle decides global polling; each league decides presentation |
| `live_matchweek.factual_context/enrich_matchweek` | Facts from old fixture.week_id, consequences merged into one model | Shared event facts for slate; compute Pick ’Em impacts independently for each league/matchup |
| `sportmonks_season.sync_season`; `pick_insight_enrichment.supported_season/cached_enrichment` | Hardcoded year-2/2026 and league-season cache key | Verified football-season/provider mapping; global canonical season/club cache; do not mistake provider league_id=8 for tenant ID |
| `pick_insight_tasks.enqueue/run_task` | Global active_season; task PK is league Week ID | Football-round task idempotency/lease; migration reconciliation of legacy task receipts |
| `shared_brief_context`, `shared_brief_scheduler.schedule`, `shared_brief_worker` | Global content keys already suitable, source joins through game weeks; fixed 2026 mapping | Canonical football loader; preserve content keys/validated current coverage; creating League #2 causes zero additional brief generation |
| recap context builders (2303, 4343), classifier/writer loaders | Week-scoped but derived from global players and fixture-week joins | Explicit league-season roster/slate, only that league’s standings and sources; immutable old context JSON/hashes |
| `ingest_correspondent_source` (3364) / single and batch APIs | Resolves global active season by code; submitted_by name | Versioned explicit league/week contract; verified Membership ID; no name-based account inference |
| `_correspondent_automation_week` (5203), `/targets` (5236) | Exactly one global active season; ambiguous season_code/week_number wire key | Enumerate opted-in leagues; validate explicit owner IDs; preserve Week-based durable keys |
| X claim/checkpoint, classification submit/reconcile, recap generation, delivery/notification claim/ack | Per-Week records work, request resolution is global | Owner-bound tokens and tasks; same ownership across cross-row links; revoked/fenced work cannot write elsewhere |
| `_configured_email_recipients`, `claim_recap_email_delivery` (3906) | One environment recipient list | League-specific recipient settings, unchanged existing delivery snapshots and sent markers |
| Three checked-in `n8n/*.workflow.json` | Carry season_code/week_number through HTTP, code nodes, email claims/acks | Versioned league-aware payload end to end; League #1 adapter until parity verified; never fan out one private context to all leagues |
| `scripts/*`, trigger scripts, test seed/reset/copy tools | Direct IDs, legacy assumptions, some destructive repair operations | Audit allowlist; force explicit environment + league/season; disable old destructive tools on new schema; no automatic data reset |
| `ensure_database_schema` (1180), import-time call, connection pragma hook (752) | Import can migrate DB; connection hook enables WAL/synchronous but not foreign_keys | New migrations explicit/versioned; no app import for audit. Enable and verify FKs on every application/raw SQL connection before transactions; fail closed on incompatible schema |

Additional review findings:

- The schema currently has global UQ on `players.name` and `seasons.code`. Adding `league_id` alone will not allow a second Steve or a second `year-2`.
- Shared event facts can have different league consequences: the same winning goal may help Steve in one league and hurt him in another. Never cache the personalized consequence under fixture ID alone.
- `league_id` in Sportmonks payloads remains a provider competition identifier. Request and schema naming must distinguish it from `pickem_league_id`/League ownership at boundaries.
- Current code has fixed ten-pick guards and six-player initialization. Retain these beta rules; test two independent six-seat leagues, including shared accounts, rather than assuming twelve distinct Users.
- Personal HTML responses should be private/no-store. Any future data cache must include League Season/Week and Membership where personalized; global football/brief cache keys must exclude league identity.
- Root redirects, pagination, forms, dialogue links, HTMX refreshes, error pages and returned HX-Trigger URLs all need preserved league context. A correct initial page alone does not establish isolation.

## 6. Staged migration and approval gates

### Stage 0 — approve this design and collect the evidence baseline

Before implementation, reconcile remaining product choices in section 10. Before any migration rehearsal is called production-representative, obtain a recent coherent database backup plus deployed SHA and schema metadata. Existing high-level schema review is not a substitute for this baseline.

Inspect the snapshot with SQLite read-only access, never by importing the Flask app. Collect `sqlite_schema`, `PRAGMA table_info`, `foreign_key_list`, `index_list/index_info`, triggers/views, SQLite version, application/user_version, integrity and FK checks, complete row counts, min/max IDs, identity sets, and legacy-column hashes. Inventory every persistent DB/store and any filesystem sessions. Record live workflow export hashes and current process/worker ownership separately; keep credentials out of the manifest.

### Stage 1 — first additive PR, only after design review

Proposed scope: explicit migration-version infrastructure; new empty account/league/football identity and linking tables; nullable ownership/canonical-reference columns; an offline preflight report; migration tests using synthetic legacy databases and an approved sanitized snapshot. No default league 1, no automatic backfill on startup, no table renames/rebuilds, no altered login/routing/polling, no new league enabled, no production runner invocation. Deployment-inertness is a hard criterion: remove the existing import-time ensure_database_schema/create_all path, keep new tables out of ORM metadata, and require explicit commands for both legacy schema initialization and foundation migration. Brief-worker startup must also require a pre-initialized operations store.

Add `schema_migrations(version T PK, checksum T, applied_at D, before_schema_hash T, after_schema_hash T)` for committed migrations. The migration command must require an explicit database target, validate expected version/schema checksum, and acquire a bounded exclusive migration window. It must not import the web application or load production credentials. Migration/version records are committed together with that stage; failed transactions leave neither half-applied schema nor a success marker. Re-running validates completed work and exits, rather than reseeding IDs.

This PR remains behavior-inert until its explicit command is authorized. A changed ORM definition plus import-time create_all is not sufficient protection: startup must not be able to run this new foundation migration implicitly. Review how an already-migrated additive database behaves under both the old and new application builds.

**Approval to prepare this PR is not approval to merge, deploy, or migrate production.**

### Stage 2 — deterministic backfill on a copy

1. Assert no conflicting League ID 1; create Loyola once. A conflict aborts; do not overwrite it.
2. Map each old Player ID to that same row’s league_id=1 and a new unclaimed User. Save a deterministic Player→User manifest; retries reuse it. No names/emails/favorites invented or historical foreign keys changed.
3. Add league_id=1 to all reviewed existing League Seasons. Map each to a canonical Football Season using actual fixture/provider evidence. Do not assume `year-1` equals a particular calendar year. An unresolved season can be marked as an isolated legacy season_key for analysis, but production hardening waits for an explicitly reviewed mapping.
4. Create Football Matchweeks from each verified season/round. Backfill `weeks.football_matchweek_id`, retaining every Week ID and number.
5. Create Clubs/provider mappings/aliases from reviewed distinct names and provider identities. Backfill fixture club/season/round references; preserve fixture strings, dates and provider IDs. If canonical duplicates/conflicts are found, stop. Do not merge fixtures and repoint picks automatically.
6. Populate League #1 fixture slates with the original fixture IDs and original ordering. Populate season rosters from actual historical participation; explicitly resolve empty/unstarted seasons.
7. Add selected_club_id to picks/preferences where the exact historical string resolves to a fixture participant. Report all ambiguities before continuing.
8. Map sync/cache/task state to shared football identities. Completed cache work remains completed; unresolved claims are drained/fenced without discarding provider acknowledgements.
9. Verify historical scoring against the same canonical Results before/after migration. Do not add archive snapshots, fabricate absent results, or change any official Result during backfill.
10. Preserve all Correspondent tables and shared-brief/reference content unchanged. Migrate recipient configuration to League #1 explicitly; keep all existing snapshots, caps, leases and sent markers.
11. Run the complete integrity report and compare with the immutable pre-migration baseline. Re-run the migration to prove no additional rows or ID changes.

Backfills may write **new columns/new tables only** at this stage. No blanket UPDATE of old `updated_at`, status, hashes, or result values. No provider fetch or AI generation is necessary to preserve existing records.

### Stage 3 — scoped application paths, still League #1 only

Introduce league-aware repositories and request context; account claim/authentication; explicit Membership authorization; slate-based fixture reads; football-based sync/live/brief loaders; per-league analytics and Correspondent payloads. Validate on an isolated copy and compare League #1 outputs with the old application. Outbound integrations use stubs/local capture; copied tokens and recipients are not permission to send.

Avoid indefinite dual writing. In the League #1 compatibility stage, old link fields remain frozen/explicitly populated where needed; the new loader is the chosen authority. Compare old vs new reads at a fixed snapshot. Reject creation of League #2 until every private read/write and background path has been scoped.

Football result updates and affected active League Week status transitions occur in the same short database transaction for beta. This avoids committing a shared result while leaving one league’s finalization stale. Fetch outside the transaction; acquire lock, validate authority/manual overrides, write each shared result once, recompute every affected active slate, and enqueue one shared enrichment job. A failure rolls back all of those changes. Archived and active seasons consume canonical Results. Archiving a league does not freeze football facts or stop another league’s shared feed; later authorized official result corrections can affect historical computed standings. Stored recap text/context remains immutable audit history. Live ingestion remains provisional and does not write official results.

### Stage 4 — enforce constraints and remove obsolete uniqueness

After a clean preflight, enforce non-null ownership, per-league uniqueness, correct FKs, cross-row triggers and new indexes. SQLite changes such as removing column-level global UNIQUE constraints require a carefully reviewed table-rebuild migration. Copy original IDs and legacy values explicitly, preserving dependent indexes/triggers/views and FK relationships; do not alter referenced identity values. This is a separate reviewed migration, not hidden in the first additive PR.

Run integrity/FK checks and logical baseline comparison inside the migration workflow, rehearse failure/restart, and verify enforcement from fresh independent connections. A release/deployment compatibility gate must block the original app build once the schema becomes incompatible; new releases also check the migration version before serving requests. A schema version marker alone cannot make an old build tenant-aware, and SQL write constraints cannot prevent its unscoped reads. No `writable_schema` shortcut.

### Stage 5 — League #2 rehearsal and controlled activation

Create League #2 only on the isolated rehearsal system first: one shared account in both leagues, distinct Membership IDs, same Football Season/fixtures, independent rosters/matchups/picks/recaps. Complete the isolation matrix below, including concurrency and automation retries.

Production rollout is a separately approved operation with explicit commits, database target, backup, maintenance window, migration version and rollback build. Initially leave League #2 automation disabled until its scoped workflow/recipients are verified. Activating League #2 must cause **no additional fixture import, live polling loop, or shared-brief batch**.

## 7. Rollback and integrity strategy

### Preserve identity and semantics, not merely row counts

Create a manifest per table containing original PKs, sorted canonical hashes of **all legacy column values**, relationship edges, and counts by season/week. Keep JSON/text payloads byte-for-byte for preservation checks, while computed-output comparisons normalize only genuinely nonsemantic presentation differences. Compare only original columns when new nullable columns are added. New columns/tables have their own expected mapping manifest.

| Integrity family | Acceptance condition |
|---|---|
| Database structure | integrity_check = ok; foreign_key_check empty; all expected indexes/triggers present; no unexpected tables altered |
| Identity | Old PK sets exactly equal before/after; no reuse/deletion/renumbering; existing picks/matchups retain identical FK values |
| Values | Original-column hashes equal for every protected row; no change to names/team text, scores, timestamps, status, provider IDs, revisions or serialized content |
| Ownership | Every old Player/Season maps to League #1; every Membership User binding accounted for; no unresolved null owner or cross-league edge |
| Football | Every original fixture has exactly one canonical season/round and reviewed club mapping; no provider ID collision; each existing result/live/event keeps original fixture |
| Slates/rosters | League #1 slate fixture IDs and order exactly match old queries; every matchup participant belongs to that season roster |
| Gameplay | All weekly and seasonal totals, for/against/net, rankings/ties, payouts, draft order, available selections, H2H, club records and chart series equal at fixed input/time |
| Archive | Finalization/archive flags unchanged; historical and active reads use canonical Results; no inferred historical scores, result snapshots or dates |
| Correspondent | Existing recap selections/text/context/hash/source usage unchanged; classifications/jobs/receipts/delivery state identical; zero re-sends or extra paid-page claims |
| Shared content | Reference IDs/rows and all brief content keys/hashes/status/attempt counts unchanged; scheduler ownership understood, not copied into an active competing worker |
| Retry | Second migration invocation is a no-op; failure halfway rolls back cleanly; recovery does not create another User/League/fixture or reset a lease |

Use SQLite’s backup API for a coherent snapshot; a naked copy of an active database file is not the rollback plan. Store uniquely named, timestamped backups with checksum, migration version and deployed SHA. The existing helper returns an old suffix-named backup if present; do not rely on that behavior for a fresh foundation checkpoint. Verify an actual restoration to a separate path and run baseline checks there. See [SQLite backup documentation](https://www.sqlite.org/backup.html).

Foreign keys must be enabled and checked on every new connection **before** its transaction. The current general connection hook does not do this. Switching the setting inside a transaction is ineffective; the migration and worker connection paths need explicit handling. See [SQLite FK documentation](https://www.sqlite.org/foreignkeys.html). Rebuilds follow the documented generalized procedure for the deployed SQLite version: [ALTER TABLE](https://www.sqlite.org/lang_altertable.html).

### Checkpoints and rollback boundaries

| Point of failure | Safe response |
|---|---|
| Additive migration before commit | Roll back transaction; leave old app/schema active; retain diagnostic report |
| Additive committed, no new canonical-only writes | Revert to a tested old-compatible app and leave harmless columns/tables; do not automatically drop data |
| Backfill/scoped reads, League #1 only | Use the tested compatibility release and reconciled ownership links. Original app is allowed only if its writes cannot violate new required columns/guards |
| Hardening or League #2 writes have begun | **Never blindly redeploy the original single-league app.** Use a tested multi-league-aware previous release or temporarily disable writes and forward-fix |
| Full restore required | Stop all web/worker/cron/n8n writers, preserve failed DB as evidence, restore matching checkpoint and compatible app/config, reconcile external acknowledgements, then resume |

A backup restore after new writes would discard those writes. Therefore the production migration window pauses writes and does not reopen until validation passes. After reopening, application rollback/forward repair is preferred; any restore requires an explicit plan to preserve/replay intervening game data. This limitation must be visible at approval time, not described as a guaranteed lossless rollback.

The backup set covers game/reference storage, separate shared-brief content/operations DBs, and relevant workflow versions. A coordinated pause gives a consistent checkpoint across those separate stores. Restoring a database cannot unsend Gmail or undo an OpenAI/X purchase. Reconcile uncertain external jobs and stable delivery markers before resuming; do not clear receipts or rerun every unfinished item automatically. Reference and brief processes must never run against a copied production store during rehearsal.

## 8. Multi-league isolation test specification

These tests are **specified, not yet implemented or run**. Existing repository tests were read as context, not executed against production. Implementation should reuse the existing draft/live/Correspondent tests and extend them with explicit two-league data. Tests must demonstrate both allowed sharing and prohibited leakage.

### Fixed test arrangement

- L1 Loyola and L2 Beta League, each with six Memberships and independent League Seasons named `year-2`, both active concurrently and referencing one shared PL Football Season.
- Steve User U1 has Membership M1 in L1 and M7 in L2 (illustrative test IDs). Another User has the same display name “Steve”; identity remains distinct. One User has L1 access only, one L2 only, and one neither.
- Same ten canonical fixture IDs appear in both Week-5 slates, but different League Week IDs, matchup IDs, pick ownership, queue orders, scores and recap content.
- L1-only commissioner, L2-only commissioner, and an explicitly privileged platform admin.
- Both league-private marker text and private autodraft priorities seeded as canaries. Shared football event data and shared brief text deliberately identical.
- An archived L1 season with an outcome-only result and a missing result; a new football season used by only L2; a postponed fixture; a withdrawn event; pending/claimed/completed Correspondent work.
- File-backed disposable SQLite DB for transaction/concurrency tests; separate independent connections/processes. Fixed clock for hashes, deadlines and live freshness. Network/AI/email clients stubbed; external calls counted locally.

| ID | Action | Required assertion |
|---|---|---|
| I01 | Backfill legacy Players then add U1 to L2 | M1 and every old FK unchanged; new Membership differs; same User links both; no new fixture copies |
| I02 | Insert duplicate Membership for U1 in L1; test same and differently cased/whitespace Membership names in L1 and L2 | Duplicate seat and normalized display name within a league rejected; same name allowed across leagues after hardening. User display names remain non-unique and never authorize a seat |
| I03 | Edit/clear favorite club from U1’s account; supply nonexistent Club ID | Change visible for U1 across both leagues; no impact on Membership/picks; invalid FK rejected; another User cannot edit U1 |
| I04 | Set L2 season active/create same `year-2` code | L1 remains active; per-league active/code UQ enforced; two active seasons inside L1 rejected |
| I05 | Query empty/new L2 roster | Empty result, never all global players; no phantom zero-score entries |
| I06 | L1-only User requests L2 tabs, partials, analytics, season selector, recap, pick insight and source endpoints | Uniform authorization denial (403/404 policy), no foreign names/IDs/private marker text; DB unchanged |
| I07 | U1 uses two browser tabs, L1 and L2, then changes selected league | Each form/HTMX URL retains its explicit league; L1 stale form cannot submit into L2 |
| I08 | Tamper league/season/week/matchup/Membership IDs in a valid pick request | All mismatched ownership combinations rejected; counts and original-column hashes unchanged |
| I09 | M1 and M7 select same fixture in their independent matchups | Both valid according to each draft’s turn; neither consumes fixture availability in the other matchup/league |
| I10 | Cross-league fixture from another season or non-slate round submitted | DB trigger and command service both reject; same football fixture legitimately on both slates is allowed |
| I11 | Insert Matchup across L1/L2 participants or use nonparticipant first picker | Direct SQL rejected; same-Membership self-match and second matchup in one Week also rejected |
| I12 | Insert Pick/auto preference for nonparticipant, wrong club or wrong slate | Direct SQL rejected, including UPDATE attempts; parent reparenting cannot circumvent validation |
| I13 | Concurrent manual/automatic picks in one matchup with same expected_count | At most one conflicting command succeeds; no duplicate fixture; bounded conflict response; no silent replay |
| I14 | Simultaneous legal picks in L1/L2 using same football fixture | Each commits independently or returns explicit retryable contention; no cross-league locking semantics beyond SQLite writer serialization |
| I15 | Edit/remove/reorder/toggle/confirm another league’s auto-draft queue | Denied; queue content never appears in shared brief cache, opponent response, or another league |
| I16 | Membership becomes inactive or User disabled after session creation | Next protected write denied using fresh DB state; archived roster and prior points remain intact |
| I17 | Change U1’s display name; another User chooses Steve; replay old name-only session | No identity reassignment; legacy session rejected after auth cutover; league display history unchanged unless separately edited |
| I18 | Invitation accepted twice, expired, revoked, wrong verified email, or bound to another league’s seat | Only one valid claim; no reassignment of occupied historical Membership; consume token and bind identity atomically |
| I19 | Compute weekly totals, league standings, H2H, club records, payouts and chart series | Only selected league’s roster/picks; compare exact expected independent numbers, including ties and an empty season |
| I20 | Poll one shared result used by both leagues | One provider request/result row; both relevant active weeks recompute; no duplicate imports; correct different point consequences |
| I21 | Repeat same poll and force an exception between two leagues’ finalization steps | Repeated poll causes no duplicate tasks/delivery actions; exception rolls back result and both status updates together |
| I22 | Archive L1 while L2 uses same active football season | L2 ingest/polling continues; L1 historical scoring still reads canonical Results; no global season deactivation |
| I23 | Later fill a missing global result or correct an outcome used by archived L1 | Both historical L1 and active L2 scoring reflect the authorized canonical result under existing scoring rules; stored recap text/context stays unchanged; no archive snapshot is created |
| I24 | L1 commissioner attempts manual result/live/provider mapping changes | Denied; platform admin can perform authorized shared correction; manual-result precedence preserved on later provider poll |
| I25 | Same live goal then VAR withdrawal reaches both leagues | One event/revision trail; independent league impacts; official results still take precedence; no cross-league player names in cached facts |
| I26 | Postpone kickoff or receive changed provider round | Fixture ID/picks remain; locked league slate unchanged; ambiguous reassignment is flagged; frozen brief validation still refuses mismatched schedules |
| I27 | Both leagues request shared briefs/enrichment for same round | Same content identities; one generation/refresh job, no second paid batch; league-private selections remain separate |
| I28 | League #2 follows an older brief content version or lacks a supported mapping | Safe existing unavailable/fallback behavior; no synchronous page-view generation and no fabricated provider mapping |
| I29 | Generate L1 and L2 recap contexts from shared fixtures | Each contains only its league’s participants, standings, sources and impacts; private marker text never crosses; old stored contexts untouched |
| I30 | Source ingestion/classification/job import/recap selection references another league’s row | Request rejected and direct-SQL consistency trigger rejects cross-owner edges |
| I31 | Concurrent source retry, classification reservation or recap automation retry | Correct existing per-Week/source uniqueness and stable keys; no cross-league reservation collision or duplicate paid work |
| I32 | L2 email claim with no L2 recipients configured | Fails closed; never uses L1 environment list. Configured case sends only to local stub’s expected L2 recipients |
| I33 | Replay sent delivery, wrong-week claim token, stale lease or crash after simulated send before ack | No cross-league ack; fenced stale worker; stable marker lookup prevents simulated duplicate send |
| I34 | Old n8n v1 payload arrives after L2 exists | Explicit L1-only compatibility contract or rejection; never chooses arbitrary active season; new contract preserves owner IDs through all code nodes |
| I35 | Delete/archive/reset L2 through legacy CLI or repair script | Unsafe legacy operation refused; all L1 and shared rows unchanged; archive is nondestructive |
| I36 | Use new raw SQLite connection for writes with malformed foreign keys | Enforcement verified as ON; invalid parent rejected; app/worker connections tested separately |
| I37 | Re-run/interrupt migration, restore checkpoint, and test compatible rollback build | No extra IDs/rows, no partially committed version, exact legacy-column hashes; restore validated at a separate path |
| I38 | Attempt to select original single-league build after L2 activation in a disposable deployment rehearsal | Release compatibility gate rejects it before serving traffic; schema marker alone is insufficient. Rollback uses approved multi-league-aware build |
| I39 | Inspect error responses, cached pages, pagination and generated insight/HX-Trigger URLs | No foreign data leaks; correct league context on all follow-up requests; personalized responses private/no-store |
| I40 | Run unchanged League #1 regression fixtures before/after migration at fixed time | Equal picks/matchups/standings/payouts/archive outputs/recap selections/live facts; no unexpected baseline differences |

For denied mutations, assertions include complete scoped row snapshots before/after, not just an HTTP status. For allowed shared updates, assert the precise shared-row change and the correct independent league effects. A successful League #1 smoke test without these adversarial cases is insufficient.

## 9. Index and enforcement checklist

In addition to PK/UQ-created indexes and normal FK indexes:

- `players(user_id, status)` for account league selection; `players(league_id, status)` for roster administration.
- `seasons(league_id, is_archived)` plus per-league active partial unique index.
- `league_season_memberships(membership_id, season_id)` in addition to season-first PK.
- `weeks(football_matchweek_id)` for fan-out; existing season/number UQ handles navigation.
- `league_week_fixtures(fixture_id, week_id)` for affected-league lookup; week/order UQ for draft display.
- `fixtures(football_season_id, kickoff_utc)` and `fixtures(football_matchweek_id, kickoff_utc)` for schedule/live work.
- `matchups(week_id, player_a_id)` and `matchups(week_id, player_b_id)` for participant uniqueness trigger lookups.
- `picks(player_id, matchup_id)` and `picks(fixture_id)`; existing matchup/fixture UQ remains.
- Correspondent Week/source/job/recap FK indexes to keep owner joins and token validation bounded.
- `football_refresh_jobs(status, lease_until)` for global worker claims.

Check actual indexes before creating duplicates. For trigger-enforced cross-row rules, define each INSERT/UPDATE/DELETE path and its parent-side counterpart in the implementation PR. Authorization is always service-layer; SQL integrity triggers are not a substitute for verifying the logged-in User.

Operational feature switches must be explicit and independent: multi-league routes enabled, League #2 active, shared job consumer enabled, per-league Correspondent enabled. They are rollout controls, not permission grants. They must fail closed when schema readiness or league configuration is missing.

## 10. Review decisions and unresolved evidence

**Approved review outcome:** preservation-first architecture and Stage-1 additive PR preparation only, with the four corrections recorded here. No merge, deployment, backfill, or production migration is authorized.

Accepted foundation decisions (later-stage implementation remains gated):

1. Keep physical `players`/`seasons`/`weeks` names for the foundation while exposing Membership/LeagueSeason/LeagueWeek in application code.
2. Use the explicit fixture-slate link and season-roster table.
3. Defer/remove league_week_result_snapshots. Shared official Results remain canonical for historical and active league scoring. A settlement-freeze product rule requires a later explicit decision.
4. Restrict shared result corrections to platform admins; league commissioners manage their own rosters/settings.
5. Require verified account claims before League #2 opens; select the actual authentication provider in the account PR. Existing users retain their historical Membership IDs throughout.
6. Keep Correspondent raw sources/classifications league-owned initially, with global briefs/reference data reused. League #2 Correspondent activation follows explicit recipient/workflow verification.

Evidence still required before migration-ready sign-off:

- Deployed commit versus audited main; actual production schemas and SQLite/FK settings.
- Current counts/PK sets/legacy-column hashes and proof that the backup restores.
- Verified mapping of historical League Seasons, Clubs and fixtures to football identities; any duplicate provider/name collisions or unexpected historical pick values.
- Current game/reference/content/operations DB locations and all active writer processes.
- Live n8n exports versus repository JSON; in-flight external jobs and email claims; explicitly approved League #1 recipient mapping.
- Per-Membership account claim destination and commissioner/platform-admin assignments. Do not derive these from names or stored room codes.

If any of these disagree with the proposed contract, amend the plan and review the discrepancy. Do not “repair” production data to make a migration pass.

## Appendix A — Exact source-defined schema inventory

The following declarations are extracted statically from the pinned source. `default=` here means the SQLAlchemy/application default as declared, not necessarily a database server default. Omitting `nullable=False` in these existing models generally permits nulls; an actual migrated DB can differ. These declarations are evidence, **not runnable target migration code**. Relationships and non-column model methods are omitted; column expressions and table-level constraints are retained.



### players

Source: [pickem_flask_htmx_tabs.py, Player](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L767)

```python
id = Column(Integer, primary_key=True)
name = Column(String, unique=True, nullable=False)
```

### seasons

Source: [pickem_flask_htmx_tabs.py, Season](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L772)

```python
id = Column(Integer, primary_key=True)
code = Column(String, unique=True, nullable=False)
name = Column(String, nullable=False)
is_active = Column(Integer, nullable=False, default=0)
is_archived = Column(Integer, nullable=False, default=0)
api_competition_code = Column(String)
api_season_year = Column(Integer)
created_at = Column(DateTime, default=datetime.utcnow)
```

### weeks

Source: [pickem_flask_htmx_tabs.py, Week](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L783)

```python
id = Column(Integer, primary_key=True)
season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False)
number = Column(Integer, nullable=False)
room_code = Column(String, nullable=False)
status = Column(String, default="drafting")
finalized_at = Column(DateTime)
__table_args__ = (UniqueConstraint("season_id", "number", name="uix_season_week_number"),)
```

### fixtures

Source: [pickem_flask_htmx_tabs.py, Fixture](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L794)

```python
id = Column(Integer, primary_key=True)
week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
match_number = Column(Integer, nullable=False)
home = Column(String, nullable=False)
away = Column(String, nullable=False)
venue = Column(String)
external_match_id = Column(Integer, unique=True)
kickoff_utc = Column(DateTime)
api_status = Column(String)
__table_args__ = (UniqueConstraint("week_id", "match_number", name="uix_week_matchnumber"),)
```

### matchups

Source: [pickem_flask_htmx_tabs.py, Matchup](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L807)

```python
id = Column(Integer, primary_key=True)
week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
player_a_id = Column(Integer, ForeignKey("players.id"), nullable=False)
player_b_id = Column(Integer, ForeignKey("players.id"), nullable=False)
first_picker_id = Column(Integer, ForeignKey("players.id"), nullable=False)
```

### picks

Source: [pickem_flask_htmx_tabs.py, Pick](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L819)

```python
id = Column(Integer, primary_key=True)
matchup_id = Column(Integer, ForeignKey("matchups.id"), nullable=False)
player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=False)
team = Column(String, nullable=False)
created_at = Column(DateTime, default=datetime.utcnow)
__table_args__ = (UniqueConstraint("matchup_id", "fixture_id", name="uix_matchup_fixture_once"),)
```

### auto_draft_settings

Source: [pickem_flask_htmx_tabs.py, AutoDraftSetting](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L832)

```python
id = Column(Integer, primary_key=True)
matchup_id = Column(Integer, ForeignKey("matchups.id"), nullable=False)
player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
enabled = Column(Integer, nullable=False, default=0)
confirmed_at = Column(DateTime)
__table_args__ = (UniqueConstraint("matchup_id", "player_id", name="uix_auto_draft_setting"),)
```

### auto_draft_preferences

Source: [pickem_flask_htmx_tabs.py, AutoDraftPreference](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L842)

```python
id = Column(Integer, primary_key=True)
matchup_id = Column(Integer, ForeignKey("matchups.id"), nullable=False)
player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=False)
team = Column(String, nullable=False)
priority = Column(Integer, nullable=False)
__table_args__ = (UniqueConstraint("matchup_id", "player_id", "fixture_id", name="uix_auto_draft_preference"),)
```

### results

Source: [pickem_flask_htmx_tabs.py, Result](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L853)

```python
id = Column(Integer, primary_key=True)
fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=False, unique=True)
outcome = Column(String, nullable=False)
home_score = Column(Integer)
away_score = Column(Integer)
source = Column(String, nullable=False, default="manual")
updated_at = Column(DateTime, default=datetime.utcnow)
```

### api_sync_states

Source: [pickem_flask_htmx_tabs.py, ApiSyncState](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L865)

```python
id = Column(Integer, primary_key=True)
season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False, unique=True)
provider = Column(String, nullable=False, default=FOOTBALL_DATA_PROVIDER)
last_attempt_at = Column(DateTime)
last_success_at = Column(DateTime)
last_error = Column(String)
requests_remaining = Column(Integer)
reset_seconds = Column(Integer)
fixtures_imported = Column(Integer, nullable=False, default=0)
results_imported = Column(Integer, nullable=False, default=0)
pending_matches = Column(Integer, nullable=False, default=0)
unmatched_matches = Column(Integer, nullable=False, default=0)
```

### weekly_recaps

Source: [pickem_flask_htmx_tabs.py, WeeklyRecap](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L882)

```python
id = Column(Integer, primary_key=True)
week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
revision = Column(Integer, nullable=False)
status = Column(String, nullable=False, default="generating")
title = Column(String)
body_markdown = Column(Text)
context_json = Column(Text, nullable=False)
context_hash = Column(String, nullable=False)
prompt_version = Column(String, nullable=False)
model = Column(String, nullable=False)
provider_response_id = Column(String)
error_message = Column(Text)
correspondent_version = Column(String, nullable=False, default="v1")
source_count = Column(Integer, nullable=False, default=0)
external_context_hash = Column(String)
automation_key = Column(String, unique=True)
created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
completed_at = Column(DateTime)
__table_args__ = (
        UniqueConstraint("week_id", "revision", name="uix_weekly_recap_revision"),
    )
```

### correspondent_sources

Source: [pickem_flask_htmx_tabs.py, CorrespondentSource](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L908)

```python
id = Column(Integer, primary_key=True)
week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
fixture_id = Column(Integer, ForeignKey("fixtures.id"))
provider = Column(String, nullable=False)
source_type = Column(String, nullable=False)
external_id = Column(String)
canonical_url = Column(String)
author_name = Column(String)
body_text = Column(Text, nullable=False)
published_at = Column(DateTime)
submitted_by_player_id = Column(Integer, ForeignKey("players.id"))
submission_note = Column(Text)
metadata_json = Column(Text)
content_hash = Column(String, nullable=False)
status = Column(String, nullable=False, default="accepted")
created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
__table_args__ = (
        UniqueConstraint(
            "week_id", "provider", "content_hash",
            name="uix_correspondent_source_content",
        ),
    )
```

### correspondent_source_classifications

Source: [pickem_flask_htmx_tabs.py, CorrespondentSourceClassification](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L937)

```python
id = Column(Integer, primary_key=True)
source_id = Column(
        Integer,
        ForeignKey("correspondent_sources.id"),
        nullable=False,
    )
prompt_version = Column(String, nullable=False)
pass_number = Column(Integer, nullable=False)
pickem_impact = Column(String, nullable=False)
editorial_functions_json = Column(Text, nullable=False)
article_use = Column(String, nullable=False)
confidence = Column(String, nullable=False)
route = Column(String, nullable=False)
reason_codes_json = Column(Text, nullable=False)
reason = Column(Text, nullable=False)
model = Column(String, nullable=False)
provider_response_id = Column(String)
created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
__table_args__ = (
        UniqueConstraint(
            "source_id",
            "prompt_version",
            "pass_number",
            name="uix_source_classification_version_pass",
        ),
    )
```

### correspondent_classification_jobs

Source: [pickem_flask_htmx_tabs.py, CorrespondentClassificationJob](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L970)

```python
id = Column(Integer, primary_key=True)
season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False)
week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
prompt_version = Column(String, nullable=False)
pass_number = Column(Integer, nullable=False)
model = Column(String, nullable=False)
openai_batch_id = Column(String, unique=True)
input_file_id = Column(String)
output_file_id = Column(String)
error_file_id = Column(String)
status = Column(String, nullable=False, default="submitting")
total_count = Column(Integer, nullable=False, default=0)
completed_count = Column(Integer, nullable=False, default=0)
failed_count = Column(Integer, nullable=False, default=0)
error_message = Column(Text)
created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
submitted_at = Column(DateTime)
last_synced_at = Column(DateTime)
completed_at = Column(DateTime)
```

### correspondent_classification_job_items

Source: [pickem_flask_htmx_tabs.py, CorrespondentClassificationJobItem](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L1002)

```python
id = Column(Integer, primary_key=True)
job_id = Column(
        Integer,
        ForeignKey("correspondent_classification_jobs.id"),
        nullable=False,
    )
source_id = Column(
        Integer,
        ForeignKey("correspondent_sources.id"),
        nullable=False,
    )
custom_id = Column(String, nullable=False, unique=True)
active_reservation_key = Column(String, unique=True)
status = Column(String, nullable=False, default="pending")
provider_response_id = Column(String)
error_message = Column(Text)
imported_at = Column(DateTime)
__table_args__ = (
        UniqueConstraint(
            "job_id",
            "source_id",
            name="uix_classification_job_source",
        ),
    )
```

### correspondent_x_collection_states

Source: [pickem_flask_htmx_tabs.py, CorrespondentXCollectionState](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L1034)

```python
id = Column(Integer, primary_key=True)
week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False, unique=True)
status = Column(String, nullable=False, default="ready")
window_start = Column(DateTime, nullable=False)
window_end = Column(DateTime, nullable=False)
next_token = Column(String)
page_count = Column(Integer, nullable=False, default=0)
retrieved_count = Column(Integer, nullable=False, default=0)
persisted_count = Column(Integer, nullable=False, default=0)
lease_token = Column(String, unique=True)
lease_expires_at = Column(DateTime)
last_error = Column(Text)
cap_reached_at = Column(DateTime)
completed_at = Column(DateTime)
created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
```

### correspondent_x_page_receipts

Source: [pickem_flask_htmx_tabs.py, CorrespondentXPageReceipt](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L1057)

```python
id = Column(Integer, primary_key=True)
collection_id = Column(
        Integer,
        ForeignKey("correspondent_x_collection_states.id"),
        nullable=False,
    )
claim_token = Column(String, nullable=False, unique=True)
request_token = Column(String)
next_token = Column(String)
received_count = Column(Integer, nullable=False, default=0)
created_count = Column(Integer, nullable=False, default=0)
created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
```

### correspondent_email_deliveries

Source: [pickem_flask_htmx_tabs.py, CorrespondentEmailDelivery](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L1076)

```python
id = Column(Integer, primary_key=True)
recap_id = Column(Integer, ForeignKey("weekly_recaps.id"), nullable=False, unique=True)
delivery_marker = Column(String, nullable=False, unique=True)
recipients_json = Column(Text, nullable=False)
status = Column(String, nullable=False, default="pending")
claim_token = Column(String, unique=True)
claim_expires_at = Column(DateTime)
gmail_message_id = Column(String)
error_message = Column(Text)
created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
sent_at = Column(DateTime)
```

### correspondent_notifications

Source: [pickem_flask_htmx_tabs.py, CorrespondentNotification](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L1095)

```python
id = Column(Integer, primary_key=True)
week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
event_type = Column(String, nullable=False)
delivery_marker = Column(String, nullable=False, unique=True)
recipients_json = Column(Text, nullable=False)
payload_json = Column(Text, nullable=False)
status = Column(String, nullable=False, default="pending")
claim_token = Column(String, unique=True)
claim_expires_at = Column(DateTime)
gmail_message_id = Column(String)
error_message = Column(Text)
created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
sent_at = Column(DateTime)
__table_args__ = (
        UniqueConstraint("week_id", "event_type", name="uix_week_notification_event"),
    )
```

### recap_source_usages

Source: [pickem_flask_htmx_tabs.py, RecapSourceUsage](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L1119)

```python
id = Column(Integer, primary_key=True)
recap_id = Column(Integer, ForeignKey("weekly_recaps.id"), nullable=False)
source_id = Column(Integer, ForeignKey("correspondent_sources.id"), nullable=False)
__table_args__ = (
        UniqueConstraint("recap_id", "source_id", name="uix_recap_source_usage"),
    )
```

### weekly_recap_selections

Source: [pickem_flask_htmx_tabs.py, WeeklyRecapSelection](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pickem_flask_htmx_tabs.py#L1131)

```python
id = Column(Integer, primary_key=True)
week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False, unique=True)
recap_id = Column(Integer, ForeignKey("weekly_recaps.id"), nullable=False)
selected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
```

### fixture_provider_links

Source: [live_models.py, FixtureProviderLink](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/live_models.py#L7)

```python
id = Column(Integer, primary_key=True)
fixture_id = Column(Integer, ForeignKey('fixtures.id'), nullable=False)
provider = Column(String, nullable=False)
external_fixture_id = Column(String, nullable=False)
__table_args__ = (UniqueConstraint('provider', 'external_fixture_id'),
                          UniqueConstraint('fixture_id', 'provider'))
```

### live_fixture_states

Source: [live_models.py, LiveFixtureState](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/live_models.py#L16)

```python
fixture_id = Column(Integer, ForeignKey('fixtures.id'), primary_key=True)
provider = Column(String, nullable=False)
state = Column(String, nullable=False)
is_live = Column(Boolean, nullable=False, default=False)
home_score = Column(Integer)
away_score = Column(Integer)
minute = Column(Integer)
extra_minute = Column(Integer)
provider_updated_at = Column(String)
last_synced_at = Column(DateTime, nullable=False)
```

### match_events

Source: [live_models.py, MatchEvent](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/live_models.py#L29)

```python
id = Column(Integer, primary_key=True)
fixture_id = Column(Integer, ForeignKey('fixtures.id'), nullable=False, index=True)
provider = Column(String, nullable=False)
external_event_id = Column(String, nullable=False)
event_type = Column(String, nullable=False)
minute = Column(Integer)
extra_minute = Column(Integer)
team_provider_id = Column(String)
team_name = Column(String)
side = Column(String)
player_provider_id = Column(String)
player_name = Column(String)
related_player_provider_id = Column(String)
related_player_name = Column(String)
detail = Column(String)
running_score = Column(String)
sort_order = Column(Integer)
is_active = Column(Boolean, nullable=False, default=True)
provider_updated_at = Column(String)
created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
revisions = Column(JSON, nullable=False, default=list)
__table_args__ = (UniqueConstraint('fixture_id', 'provider', 'external_event_id'),)
```

### club_season_enrichment

Source: [pick_insight_enrichment.py, ClubSeasonEnrichment](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pick_insight_enrichment.py#L50)

```python
season_id = Column(Integer, ForeignKey('seasons.id'), primary_key=True)
sportmonks_team_id = Column(Integer, primary_key=True)
sportmonks_season_id = Column(Integer, nullable=False)
league_id = Column(Integer, nullable=False)
footy_club = Column(String, nullable=False)
crest_url = Column(String)
top_scorers = Column(JSON, nullable=False, default=list)
goals = Column(Integer)
synced_at = Column(DateTime, nullable=False)
__table_args__ = (UniqueConstraint('season_id', 'footy_club'),)
```

### pick_insight_refreshes

Source: [pick_insight_tasks.py, PickInsightRefresh](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/pick_insight_tasks.py#L13)

```python
week_id = Column(Integer, ForeignKey('weeks.id'), primary_key=True)
season_id = Column(Integer, ForeignKey('seasons.id'), nullable=False, index=True)
status = Column(String, nullable=False, default='pending')
created_at = Column(DateTime, nullable=False)
completed_at = Column(DateTime)
claim_token = Column(String)
lease_until = Column(DateTime)
```

### Raw SQL definitions — football_reference_schema.py

Source: [football_reference_schema.py](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/football_reference_schema.py)

```sql
CREATE TABLE IF NOT EXISTS football_reference_tasks (
    provider TEXT NOT NULL, external_season_id INTEGER NOT NULL,
    status TEXT NOT NULL, started_at TEXT, completed_at TEXT,
    claim_token TEXT, lease_until TEXT, next_attempt_after TEXT,
    last_successful_sync_at TEXT, error_code TEXT,
    PRIMARY KEY(provider, external_season_id));

CREATE TABLE IF NOT EXISTS football_reference_seasons (
        id INTEGER PRIMARY KEY, provider TEXT NOT NULL, external_season_id INTEGER NOT NULL,
        league_id INTEGER NOT NULL, name TEXT NOT NULL, finished INTEGER, is_current INTEGER,
        sync_status TEXT NOT NULL, fixture_count INTEGER NOT NULL,
        last_successful_sync_at TEXT NOT NULL,
        UNIQUE(provider, external_season_id));

CREATE TABLE IF NOT EXISTS football_reference_fixtures (
        id INTEGER PRIMARY KEY, provider TEXT NOT NULL, external_fixture_id INTEGER NOT NULL,
        reference_season_id INTEGER NOT NULL REFERENCES football_reference_seasons(id),
        league_id INTEGER NOT NULL, kickoff_utc TEXT NOT NULL,
        home_team_id INTEGER NOT NULL, home_team_name TEXT NOT NULL,
        away_team_id INTEGER NOT NULL, away_team_name TEXT NOT NULL,
        home_score INTEGER CHECK(home_score >= 0), away_score INTEGER CHECK(away_score >= 0),
        state_id INTEGER NOT NULL, state TEXT NOT NULL, provider_updated_at TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(provider, external_fixture_id));

CREATE TABLE IF NOT EXISTS football_reference_events (
        id INTEGER PRIMARY KEY,
        reference_fixture_id INTEGER NOT NULL REFERENCES football_reference_fixtures(id),
        provider TEXT NOT NULL, external_event_id INTEGER NOT NULL,
        type_id INTEGER NOT NULL, event_type TEXT NOT NULL, minute INTEGER, extra_minute INTEGER,
        team_provider_id INTEGER, team_name TEXT, player_provider_id INTEGER, player_name TEXT,
        related_player_provider_id INTEGER, related_player_name TEXT,
        sub_type_id INTEGER, detail TEXT, info TEXT, addition TEXT, running_score TEXT, sort_order INTEGER,
        rescinded INTEGER, is_active INTEGER NOT NULL, is_present INTEGER NOT NULL, provider_updated_at TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(provider, external_event_id));

CREATE INDEX IF NOT EXISTS football_reference_fixture_season
        ON football_reference_fixtures(reference_season_id, kickoff_utc);

CREATE INDEX IF NOT EXISTS football_reference_event_fixture
        ON football_reference_events(reference_fixture_id, is_active);

CREATE TABLE IF NOT EXISTS football_reference_syncs (
        id INTEGER PRIMARY KEY, reference_season_id INTEGER NOT NULL REFERENCES football_reference_seasons(id),
        status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
        counts_json TEXT NOT NULL);

```

### Raw SQL definitions — shared_brief_store.py

Source: [shared_brief_store.py](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/shared_brief_store.py)

```sql
CREATE TABLE IF NOT EXISTS brief_batches (
                provider TEXT NOT NULL,competition TEXT NOT NULL,season_year INTEGER NOT NULL,
                matchweek INTEGER NOT NULL,content_version TEXT NOT NULL,as_of TEXT NOT NULL,
                model TEXT NOT NULL,prompt_sha256 TEXT NOT NULL,
                PRIMARY KEY(provider,competition,season_year,matchweek,content_version));

CREATE TABLE IF NOT EXISTS team_briefs (
                provider TEXT NOT NULL,competition TEXT NOT NULL,season_year INTEGER NOT NULL,
                external_fixture_id INTEGER NOT NULL,team_id INTEGER NOT NULL,content_version TEXT NOT NULL,
                matchweek INTEGER NOT NULL,home TEXT NOT NULL,away TEXT NOT NULL,team TEXT NOT NULL,kickoff TEXT NOT NULL,
                context_json TEXT,context_sha256 TEXT,
                status TEXT NOT NULL CHECK(status IN ('pending','blocked','running','failed','uncertain','succeeded','expired')),
                attempts INTEGER NOT NULL DEFAULT 0,claim_token TEXT,lease_until TEXT,error_code TEXT,
                generated_json TEXT,completed_at TEXT,
                PRIMARY KEY(provider,competition,season_year,external_fixture_id,team_id,content_version));

```

### Raw SQL definitions — shared_brief_operations.py

Source: [shared_brief_operations.py](https://github.com/footypickem-loyola/Footy_Pickem/blob/adf2117aa7630cad9a044769525a88a05dd92575/shared_brief_operations.py)

```sql
CREATE TABLE scheduler(id INTEGER PRIMARY KEY CHECK(id=1),token TEXT,lease_until TEXT,heartbeat TEXT,report TEXT);

```

## Appendix B — Audit limitations and validation performed

Read the pinned repository source without importing the Flask app, running workers, or opening a production database. Cross-checked the ORM inventory against all `__tablename__` declarations and the static raw SQL CREATE TABLE declarations in the repository. Reviewed request identity, season selection, game mutations, aggregates, result/live ingestion, enrichment, reference data, shared briefs, Correspondent task/delivery contracts and checked-in n8n workflows. No AGENTS.md was present in the retrieved repository. Stage 1 migration/startup tests use synthetic databases; results are recorded in docs/PRIVATE_BETA_STAGE1.md and the PR. No production integrity checks have been run. The 40 full multi-league isolation cases above remain acceptance criteria for later implementation; Stage 1 does not enable multi-league behavior.
