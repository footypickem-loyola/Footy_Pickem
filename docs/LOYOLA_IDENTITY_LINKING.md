# Controlled linking of existing Loyola Stage 2 placeholders

## Scope and trust

`loyola_identity_link.py` is a private operator tool, never a Flask endpoint or
startup hook. It imports no Auth0 client and performs no discovery, provider call,
account creation, email lookup, account merging or claiming through room codes.
This task authorizes synthetic validation only. No real identity mapping has been
created and no real account has been linked.

Stage 2 already assigns the original `players.id` rows to placeholder `users.id`
values. These relationships must be retained. The tool accepts 1–6 explicitly
approved links, while requiring exactly six memberships in the pinned Loyola
league (ID 1, slug `loyola-pick-em`). Each selected player must still point to the
specified single placeholder user, have unset role/status/left_at/recap_opt_in,
and the user must be unclaimed, non-admin and without email assertions. Selected
users must have no identity association. An issuer/subject already assigned to
any account is refused. Subsequent partial batches require a new reviewed packet.

The tool verifies file hashes and database relationships. It does NOT prove that
a human owns an Auth0 subject, authenticate an owner's signature, or manufacture
review evidence. A packet hash communicated through a trusted owner channel is
the approval anchor; an `approved: true` field is not supported.

## Authoritative evidence prerequisite

For each player, separately obtain authoritative evidence of the exact signed-in
Auth0 issuer and subject, plus the owner's explicit assignment of that identity
to the original membership/user IDs. Use an independently verified authentication
record and direct owner confirmation of the association. An Auth0 directory entry
may corroborate a subject, but a name/email search alone does not establish who
should own a membership. Room codes, picks and historical deliveries are never
account-ownership evidence.

The current login intentionally refuses unknown/unclaimed accounts. Its public
errors or logs are not a subject-discovery/claiming mechanism. If authoritative
proof cannot otherwise be obtained, a separately approved identity-verification
step is required first. No live Auth0 access is authorized here. Do not store raw
bearer, ID/access/refresh tokens, credentials or invitation links in the mapping.

## Private mapping format

Store the mapping and every referenced artifact outside all Git repositories in
an encrypted, access-restricted review directory. Each reference is an absolute
local file path and its raw SHA-256. Windows files must pass the exact operator /
SYSTEM / Administrators file-ACL check; links and multiple hard links are refused.
New packet/audit files receive restrictive permissions before content is written.
Parent-directory permissions are never changed.

The strict JSON structure is:

- `contract`: `loyola-existing-identity-links-v1`
- `issuer`: exact reviewed HTTPS root issuer, with trailing slash; no normalization
- `owner_decision`: `{path, sha256}` referencing the owner's limited linking decision
- `links`: a nonempty list, maximum six, of exactly:
  - `membership_id`, `user_id`: existing positive integer IDs, not names
  - `subject`: exact independently verified Auth0 subject
  - `identity_evidence`: `{path, sha256}` for authoritative identity evidence
  - `membership_evidence`: `{path, sha256}` for the reviewed membership assignment

No role, admin, email, verification-time or notification fields are accepted.
Do not commit this JSON or any real identity/evidence file. Do not invent an owner
approval timestamp. Packet creation and actual application timestamps are recorded
as those events only, never as authentication or owner-approval timestamps.

## Intended owner-approved procedure — not executed on real data

1. Never use a retained checkpoint, restore or prior evidence database as the
   write target. Use only a separately approved fresh execution copy or future
   explicitly approved live target. Verify the exact tested source/package and
   the intended target's Stage 2
   completion with existing receipt tooling before linking. Separately approve
   backup/restore readiness, a controlled writer hold, and any real-data access.
   The linking tool neither creates backups nor pauses/fences services. Its
   `--writers-fenced` flag is an explicit operator attestation, not proof of remote
   fencing, and does not resolve the Stage 2 X-claim or cutover gates.
2. Collect authoritative evidence and create the strict mapping privately. Review
   every membership/user/issuer/subject tuple explicitly. Verify the runtime's
   configured issuer independently. No default issuer, database or subject exists.
3. Use `prepare` against an explicit private target and hash-pinned mapping. It
   reads a consistent SQLite transaction, checks integrity/FKs/placeholders, pins
   the database file identity, full schema/data fingerprints and protected state,
   and writes a new exclusive private approval packet. It makes no DB changes.
4. The owner reviews that exact private packet, mapping and evidence, and provides
   the packet's raw SHA-256 through the trusted approval channel. Record the exact
   audit directory and invocation in the review. Do not substitute an artifact's
   self-reported checksum for independent owner approval.
5. With the separately approved maintenance conditions established, execute the
   exact packet once using the confirmation below. A fresh `BEGIN IMMEDIATE`
   transaction rechecks the entire source and all selected associations. No stale
   source or unexpected existing link is repaired, adopted, merged or overwritten.
6. Run `verify` read-only against the same packet and audit directory. Preserve
   all private approval, attempt, intent and receipt files. Review the completion
   receipt and unchanged historical/protected-state evidence independently.

Proposed invocation shape (placeholders only; trusted Python/worktree required):

