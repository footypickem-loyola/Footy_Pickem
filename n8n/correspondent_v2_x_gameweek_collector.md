# Correspondent V2 automation

The committed workflows are inactive exports for staging review. Importing them does not activate or deploy them.

## Responsibility boundary

Python/SQLite owns week selection, `finalized_at`, the one-hour buffer, X page checkpoints and the 1,000-post cap, Batch jobs and both classifier passes, recap persistence, and email/notification delivery state. n8n owns schedules, one authorized X request, polling/branching, Gmail Sent lookup, and Gmail send operations.

The workflows never infer whether `AUTOMATED_REVIEW` needs Pass 2. Reconciliation imports Batch results and creates Pass 2 idempotently in Python.

## Workflows

- `correspondent_v2_x_gameweek_collector.workflow.json` runs every five minutes. It gets authoritative targets, claims at most one paid X page for one week, performs exactly one X request, normalizes the response, and atomically checkpoints the page and continuation token in Python. It has no HTTP-node pagination.
- `correspondent_v2_orchestrator.workflow.json` runs every five minutes. It performs at most one allowed Python action per target: submit Pass 1, reconcile classification (including Python-owned Pass 2 creation), or generate the automated recap.
- `correspondent_v2_gmail_delivery.workflow.json` runs every five minutes. It claims recap or cap-alert email work, searches Gmail Sent using Python's stable delivery marker, sends only when no matching Sent message exists, then acknowledges the Gmail message ID to Python.

## Secure configuration

Python/Railway requires:

- `CORRESPONDENT_V2_ENABLED=1`
- `CORRESPONDENT_AUTOMATION_SECRET` (a long random shared secret)
- `CORRESPONDENT_RECAP_RECIPIENTS` (comma-separated league recipients)
- `CORRESPONDENT_ADMIN_ALERT_RECIPIENTS` (comma-separated administrative recipients)
- existing `OPENAI_API_KEY` for Batch and recap generation

n8n requires:

- `PICKEM_BASE_URL`
- the same `CORRESPONDENT_AUTOMATION_SECRET`
- `X_USER_ID`
- `X_USER_ACCESS_TOKEN`
- an n8n Gmail OAuth2 credential authenticated as `footypickem@gmail.com`, attached manually to both Gmail nodes after import

No OAuth token, API token, recipient list, or shared secret belongs in the workflow JSON or source control.

## Safety behavior

- The Python claim response authorizes one page and returns `max_results <= 100`.
- Missing, empty, or whitespace-only X `next_token` becomes `null` and completes collection.
- The next paid page cannot be claimed until the prior page and its sources are committed together.
- An expired ambiguous X claim is not automatically re-fetched. It enters an error state but still accepts a late checkpoint with its original claim token.
- Python enforces 1,000 unique accepted X sources per week; n8n also rejects a page larger than the authorized limit.
- Repeated source checkpoints, classification actions, recap calls, Gmail claims, and acknowledgements are idempotent.
- Reaching the cap creates one durable administrative alert and does not block classification or recap processing.

## Activation checklist

1. Import all three inactive workflows into staging n8n.
2. Configure environment variables and attach the `footypickem@gmail.com` OAuth credential.
3. Validate the target/status calls and a mock/non-paid path without calling X.
4. Validate Gmail Sent lookup with a non-production recipient.
5. Activate only after staging failure/retry tests pass.

The existing Railway Score Sync Cron remains the owner of football-data.org result polling. No separate Railway correspondent cron is needed.
