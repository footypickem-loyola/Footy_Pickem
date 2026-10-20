# Stage 2 production runbook — DRAFT, not execution authorization

This is a review document. Slice 5C is local/offline only. No production access,
writer fencing, checkpoint creation, deployment, mutation, or writer resume is
authorized by this document. Each requires explicit authorization in the live task.

The execution API is foundation_backfill_execute.py, an explicit maintenance
module with no CLI or application/startup hook. The original read-only CLI,
synthetic writer, rehearsal capability, Stage 1 DDL/checksum, and runtime remain
unchanged. Production use requires reviewing and making the exact maintenance
code available on the verified service through a separately authorized procedure.

## Source freshness and historical evidence

The October 18 standalone snapshot has SHA-256:
03052025426e467ac2138be0c23c9517f36c60b2b7b377ce3f3f806426c16722.

Only that source supports the historical 998 inserts / 2,425 legacy-row updates,
manifest checksum 1a0ce87db124dfbc10f420558a6588334dace7ced70ef4deb5df74a24054c989,
and canonical plan SHA-256 bde2cd3312f45776fdbfcb7d99f3b97cd1553440fdd96d34024b83bd1eaa4e31.
These are rehearsal reference values, never production defaults.

A later production source needs fresh fingerprints, fresh evidence coverage,
fresh manifest and deterministic plan, an explicit mapping/policy comparison,
and new human approval. Do not rewrite the source to fit old evidence. Apply
never generates, repairs, substitutes or modifies a plan. Drift means STOP and
return to planning; never silently refresh evidence inside apply.

Reviewed policy remains League 1, Loyola Pick 'Em (canonical reviewed Unicode
name in the private manifest), slug loyola-pick-em, status setup, America/New_York,
correspondent disabled. Freeze new-row timestamps in the fresh reviewed manifest.
Receipt applied_at is different: it is generated once in UTC per actual invocation,
and preserved on exact rerun. Never copy the historical rehearsal receipt timestamp.
Do not infer User claims, email association, permissions, or admin roles.

## Phase A — read-only / fresh planning

### Prerequisites requiring separate operational authorization

Establish current project/environment/service/deployment/volume ownership first.
Historical project c3fafbce-9787-4dff-9fe3-f6f56370fc81, environment
6191cc5e-9c57-4fdd-aff6-ba3ee78291d7, and service
9af00557-a6dd-4b05-8e24-fb3a02609079 are references to verify, not permission
or sufficient evidence of today's deployment. Verify the explicit actual database
path and maintenance-code hashes; do not rely on cwd or DB_PATH defaults.

Before checkpointing, obtain explicit permission to fence every writer and record
their original states and the fencing time. Phase A contains no production
mutation command. Writer-control actions are a separately approved operational
prerequisite, not something the maintenance adapter performs.

Inventory must cover all replicas and all known write entry points:
- Web/player/admin requests, picks, drafts/autodrafts, week/season setup and
  finalization, result imports/edits, and request-triggered writes.
- Scheduled score sync, trigger_score_sync.py, scheduled pick-insight and football
  reference synchronization, trigger_football_reference_sync.py and manual scripts.
- live_sync_worker.py and live-ingest HTTP entry points.
- Correspondent/n8n intake and batch ingestion, X claims/checkpoints/cap alerts,
  classification submit/reconcile, recap generation, delivery claims and acks.
- shared_brief_worker.py, its supervisor/restart policy, scheduler operations,
  shared content generation and any manual maintenance processes.
- Other discovered services/replicas/jobs with this volume, API credentials or a
  route to these write endpoints.

Disabling one flag or public ingress alone is insufficient. Drain in-flight work,
disable automatic restarts/retries where needed, and positively verify that no
writer can resume during review or execution. Do not cancel uncertain provider
work or clear its local state merely to obtain quiescence.

### Unique checkpoint and independent restore verification

