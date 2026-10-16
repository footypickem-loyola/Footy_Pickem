# Private Beta Stage 2 handoff

Workspace: `C:\Users\sbayd\Documents\Footy_Pickem_Stage2`  
Branch: `beta/stage2-backfill`  
Baseline: `a7fa1858bccb9ec6bba68ff6198db138bf4a2e08`

## Current state

The following production completion status was supplied by Steve for this handoff; it was not independently rechecked against production during this documentation-only setup.

- Stage 1 is complete in production; its code is merged into GitHub `main` at the baseline above.
- The Stage 1 production migration completed successfully. Production post-state: **49 tables / 80 indexes**.
- Checkpoint, independent restore verification, private off-host checksum, integrity/FK validation, legacy preservation, idempotency, and guarded application smoke all passed.
- Application writers were resumed successfully and public ingress returned HTTP 200.
- No Stage 2 backfill has been run.
- No League #2 has been created or activated.

The existing [foundation review](PRIVATE_BETA_FOUNDATION_REVIEW.md) and [Stage 1 documentation](PRIVATE_BETA_STAGE1.md) retain their earlier preparation-time status. Use them for the reviewed architecture, preservation contract, schema, migration tooling, and test evidence; this handoff records the subsequent completion status supplied above.

## Stage 2 objective

Deterministically populate/backfill the empty Stage 1 foundation, beginning and proving the process on an isolated copy before any production consideration.

Approved Stage 2 model:

1. Create/verify `leagues.id = 1` as Loyola Pick ’Em; abort on conflicting identity.
2. Map each existing Player/Membership to `league_id=1` and a newly created unclaimed User, preserving every existing Player ID and historical FK.
3. Produce a deterministic Player→User manifest so reruns reuse the same mapping.
4. Map existing League Seasons to canonical Football Seasons using actual fixture/provider evidence; do not infer mappings merely from names or years.
5. Create canonical Football Matchweeks and populate `weeks.football_matchweek_id`, preserving Week IDs and numbers.
6. Create Clubs, provider mappings, and reviewed aliases; populate canonical fixture season/round/home/away club references without changing fixture IDs or historical fixture strings.
7. Populate League #1 fixture slates in `league_week_fixtures` using original fixture IDs and ordering.
8. Populate season-roster participation from actual historical participation.
9. Populate `selected_club_id` only when an existing historical pick/team string resolves exactly and unambiguously to a fixture participant; report ambiguity instead of guessing.
10. Reconcile sync/cache/task state to shared football identities without losing completed work or provider acknowledgements.
11. Verify historical scoring before/after against the same canonical Results.
12. Preserve all Correspondent history, classifications, recaps, leases, caps, delivery markers, and shared brief/reference content.
13. Explicitly map League #1 notification recipients rather than inferring them from names.
14. Run full integrity/preservation comparison and prove the backfill is idempotent.

## Stage 2 boundaries

- Populate new Stage 1 tables and new nullable columns only.
- Do not rewrite historical IDs.
- Do not blanket-update old `updated_at`, status, hashes, or Result values.
- Do not fabricate missing football facts or fuzzy-match ambiguous clubs/fixtures.
- Do not fetch provider data or invoke AI merely to make the backfill succeed unless separately reviewed and explicitly authorized.
- Do not change login/routing or make the application multi-league; that belongs to Stage 3.
- Do not enforce final NOT NULL ownership or remove obsolete global uniqueness; that belongs to Stage 4.
- Do not create League #2.
- **Production execution is not authorized by this document.**

## Development approach

- Inspect the current Stage 1 schema/migration tooling first: [foundation_schema.py](../foundation_schema.py) and [foundation_migrations.py](../foundation_migrations.py). These provide explicit additive DDL, schema/checksum validation, preservation hashing, backup verification, and refusal/idempotency checks; they do not implement Stage 2.
- Design the Stage 2 backfill as deterministic, inspectable, and rerunnable.
- Build against synthetic databases and/or approved local copies only.
- Add refusal tests for ambiguous/conflicting mappings.
- Preserve before/after evidence; keep private evidence, database copies, and secrets out of GitHub.
- Treat unresolved mapping as a **STOP** condition rather than guessing.

**Authorization note:** Creation of this handoff authorized documentation setup only. Stage 2 implementation, commits, pushes, production access, Railway commands, database modification, and n8n changes each require the scope explicitly granted in the current task. This document itself does not authorize production execution.
