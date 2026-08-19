#!/usr/bin/env python3
"""Call the protected score-sync endpoint once and exit."""

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_SCHEDULE_TIMEZONE = "America/New_York"


def should_sync_now(
    now: datetime | None = None,
    timezone_name: str = DEFAULT_SCHEDULE_TIMEZONE,
) -> bool:
    """Sync every 15 minutes on weekends and hourly on weekdays."""
    try:
        schedule_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"Unknown SYNC_SCHEDULE_TIMEZONE: {timezone_name}"
        ) from exc

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        raise RuntimeError("Schedule time must include a timezone")

    local_now = now.astimezone(schedule_timezone)
    is_weekend = local_now.weekday() >= 5
    return is_weekend or local_now.minute == 0


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


def main() -> int:
    endpoint_url = os.environ.get("SYNC_URL", "")
    sync_secret = os.environ.get("SYNC_SECRET", "")
    try:
        schedule_timezone = os.environ.get(
            "SYNC_SCHEDULE_TIMEZONE", DEFAULT_SCHEDULE_TIMEZONE
        ).strip()
        if not should_sync_now(timezone_name=schedule_timezone):
            print(
                "Scheduled score sync skipped: weekday quarter-hour run; "
                "the next sync is at the top of the hour."
            )
            return 0
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