With fencing positively established, create a unique private checkpoint using
the reviewed foundation_migrations.backup_database(source, destination) / SQLite
backup API. This happens BEFORE BEGIN IMMEDIATE, never from within the backfill
transaction. Use an explicit existing source, an exclusive new destination and
private storage outside Git; never raw-copy a live main DB while ignoring WAL.

Independently reopen the completed checkpoint and require:
- integrity_check exactly ok; foreign_key_check empty;
- valid Stage 1-only history and schema;
- exact current source/schema/data/Stage 1-row fingerprints;
- recorded size, SHA-256, UTC creation/verification times and provenance;
- no WAL, SHM or rollback-journal sidecars for the transferable standalone artifact.

Create a separately named restore-test copy of the completed standalone checkpoint,
reopen it independently, and verify the same logical fingerprints, Stage 1 row,
integrity, FKs and scoring. Record its independent file hash. Never restore onto
the live database as a test. Keep checkpoint and restore artifacts immutable.
No raw copy of the live database is allowed. The production adapter creates
neither checkpoint nor restore artifact and does not start a writer hold.

The original offline source uses immutable=1 only because it is already a verified
standalone immutable artifact. Live target connections always use WAL-aware
mode=ro or mode=rw, never immutable=1 or nolock=1.

### Fresh mapping, deterministic planning, and evidence packet

Privately read the fresh checkpoint, inventory all rows and resolve mapping
coverage using reviewed evidence. Re-read current recipient configuration without
printing addresses; revalidate separate stores and task state. Preserve all
historical values, provider strings and receipt/state rows.

Produce the private manifest with its source fingerprint, reviewed mappings,
coverage and permissions. Generate the private plan with the existing planner.
Compare against historical approvals by stable legacy identity: additions,
deletions, changed source values, season/week/club evidence, roster and fixture
slates, recipients/User associations, task disposition, and counts. Any unresolved
mapping or policy change requires explicit review. Do not invent provider IDs.

Use canonical UTF-8 plan bytes and plan_sha256(plan); do not substitute the
different plan_summary digest. Record Stage 1 row identity as digest of the
five-column SELECT rows ordered by the one approved Stage 1 version. Record
actual schema hash and complete trigger-inventory hash.

Call prepare_execution with these explicit keyword arguments:
- database_path: exact intended existing execution target;
- manifest and plan: private parsed documents;
- expected_manifest_checksum and expected_plan_sha256;
- expected_source: schema_sha256/data_sha256 pair from the fresh source;
- expected_stage1_ledger_sha256 and expected_schema_sha256;
- checkpoint: path, sha256, restore_path, restore_sha256,
  independently_verified=true.

Preparation reads checkpoint and restore independently, verifies target baseline,
rebuilds the deterministic plan solely to compare it byte-exactly, and returns a
packet containing the contract, target path/device/inode identity, source,
manifest/plan checksums, Stage 1 row/schema/trigger identities, scoring evidence
and checkpoint identities. It writes nothing. A partial/completed target cannot
be used to prepare a new baseline packet. A completed target is handled with
the original approved packet in recovery verification.

Save the packet privately and calculate packet_sha256(packet). Review it together
with the private mapping changes, counts, code revision, target and operational
evidence. The hash is a binding, not a signature or an independent proof that a
human reviewed it. Operator authorization remains essential.

## STOP — HUMAN APPROVAL REQUIRED

Do not invoke execute_plan until Steve explicitly approves the exact fresh target,
source, manifest checksum, canonical plan SHA, packet SHA, proposed counts and
reviewed mappings/policies, checkpoint evidence, operational gate results, code
revision, and the separately scoped production mutation.

Keep writers fenced throughout this boundary. If source, code, target identity,
checkpoint, plan, permission, or operational evidence changes, STOP. Reinspect,
replan where needed, and obtain renewed approval. Do not reuse October 18 counts
or approval for a later source.

## Phase B — separately authorized production mutation

