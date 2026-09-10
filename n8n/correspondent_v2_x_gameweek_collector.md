# Correspondent V2 – X Gameweek Collector

This file preserves the recoverable design for the n8n collector that was originally built and tested in the project chat before n8n's ephemeral storage was lost.

## Status

- This is a reconstruction of the previously approved collector behavior.
- The behavioral rules below are recovered from the project conversation and should be treated as authoritative.
- The exact original n8n workflow JSON and credential object were lost when n8n was writing to ephemeral `/root/.n8n` storage.
- n8n persistence is now fixed to the Railway-mounted volume before this workflow is rebuilt.

## Recovered collector behavior

Workflow name: `Correspondent V2 – X Gameweek Collector`

The collector was intentionally inactive/manual while being developed. No schedule was enabled.

For one configured Premier League gameweek window it should:

1. Retrieve the Footy Pick 'Em X account's reverse-chronological home timeline.
2. Request up to 100 posts per X API page.
3. Follow `next_token` / `pagination_token` until one of the stop conditions is reached.
4. Deduplicate posts by X post ID across pages.
5. Stop when:
   - the configured gameweek time window is exhausted;
   - X returns no next token; or
   - 10 pages / 1,000 posts have been collected.
6. Keep the collected posts in chronological source material without applying the semantic classifier in n8n.
7. Preserve bare retweets as attention signals for storage, but downstream Python must prevent bare retweets from reaching semantic classification or the article writer.
8. Preserve originals, replies, and quote-post commentary as downstream classification candidates.
9. Use the same ceiling/stop detector for mock and live collection paths.
10. If the 1,000-post ceiling is reached, route to a Gmail alert path for `footypickem@gmail.com` so the collection window can be reviewed rather than silently truncating.

The original live test collected 808 posts for Gameweek 3. Those posts were saved only in n8n execution data and had not yet been copied into the Python application's database when the old n8n container was redeployed.

## X API endpoint

The exact URL string from the lost n8n workflow is not recoverable from the surviving conversation. The project conversation described this as the Footy Pick 'Em account's chronological/home timeline. The current X API endpoint matching that approved behavior is:

`GET https://api.x.com/2/users/{id}/timelines/reverse_chronological`

This endpoint requires user-context authentication and returns the authenticated user's reverse-chronological home timeline, including posts from accounts the user follows.

Recommended request parameters for the rebuilt workflow:

- `max_results=100`
- `start_time=<gameweek_start_utc>`
- `end_time=<gameweek_end_utc>`
- `pagination_token=<next_token>` on page 2+
- `tweet.fields=id,text,author_id,created_at,conversation_id,referenced_tweets,public_metrics,entities`
- `expansions=author_id,referenced_tweets.id,referenced_tweets.id.author_id`
- `user.fields=id,name,username,verified`

The exact original field list is not recoverable and should be checked against downstream normalization before the rebuilt workflow is activated.

## Collector state

A workflow execution should maintain at least:

- `gameweek_number`
- `season_code`
- `window_start_utc`
- `window_end_utc`
- `page_number`
- `next_token`
- `seen_post_ids`
- `collected_posts`
- `hit_ceiling`

The hard ceiling is 10 X requests / 1,000 posts for one gameweek collection run.

## Downstream handoff

The rebuilt collector should normalize source records and POST them to the Python V2 staging endpoint in one bounded batch after collection is complete:

`POST /api/correspondent/sources/batch`

The Python endpoint accepts at most 1,000 sources and is transactional/idempotent. It is the correct persistence boundary for the collected X material; n8n execution history must not be the only durable copy.

Each source handed to Python must provide the ingestion fields required by the current branch, including:

- `season_code`
- `week_number`
- `provider`
- `source_type`
- `external_id`
- `body_text`

and may include:

- `canonical_url`
- `author_name`
- `published_at`
- `metadata`

The Correspondent ingestion secret is shared between n8n and the Python service through Railway's `CORRESPONDENT_INGEST_SECRET` shared variable.

## Separation of responsibilities

n8n is responsible for external X collection, pagination, normalization, and delivery.

Python remains the source of truth for Pick 'Em state and performs semantic classification, second-pass review, ranking/selection, article context construction, and recap persistence.

OpenAI classification/writing must not be performed in this collector workflow.

## Safety / cost controls

- Workflow remains manual/inactive until X authentication is restored and a mock run is validated.
- Never run a live X request merely to test n8n plumbing.
- Use mock data first for pagination and handoff testing.
- Maximum live collection is 10 pages / 1,000 posts per gameweek run.
- Persist the completed collection to Python immediately rather than relying on n8n execution data.
- Retain the Gmail ceiling alert path.
- Version-control the workflow export in this repository after rebuilding/importing it into n8n.
