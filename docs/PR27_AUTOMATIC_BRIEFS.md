# PR27 automatic shared briefs — architecture decision

Recorded before orchestration implementation. PR27 starts at merged PR26 `84c0fcb`.

## Read-only infrastructure assessment

Railway production inventory confirms one `Footy_Pickem` replica with the persistent
`footy_pickem-volume` mounted at `/data`. Its start command is Gunicorn with two
workers, eight threads and a 60-second timeout. Score Sync Cron runs every five
minutes and Football Reference Sync Cron hourly at minute 15; neither has a volume.
Live Sync Worker likewise has no volume and sends authenticated snapshots to Flask.
The cron scripts call authenticated Flask task endpoints; Flask owns database writes.
Only variable names were inspected, never secret values. No configuration changed.

## Selected design

Run one dedicated brief scheduler process alongside Gunicorn **inside the existing
Flask container**, using an opt-in supervisor start command. It opens the game SQLite
file read-only and owns `/data/shared_briefs.db`; web workers only read that content
file. `/data/shared_brief_scheduler.db` holds only operational leases/status, never
football content or game records. Existing databases are not moved or migrated.

This reuses the service's volume without assuming cron/worker services share it.
A periodic authenticated task that performs generation in an HTTP request conflicts
with the current web timeout and consumes web capacity. An unmanaged background
thread inside each Gunicorn worker complicates restarts and duplicates schedulers.
A separate Railway worker plus central Postgres is viable for future multi-service
consumers but adds infrastructure and a persistence adapter now. It is not necessary
for this one-volume deployment. Future leagues in this app share the canonical
store; independent web services will need a central content service/database.

The supervisor restarts a failed brief worker without stopping the website. Game
traffic remains available if generation fails. It propagates termination to both
children and exits when Gunicorn exits. Nothing starts on Flask import or page GET.
Activation and Railway start-command changes require approval.

## Timing and recovery policy

Poll actual official schedules every 60 seconds. Eligibility is `[first kickoff -
30 hours, first kickoff - 18 hours)`, in UTC, for ten distinct fixtures/twenty teams.
The primary readiness deadline is **24 hours before first kickoff**. Fewer than
twenty successes at that instant immediately flags a missed readiness target;
safe automatic recovery continues until the separate 18-hour hard stop.
No Saturday assumptions. Official sync must have succeeded within 15 minutes with
no reported error or unmatched fixtures. Unknown/ambiguous schedules fail closed.

At first eligible preparation take a consistent local read-only source snapshot,
then freeze the cutoff and all twenty contexts in PR26's store. Discard the temporary
snapshot after preparation. Subsequent calls use **only saved contexts**, including
blocked slots, never facts refreshed after that cutoff. Per-fixture official
schedule checks and kickoff restrictions still run against the current source.

At most one provider attempt per cycle reuses PR26's workflow. Transactional scheduler
leases prevent overlapping ticks; PR26 entry claims/fencing remain authoritative.
Known failures get at most three total attempts, at least 15 minutes apart. Uncertain
provider outcomes and expired entry leases never automatically retry. Successful
entries never regenerate. Restarts resume saved work, not the original source.

Any change to a prepared round's identities/kickoffs stops automatic work and
requires intervention/new explicitly approved content version. At the 18-hour hard stop,
incomplete batches stop automatic generation and report a missed hard window; do not
silently produce late content. Recommend operator-approved late completion only
while still pre-kickoff, or leave unavailable. Rescheduled/Friday/midweek rounds
use exactly the same arithmetic. A late successful response is reported as late.

## Status and alerting

An authenticated read-only status endpoint reports heartbeat, upcoming matchweek,
first kickoff/window, counts out of twenty, per-entry failure states, last successful
entry and intervention reasons. Durable status survives restarts. Logs contain safe
status codes, not provider exception strings or credentials. Proposed n8n monitor:
poll status every five minutes and notify operators on intervention or stale heartbeat;
configuration/notification delivery is an activation prerequisite, not executed here.

