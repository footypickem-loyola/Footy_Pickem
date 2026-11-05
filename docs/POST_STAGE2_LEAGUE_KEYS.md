# Post-Stage-2 league keys and shared-fixture scoring

Version: `private_beta_003_league_keys`; packet contract:
`post-stage2-league-keys-v1`. Local review only; no startup migration or CLI.

## Explicit migration API

`foundation_league_keys.prepare(path, stage2_evidence=...)` reads an explicit
private local database outside repositories. The unchanged Stage 2 receipt
verifier must prove completed population, the reviewed source/manifest/plan,
original receipts and preserved history. Stage 1 alone, partial backfills,
unexpected ledgers or changed history refuse. It simulates the rebuild on a
standalone in-memory copy before producing a review packet containing schemas,
row fingerprints, prior ledger rows, exact implementation hash and Stage 2 input
bindings. Packets are private operator artifacts, not Git content.

`apply(path, packet=..., expected_packet_sha256=..., stage2_evidence=...,
checkpoint=..., authorized=True, writers_fenced=True, offline=...)` consumes the
exact separately reviewed packet. The path is explicit, local, single-link and
outside Git; its parent and checkpoint parent must pass existing private-directory
checks. A v3 offline source is confined to the local temporary directory and needs
explicit offline intent; it is never production-cutover evidence. The caller must
actually coordinate writers: the boolean is an attestation, not a service fence.

After `BEGIN IMMEDIATE`, baseline schema, full non-ledger row fingerprints, old
ledger and unchanged Stage 2 verification are checked again. A fresh exclusive
SQLite online checkpoint is created, restricted with the tested artifact ACL
helper and independently verified against the baseline. Checkpoint verification
includes the unchanged Stage 2 verifier. Nothing overwrites or automatically
removes an existing checkpoint. Lock waits are bounded to two seconds; no retries.
Execution time supervision remains an operator concern before any real-data use.

The two tables are rebuilt with every original column/value/ID and incoming FK
unchanged. Only the inline global name/code UNIQUE rules are removed. Existing
`(league_id,name_key)` and `(league_id,user_id)` indexes remain; seasons gain
`uix_league_season_code (league_id,code)`. CHECKs require owned player/user/name-key
and owned season/canonical-season fields, preventing new unscoped legacy inserts.
Every existing applicable index/trigger/view is retained verbatim. Unsupported
layouts, dependencies on removed autoindexes or FKs referencing the removed global
keys refuse. Dependent views and trigger programs are compiled without execution;
foreign-key metadata, integrity, constraints and all row fingerprints are checked.

SQLite FK enforcement is disabled only on this explicit migration connection,
before the transaction, to prevent DROP from affecting dependent rows. The writer
lock and legacy-alter setting keep external FK declarations stable. Every FK is
checked before commit; enforcement is restored in `finally`. Copying precedes
trigger reinstallation, so rebuilds do not replay business triggers. Failures at
either table drop roll back the entire transaction; the checkpoint is retained.

The new ledger row's checksum is the approved packet SHA-256; that packet binds the
implementation hash. Stage 1/2 rows and their timestamps/checksums are untouched.
`verify_applied(db,packet,hash)` verifies exact post-schema, the new receipt and old
ledger, plus FK/integrity/dependency checks. Exact reruns perform no writes, including
after valid application data changes. They still revalidate the original supplied
manifest/plan bytes and private offline owner evidence. Verification of completed
migration is not a claim that later gameplay data equals the pre-migration snapshot.
Old Stage 1/2 verifiers intentionally remain strict about the old schema/history;
use this new verifier for version 003. Do not rerun Stage 2 over version 003.

A rollback rehearsal restores only the new standalone checkpoint to another
exclusive disposable file and revalidates Stage 2 there. No automatic live restore
or downgrade is supplied: after application writes, restoring a checkpoint loses
those writes and needs a separate reconciled rollback plan and owner approval.

## Real runtime changes

The existing ORM no longer declares global name/code uniqueness. Foundation
columns stay outside legacy ORM metadata so an unmigrated disabled app still loads;
scoped DB uniqueness is installed explicitly by version 003, not at startup.

Legacy name/code and active-season lookups explicitly select Loyola when ownership
is present. Stage 1 all-unowned and unmigrated cases retain old behavior. Missing
Loyola ownership fails closed instead of selecting another league. Legacy seeders
refuse owned databases before writes or provider fetches. They cannot assign a
second league by reusing global display-name lookup or deactivate its season.
No automatic account association or new league creation is added.

Weekly points and correct/incorrect/draw counts join canonical results to the
selected week's picks. League-owned zero-score members remain present, preserving
Loyola's prior output. For/against, finalized season totals, standings, head-to-head
money and payouts consume those corrected helpers; the $5-per-point rule is
unchanged. Club records use the selected player's finalized-season pick fixtures.
Related result counts, weekly score/result presentation and recap fixture reads
use `league_week_fixtures` for owned canonical seasons, with the original fixture
week fallback only for pre-canonical legacy schema/data.