```powershell
& '<TRUSTED_PYTHON>' -B '<REVIEWED_WORKTREE>\loyola_identity_link.py' prepare `
  --database '<EXPLICIT_TARGET>' --mapping '<PRIVATE_MAPPING_JSON>' `
  --mapping-sha256 '<OWNER_REVIEWED_RAW_HASH>' --issuer '<EXACT_REVIEWED_ISSUER>' `
  --packet '<NEW_PRIVATE_PACKET_JSON>'

& '<TRUSTED_PYTHON>' -B '<REVIEWED_WORKTREE>\loyola_identity_link.py' apply `
  --packet '<PRIVATE_PACKET_JSON>' --packet-sha256 '<OWNER_APPROVED_RAW_HASH>' `
  --audit-directory '<SAME_PRIVATE_AUDIT_DIRECTORY>' --writers-fenced `
  --confirmation LINK_EXACT_REVIEWED_LOYOLA_IDENTITIES

& '<TRUSTED_PYTHON>' -B '<REVIEWED_WORKTREE>\loyola_identity_link.py' verify `
  --packet '<PRIVATE_PACKET_JSON>' --packet-sha256 '<OWNER_APPROVED_RAW_HASH>' `
  --audit-directory '<SAME_PRIVATE_AUDIT_DIRECTORY>'
```

These commands are a future procedure, not authorization to run against real data.

## Exact write scope, atomicity and audit behavior

For each approved pair only:

- Insert one exact `user_identities` association, with actual application time as
  `created_at`; leave `last_authenticated_at` NULL (linking is not a login).
- Set the existing placeholder user's `status='active'` and its actual update time.
- Set the existing player's `status='active'` and ordinary `role='member'`.

Original player/user IDs and their association, legacy values, joined_at, names,
all picks/results/fixtures/matchups, league/season relationships, Stage 2 ledger,
X claims, admin flags, emails, email-verification fields, preferences and recap
opt-ins remain unchanged. Loyola Correspondent must already be disabled with zero
manual recipient rows. No email/notification eligibility is created.

The transaction uses foreign keys, integrity checks, narrow SQLite write/function
allowlists, trigger refusal, compare-and-set predicates, exact write counts and
before/after protected-state equality. Only the selected status/role/update cells
and planned new identity rows may differ. Schema must stay unchanged. SQL progress
has a 30-second deadline; private-file ACL subprocess checks are separately bounded.
There is no automatic retry, cleanup, deletion, rollback restore or service action.

A private exclusive attempt is written before dispatch; a full post-state intent
is fsynced before COMMIT; a private completion receipt follows COMMIT. SQLite
linking is atomic, but filesystem audit and SQLite commits are not one distributed
transaction. Failure or interruption is therefore an uncertain outcome requiring
independent review, not permission to retry. A partial receipt is never silently
replaced or accepted. Real execution requires filesystem/disk/restore readiness
review in addition to this synthetic qualification.

Exact successful reruns are read-only: full post-state, target identity, protected
state and every present receipt field/permission must match, then `already_applied`
is returned with zero writes. Missing receipts return
`applied_receipt_missing_review_required`, also with zero writes. Attempts without
intents, rolled-back attempts, state drift or malformed/unrelated/private-invalid
receipts refuse. Preserve them for owner review; do not change audit directories,
delete evidence or issue replacement attempts to evade the refusal. Receipt
finalization/recovery needs separate owner direction.

The original Stage 2 receipt is preserved, not rewritten. Legitimate linking
changes the original Stage 2 population; do not rerun the backfill or relax its
old exact-population verifier to hide that expected evolution. Retain the verified
Stage 2 baseline plus the separately bound linking receipt as the audit chain.

## Focused security review and validation

The independent review covered evidence/approval binding, wrong-user/conflicting
claims, privilege changes, privacy, trigger effects, atomicity and idempotency.
It found one medium audit issue: present receipts were initially checked for
existence only. The implementation now private-validates and strict-parses every
present receipt and compares all packet/mapping/source/post-state/protected/time/
count/flag bindings. A narrow follow-up review confirmed that finding resolved;
no cross-user linking or privilege-escalation path was found.

**15/15 focused tests passed** after the audit correction.

`tests/test_loyola_identity_link.py` creates actual Stage 2 backfills on fresh,
non-sensitive synthetic six-player databases only. It covers success, existing
wrong-user/membership IDs, duplicate subjects/users/memberships, preexisting
identity conflicts, elevated roles/admin refusal, stale source, evidence and packet
tampering, trigger rollback, failures before commit, post-commit audit failure,
exact idempotency and tampered/truncated/hard-linked/private-invalid receipts.
Sockets are forbidden; no live Auth0 identity or preserved rehearsal DB is used.

Run only this focused class from a trusted local environment:

```powershell
python -B -c "import sys,unittest; sys.path.insert(0,'tests'); unittest.main(module='test_loyola_identity_link',defaultTest='LinkTests')"
```

Outstanding real-use prerequisites are authoritative subject and membership-owner
evidence, privately approved mappings/packet hashes, independent Stage 2 baseline
verification, private encrypted review/audit storage, and separately approved
backup/restore and writer coordination. No real linking or feature enablement is
authorized by the implementation, this documentation or a successful synthetic test.