Recheck all gates immediately before invocation. Produce a private
operational_evidence object with exactly:
- mode: production_attestation (offline_simulation is rehearsal evidence only);
- approved_packet_sha256;
- observed_at: fresh UTC ISO timestamp, no future time;
- gates: every exact code below, each containing passed=true and
  evidence_sha256 identifying its independently reviewed private evidence.

The adapter limits gate-observation age to 15 minutes and rechecks it before and
under the write reservation. Refresh the actual checks if they expire; do not
merely renew the timestamp. Hashes/booleans attest operational observations;
software cannot independently prove a Railway/n8n fence or recipient permission.
An offline result never satisfies a production gate.

The only mutation invocation is the explicit API below, in a separately reviewed
private operator wrapper. It has no implicit database, inputs or automatic retry:

~~~python
from foundation_backfill_execute import execute_plan, CONFIRMATION

result = execute_plan(
    database_path=reviewed_target,
    manifest=reviewed_manifest,
    plan=reviewed_plan,
    expected_manifest_checksum=approved_manifest_checksum,
    expected_plan_sha256=approved_canonical_plan_sha256,
    expected_source=approved_source_fingerprints,
    expected_stage1_ledger_sha256=approved_stage1_row_sha256,
    expected_schema_sha256=approved_schema_sha256,
    checkpoint=independently_verified_checkpoint,
    approved_packet=reviewed_packet,
    approved_packet_sha256=human_approved_packet_sha256,
    confirmation=CONFIRMATION,
    writers_fenced=True,
    operational_evidence=fresh_reviewed_production_evidence,
)
~~~

Values above are explicit reviewed private inputs, not environment lookups or
historical constants. Launch only once under the separately approved operator
context. Capture safe result metadata; never log private inputs or raw exceptions.

The adapter revalidates manifest/plan/source identities, checkpoint and restore,
approval and gates before the write connection. It then acquires BEGIN IMMEDIATE
with a two-second lock wait and a bounded 30-second transaction. Under reservation
it repeats target identity, lifecycle, Stage 1 row, schema/data, trigger and scoring
checks. It never replans or chooses substitute IDs. Exact approved writes precede
full post-state/preservation/scoring/schema validation, receipt insertion and full
receipt/population verification. One COMMIT follows. Any precommit failure rolls
back all population and the receipt; there is no checkpoint operation or writer
control inside the transaction.

Independently reopen the result read-only and verify completed state using
foundation_backfill_receipt.verify_state(..., require_completed=True), supplying
the approved manifest/plan and their exact hashes. The explicit
foundation_migrations.verified_applied(..., stage2_evidence=...) path accepts that
state; the default Stage 1-only verifier intentionally rejects a second row.
Require integrity/FKs/schema/preservation/scoring equality and the exact receipt.
An exact idempotency check uses the original packet/checkpoint/evidence binding
and fresh operational observations; it verifies state then rolls back without
data or receipt writes. It must preserve applied_at.

## Mandatory operational gates: pass / fail

- WRITER_FENCING_AND_REVALIDATION_REQUIRED: PASS only with current complete writer
  inventory, every writer/replica/ingress/scheduler held, in-flight work drained,
  restart policy controlled and observation maintained through commit/verification.
  Unknown writer, new request or unproven hold means FAIL.
- X_CLAIM_REVALIDATION_AND_FENCING_REQUIRED: PASS only after fresh private inspection
  of each historical unresolved claim (including IDs 2–4 if still present), no
  live holder/retry or uncertain outstanding provider operation, and an explicitly
  approved preserve-only disposition. Expired lease alone is not proof. Claims
  are not cleared, repaired or rewritten. Uncertain outcome means FAIL.
- CURRENT_RECIPIENT_CONFIGURATION_REVALIDATION_REQUIRED: PASS only if current recap
  and admin-alert configuration has been privately re-read, normalized and compared
  to the freshly approved manifest, with explicit permission and any User association
  evidence. Print counts/digests only, never addresses. Any difference or unknown
  permission/association requiring a decision means FAIL.