Railway documentation: [volume ownership and limits](https://docs.railway.com/volumes/reference)
and [cron overlap behavior](https://docs.railway.com/cron-jobs).

## Approved activation procedure (not executed)

1. Review/merge code separately from activation approval. Initialize the one content
   file on the Flask volume with `python scripts/generate_shared_briefs.py --store
   /data/shared_briefs.db --init-store`. This is a production content write and needs
   approval. Back up content/operations files with the existing volume backup policy.
2. Preserve the existing game `DB_PATH` (SQLite URI). Configure only the Flask service:
   `SHARED_BRIEF_STORE=/data/shared_briefs.db`,
   `SHARED_BRIEF_OPERATIONS_STORE=/data/shared_brief_scheduler.db`,
   `SHARED_BRIEF_CONTENT_VERSION=shared-auto-v1` (a new explicit version),
   `SHARED_BRIEF_AUTOMATION_ENABLED=1`, and an explicitly approved `OPENAI_MODEL`.
   Reuse its intentional `OPENAI_API_KEY` and `SYNC_SECRET`; never copy them to cron
   jobs. The worker requires distinct absolute paths in the game file's volume
   directory and an already initialized content store. It initializes only its own
   operational database on explicit enabled startup. All consumers use the same
   content version/path. The verified mapping currently supports only 2026/27.
3. With approval, set Flask's Railway start command to
   `python run_web_with_briefs.py`. Gunicorn retains its existing worker/thread/timeout
   settings. Keep the service always on; verify that sleep is disabled. No cron or
   live-worker command needs changing. No new Railway service or game migration.
4. Configure an n8n Schedule Trigger every five minutes to GET the Flask private
   endpoint `/tasks/pre-match-briefs/status`, using its secret credential facility for
   `X-Sync-Secret`. Notify the existing operator channel when the request fails,
   `automation_enabled` is false, `heartbeat_stale` is true, or
   `intervention_required` is true. Deduplicate unchanged alerts by matchweek/status;
   send recovery on resolution. This is a proposed small workflow, **not installed
   or tested in production**. Do not treat log lines alone as delivered alerts.
5. Confirm status heartbeat, upcoming round/window and readiness before the first
   eligible window. The status GET never initializes a store or generates content.
   Full and HTMX Training Ground requests continue to read five saved selections.

## Operator status and recovery

`/tasks/pre-match-briefs/status` uses existing task authentication, returns `no-store`,
and reads operational state only. It includes `upcoming_matchweek`, per-round
`first_kickoff`, `eligible_at` (-30h), `target_at` (-24h), `hard_stop_at` (-18h),
`readiness_target_missed`, `generation_allowed`, `blocking_reasons`, `successful`, `total`, `counts`, failed/
blocked/running/uncertain entry identities, attempt counts, `last_successful_generation`,
`intervention_required`, heartbeat and heartbeat staleness. It reports worker checks,
not a live database freshness query on each GET. An absent/unreadable status store
is an intervention, not false health. Tracked past rounds remain visible for missed
windows; unprepared old rounds are not retroactively scheduled.

- `generating_before_target`: within the -30h/-24h preparation window.
- `ready_on_time`: all twenty completed at or before -24h; remains on-time later.
- `readiness_target_missed_recovering`: incomplete at/after -24h and before -18h;
  intervention is flagged immediately. Safe work proceeds only when
  `generation_allowed` is true. Stale official data/unconfirmed schedules remain
  blockers, listed in `blocking_reasons`; uncertain outcomes never auto-retry.
- `completed_late`: all twenty completed after -24h; the missed target stays visible.
- `hard_window_missed`: fewer than twenty ready at/after -18h; no new automatic calls.

Waiting, changed schedules, invalid sources and blocked/uncertain work retain their
fail-closed handling. Schedule errors may take status priority, but the separate
readiness flag still reports a missed target once the first kickoff is known.

Normal attempts take one entry per 60-second cycle plus provider time. Twenty
successful entries normally require roughly 20–50 minutes (90-second provider
request timeout), within the six-hour pre-target window; this is a capacity estimate,
not a completion guarantee. There is another six-hour recovery window after -24h.
No request starts at/after the 18-hour hard stop. If an already in-flight response
crosses the hard stop but remains before kickoff and within its lease, existing PR26 persistence can save
it; status explicitly reports a late completion or a missed hard window, never on-time
success. Per-fixture kickoff and claim-token publication checks remain unchanged.

- Known rejected output retries after at least 15 minutes, up to three total
  attempts. Other pending entries can proceed while it cools down.
- Timeout/crash/lost response yields `uncertain` after the existing ten-minute entry
  lease. No automatic retry. Inspect provider/content state before explicitly
  approving a retry; remotely completed calls cannot be made exactly-once across
  lost responses. Scheduler ownership itself uses a five-minute fenced lease.
- Blocked context stays frozen. New reference imports cannot silently add facts.
  Refreshing context requires an approved new content version and another valid
  pre-kickoff preparation; this is never an automated repair pass.
- A changed prepared schedule stops the batch and alerts. Already successful rows
  remain immutable. The read adapter hides entries whose fixture metadata changed.
  Do not change existing saved kickoff values to make old content appear current.
- Missed -24h readiness target: alert while safe recovery continues until -18h.
- Missed -18h hard stop: default is stop and alert. Recommended exception policy
  is explicit operator approval for late pre-kickoff completion, otherwise leave
  unavailable. Do not weaken kickoff or result-update cutoffs.
- Worker configuration/start failure does not stop Gunicorn. The supervisor retries
  the worker after 60 seconds; stale-heartbeat monitoring detects a persistent failure.
  SIGTERM/exit cleanup terminates children; killed in-flight calls remain uncertain.

## Controlled twenty-perspective evaluation

The authorized October 7 evaluation is complete: **20 calls, 16 persisted,
4 validation failures; 45 supported and 2 unsupported sentences**. See
[the complete evaluation](PR27_LIVE_EVALUATION.md). The subsequent
[targeted offline correction report](PR27_TARGETED_CORRECTIONS.md) records the fixes
and unchanged-output replay: 18 accepted, two false outputs rejected, no new valid
sentence rejections. The authorized [second live evaluation](PR27_SECOND_LIVE_EVALUATION.md)
then made 20 fresh attempts: 18 persisted, two supported outputs rejected by W-D-L
subject parsing, and one accepted sentence added an unsupplied stadium name.
The subsequent [final narrow corrections](PR27_FINAL_CORRECTIONS.md) resolve those
observed cases offline: 19 second-run outputs pass and Liverpool's unsupported
venue is rejected; all 42 supported sentences pass. **Proceed to merge review
without a third paid run**; keep PR27 draft and production activation unapproved.
No output was edited or
regenerated to hide failures; all raw outputs remain local. For a separately
authorized future evaluation, use a fresh **nonproduction** copy of the
authoritative source and a separate local content/operational store. Keep the source
unchanged and choose an actual upcoming ten-fixture matchweek whose kickoff is known.
Never rewrite timestamps or the clock to make production appear eligible.

1. Prepare offline via PR26's CLI (`--source <nonproduction-copy> --store
   <local-content.db> --season-year 2026 --matchweek <N> --content-version
   pr27-eval-1 --prepare`, after `--init-store`). Inspect all twenty contexts and
   report blocked cases; do not fabricate them. This command makes no OpenAI call.
2. **After explicit paid-generation approval**, use the same source/store/model/
   content version with `--generate`. Make one pass, no `--retry-failed` or
   `--retry-uncertain`, and no AI repair. Preserve the local SQLite content file:
   it contains exact structured output, sentence fact IDs, context/hash, prompt
   hash/version, model and response ID for every successful entry. Inspect failed/
   uncertain entries without selective regeneration. Never commit raw artifacts.
3. Review all twenty perspectives clause by clause against each entry's frozen
   cited facts (SUPPORTED/OVERSTATED/UNSUPPORTED/AMBIGUOUS), including named scorer
   club attribution, literal stoppage-time minutes, bounded recency and no internal
   vocabulary. Review concision, variety, interesting facts and thin-context restraint.
4. Re-run with a provider stub that raises if called, or verify stored response IDs
   and attempt counters are unchanged. All twenty successes must be reusable with
   zero new OpenAI calls. The offline regression suite already exercises this path.
5. Point a **local** Flask process at the nonproduction game copy and that content
   file/version, with automation disabled. Check two users' five-entry Training
   Ground views and HTMX refreshes. Verify the correct fixture/team lookup, same
   shared content across local league IDs, and no new attempt/response IDs on GET.
6. Separately exercise the automated worker in an actual eligible window against
   the nonproduction copy with fresh official sync metadata. Check status/heartbeat,
   deadline alerting, process restart recovery and external monitor notification.
   A stale snapshot should report stale data; do not edit freshness timestamps to
   claim production readiness.

## Remaining activation blockers

Approval is required for additional paid evaluation, production content initialization,
enabled worker/start command, deployment and monitor configuration. The October 7
read-only snapshot had ten mapped fixtures, fresh official sync and twenty usable
contexts, but the live quality review found material blockers. The targeted fixes
pass offline replay. The second live run's two further validator false positives
and ungrounded venue addition are now covered by final deterministic corrections
and exact-output replay. Review those changes before merge; another paid evaluation
is not required for the observed cases.
Monitor delivery, volume capacity/backup policy, always-on behavior and an
actual in-window production-equivalent run remain unverified.
The reference cron's fresh data is frozen at preparation; generation does not import
or scrape anything. Future multi-service league deployments need a shared content
service/central transactional database rather than independent SQLite copies.

## Verification for this draft

Final narrow pass: **490 Python tests / 10 JavaScript tests passed**. Exact second-run
replay accepts 19 briefs and all 42 supported sentences; it rejects Liverpool and
the one unsupported sentence. No new supported-prose rejections. First-run replay
also retains its expected 45-supported/two-false result. See the
[final correction report](PR27_FINAL_CORRECTIONS.md).

Earlier targeted pass: **480 Python tests / 10 JavaScript tests passed**. The
[correction report](PR27_TARGETED_CORRECTIONS.md) records the 30h/24h/18h boundaries,
exact false-acceptance regressions, actor fixes and offline replay of all twenty
unchanged outputs. All 45 supported sentences pass; both false sentences fail.
The same full suites passed again after the second live run. Its independent
review classified **42/43 sentences supported and one unsupported**; the second
report records all outputs, hashes, response IDs and local operational checks.

Original implementation: **463 Python tests and 10 JavaScript tests passed.** Commands:
`python -m unittest discover -s tests` and
`node --test tests/bulk_picks.test.cjs tests/pick_confirm.test.cjs`.
Original coverage included the exact 24-hour/18-hour boundaries, Friday/midweek schedule
arithmetic, schedule changes, stale sync rejection, overlapping ticks, twenty saved
entries reused after restart, immutable facts after source refresh, bounded known
failure retries, uncertain outcomes, expired/fenced scheduler claims, blocked-context
freezing, late successful responses, stale heartbeat and read-only authenticated
monitoring. Supervisor tests cover opt-in startup, independent worker recovery and
child cleanup. Existing PR26 cross-user/league reuse, five-entry GET/HTMX checks,
PR24 ranking, PR25 grounding and protected game-state tests also passed.

The full 463 Python / 10 JavaScript suites passed again after the authorized live
evaluation. That evaluation used 20 OpenAI calls and a read-only production snapshot;
all content/operational persistence and UI setup stayed local. No production writes,
Railway configuration changes, n8n changes, merge or deployment were performed.
Unrelated local files and `.env` remain untouched.