The shared fixture retains its original `fixtures.week_id` and original ID. Both
leagues assign it through their local slate and keep separate matchups/picks. A
single canonical result is reused; no duplicate result is created.

Legacy globally scoped routes remain Loyola-only in verified mode. Legacy open
schedule/pick-insight layouts and deletion/import operations still have original
fixture-anchor assumptions and are not League B entry points. Background sync,
Correspondent delivery, administration and league creation are not enabled or
completed by this change. New scoped standings UI is a separate runtime slice.

## Focused synthetic verification

New tests build an actually completed Stage 2 synthetic database before version
003. They cover duplicate cross-league names/codes, within-league duplicate and
unowned-row refusal, independent guarded drafts on one canonical fixture, one
shared result, points/counts/for-against/standings/payouts, unchanged Loyola scores
and historical picks/results, disabled legacy key resolution/HTTP behavior,
custom index/trigger/view preservation, dependent CASCADE FK rows, unsupported
referenced global keys, missing Stage 2 receipt, stale evidence/schema/data,
atomic rollback, independent checkpoint restoration and idempotency.

```powershell
python -B -m unittest discover -s tests -p test_foundation_league_keys.py
python -B -m unittest discover -s tests -p test_runtime_league_keys.py
```

The focused runtime auth/boundary/draft/navigation/Matchweek modules are also
checked. All databases and accounts are synthetic, transport is mocked/blocked,
and no production snapshot is opened. The original Task 30 report/test remain
untouched in the runtime worktree. This work is preserved separately on
`codex/post-stage2-league-keys`; PRs #39/#40 are unchanged.

## Task 32 focused review corrections

1. Name/code scoping alone did not fence direct-ID legacy handlers when the Auth0
   feature was off. An install-time listener now performs no database work until
   a protected request, then takes a read snapshot and refuses all non-static
   legacy dispatch if any player/season belongs to a non-Loyola league. It also
   covers machine handlers, which cannot safely use fallback global behavior in
   that state. Setup/archived second-league data is protected too. Single-league
   migrated Loyola and unmigrated legacy fallback remain usable. Verified mode
   retains its existing membership/route boundary. Consequently verified-account
   mode is a prerequisite before introducing second-league owned data; disabling
   it afterward fails closed, not back to a global room-code experience.
2. Migration authorization, writer-fence and required offline-intent flags now
   require literal `True`, refusing truthy strings or other accidental values.
3. The old disabled-HTTP-pick test used an already multi-league fixture. It now
   independently seeds a single-league legacy database to verify the original
   successful pick behavior. New coexistence tests separately prove non-Loyola
   direct IDs/admin/room-code routes refuse, and a single-league version-003
   database still serves legacy Matchweek/join. No display name links an account.

The original six-file implementation plus that one existing focused test correction
are the complete seven-file review scope. No unrelated refactor is included.

## Required separately approved real-data offline rehearsal

No private historical snapshot has been opened for this review. Before any real
cutover, obtain owner authorization for these steps, using qualified acquisition,
storage, ACL, watchdog and transfer mechanisms already reviewed separately:

1. Select/acquire and independently verify a suitable consistent game checkpoint;
   retain it unchanged in approved encrypted private storage. Do not reuse stale
   source fingerprints or mapping approval if the data has changed.
2. Restore/verify into a fresh private disposable execution target. Verify exact
   source hashes, SQLite integrity/FKs and reviewed mapping/no-send policy evidence.
3. Run unchanged guarded Stage 2 on that disposable target with a completion receipt.
   Preserve X-claim gates and original checkpoint/restore. Migration 003 requires
   the exact completed Stage 2 state, so it precedes identity activation or later
   gameplay changes in this rehearsal sequence.
4. Prepare and privately review the 003 packet, then separately approve applying it
   to that execution target only. Independently verify its exclusive pre-003
   checkpoint. Use explicit offline intent/private temp paths and no providers.
5. Compare all historical IDs/values/fingerprints, SQLite FKs/integrity, indexes,
   triggers/views, both original receipts and new receipt, original Loyola points,
   records, standings and payouts. Verify idempotency, stale/partial refusal and a
   separately restored checkpoint returning to the verified Stage 2 state.
6. On another disposable derivative, exercise synthetic second-league duplicates,
   shared fixture/results, scoped writes and verified-mode access. Never add fake
   identities or recipients to the retained production checkpoint. Stop on any
   mismatch; do not relax a guard to finish the rehearsal.

Production additionally requires writer coordination and X-claim fencing, provisioned
private persistent auth storage, reviewed Auth0/HTTPS/health settings, authoritative
owner-approved account links, and completed scoped background/admin integration.
No league creation, invites, notifications or cutover are enabled by this PR.
A rollback after live writes needs reconciliation: restoring pre-cutover data would
otherwise lose those writes. No automatic production restore or downgrade exists.

Review validation: 87/87 focused tests passed (7 migration, 4 coexistence,
16 auth, 13 boundary, 16 drafting, 12 navigation, 10 Matchweek, 9 pick UI).
Tracked and new-file whitespace checks passed. No broad application suite or
private-data rehearsal was run.