- SHARED_BRIEF_AND_SCHEDULER_REVALIDATION_REQUIRED: PASS only after fresh read-only
  checks of the actual distinct shared_briefs.db/shared_brief_scheduler.db stores
  (or verified configured paths), preserved content/state fingerprints, no live
  claims/leases/active or uncertain work, and held workers/supervisor. Missing,
  different or uninspected store or uncertain task means FAIL.
- SOURCE_SCHEMA_DATA_STAGE1_LEDGER_REVALIDATION_REQUIRED: PASS only with exact fresh
  source fingerprints, unchanged reviewed Stage 1 row/checksum/schema, exact
  checkpoint/restore agreement, approved mappings and same reserved target.
  Unrecognized receipt/history, drift or partial population means FAIL.
- APPROVED_INDEPENDENT_CHECKPOINT_REQUIRED: PASS only with a unique checkpoint made
  after fencing, verified independently and through a separate restore copy, matching
  the approved source; hashes, sizes, provenance and UTC times recorded privately.
  No checkpoint, older unapproved provenance, nonstandalone artifact or failed
  restore means FAIL. Merely setting independently_verified is insufficient.
- SCORING_AND_PRESERVATION_EQUALITY_REQUIRED: PASS pre-execution requires reviewed
  baseline scoring from the actual legacy functions and full protected fingerprints;
  PASS for writer resume additionally requires exact after-state equality, including
  Results/picks, historical IDs/strings, delivery/provider receipts, claims and
  Stage 1 row. Any mismatch means FAIL and rollback/refusal.
- TRIGGER_EFFECT_REVIEW_REQUIRED: PASS only after fresh full schema/trigger inventory,
  explicit effect review and matching approved trigger digest under reservation.
  Any unreviewed trigger or unexpected write count/side effect means FAIL. Do not
  drop, disable or rewrite triggers to force success.

Each observation is bound to the approved packet. All PASS attestations are required;
successful offline tests do not clear these gates.

## Failure, recovery and writer resume

Lock timeout, input refusal or known precommit failure: keep writers held; verify
the target independently. Expected rollback state is exact original Stage 1-only
fingerprints, empty foundation, null additions, and no Stage 2 receipt. Preserve
all evidence; correct planning/evidence only under a new approved scope.

SSH loss, timeout, process death, COMMIT error or lost result is ambiguous. Never
automatically retry, rerun a writer, restore or resume services. First establish
target state using explicit read-only evidence-bound verification:
- Exact completed population plus valid receipt: commit succeeded; preserve the
  original applied_at and obtain approval for any idempotency/resume action.
- Exact Stage 1-only state: no committed backfill; a new attempt requires confirmed
  operational gates and explicit authorization.
- Anything else: STOP, preserve forensic evidence, keep writers fenced, escalate.

Do not use default Stage 1-only verification to misclassify a valid completed
Stage 2 database as corruption. Do not automatically delete the second receipt.

Never restore a checkpoint after resumed writes or external deliveries: doing so
could discard new work or repeat effects. Any restore requires explicit separate
approval, full writer fencing, a current forensic copy and an impact review.
No automatic down migration or row cleanup exists.

Resume only after independently verified target state, all postconditions and
gates pass, explicit operator approval, and a recorded exact restoration of the
original writer/service states. Resume sequencing and observation must include
web ingress, workers/supervisors, schedules and n8n. Never turn on previously
disabled components. No app/runtime rollout or multi-league routing is part of
Stage 2. Retain checkpoints and private evidence until separately authorized
cleanup; never place them in Git, chat, shared storage or public logs.

## Remaining production preparation

Fresh current topology/writer inventory, exact fencing/resume commands and
in-flight dispositions cannot be established locally. Collect and approve them
in a separately authorized production read-only/operations task before execution.
The operator wrapper, private file permissions, checkpoint destination/capacity,
reviewed code availability and approval record must be reviewed for the actual
host. This draft provides no authority to deploy those files or execute mutation.
