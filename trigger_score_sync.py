#!/usr/bin/env python3
"""Sync scores, then optionally dispatch pending enrichment to Flask."""

import json
import os
import sys
from typing import Any, Callable, Dict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT_SECONDS = 180


def trigger_score_sync(
    endpoint_url: str,
    sync_secret: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    opener: Callable[..., Any] = urlopen,
) -> Dict[str, Any]:
    endpoint_url = endpoint_url.strip()
    sync_secret = sync_secret.strip()
    if not endpoint_url:
        raise RuntimeError("SYNC_URL is required")
    if not sync_secret:
        raise RuntimeError("SYNC_SECRET is required")
    if not endpoint_url.startswith(("https://", "http://")):
        raise RuntimeError("SYNC_URL must start with https:// or http://")

    request = Request(
        endpoint_url,
        data=b"",
        headers={
            "Accept": "application/json",
            "X-Sync-Secret": sync_secret,
            "User-Agent": "footy-pickem-railway-cron/1.0",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Sync endpoint returned HTTP {exc.code}: {body[:500]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach sync endpoint: {exc.reason}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Sync endpoint returned invalid JSON with HTTP {status}: {body[:500]}"
        ) from exc
    if status != 200 or not payload.get("ok"):
        raise RuntimeError(
            f"Sync endpoint reported failure with HTTP {status}: {body[:500]}"
        )
    return payload


def dispatch_pick_insight(endpoint_url, sync_secret, timeout_seconds, opener=urlopen):
    """Reuse the authenticated Flask transport; never contact a provider here."""
    payload = trigger_score_sync(endpoint_url, sync_secret, timeout_seconds, opener)
    if (not isinstance(payload, dict) or payload.get('status') not in
            ('no_work', 'already_completed', 'completed')):
        raise RuntimeError('Invalid Pick Insight task summary')
    summary = {'status': payload['status']}
    for field in ('clubs', 'clubs_with_scorers'):
        value = payload.get(field)
        if type(value) is not int or value < 0:
            raise RuntimeError('Invalid Pick Insight task summary')
        summary[field] = value
    return summary


def main() -> int:
    endpoint_url = os.environ.get("SYNC_URL", "")
    sync_secret = os.environ.get("SYNC_SECRET", "")
    try:
        # Railway owns the five-minute schedule; every invocation performs a sync.
        timeout_seconds = int(
            os.environ.get("SYNC_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        )
        summary = trigger_score_sync(
            endpoint_url,
            sync_secret,
            timeout_seconds=timeout_seconds,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Scheduled score sync failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, sort_keys=True))
    print(
        "Scheduled score sync completed: "
        f"{summary.get('results_imported', 0)} imported; "
        f"{summary.get('pending_matches', 0)} pending."
    )
    insight_url = os.environ.get('PICK_INSIGHT_SYNC_URL', '').strip()
    if insight_url:
        try:
            enrichment = dispatch_pick_insight(insight_url, sync_secret, timeout_seconds)
            print('Pick Insight task: ' + json.dumps(enrichment, sort_keys=True))
        except Exception:
            # Score sync and finalization already succeeded. Do not print
            # transport exception text, response bodies, endpoint URLs or secrets.
            print('Pick Insight refresh dispatch failed; score sync succeeded. '
                  'Pending work can be retried on the next run.', file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
