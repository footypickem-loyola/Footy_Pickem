"""Dedicated worker: python live_sync_worker.py. Never started by web workers."""
import logging
import os
import time
from live_bridge import LiveIngestClient, BridgeError
from sportmonks_live import SportmonksClient, ProviderError


def cycle(client, bridge, intervals=(15, 60)):
    fast, near = intervals
    try:
        items = client.livescores()
        count = bridge.send(items, client.league_id)
        logging.info('Live sync stored %d fixtures', count)
        delay = fast if any(item.state in ('live', 'halftime', 'break') for item in items) else near
        limit = client.rate_limit or {}
        if limit.get('remaining') == 0:
            delay = max(delay, min(3600, int(limit.get('resets_in_seconds') or 60)))
        return delay
    except (ProviderError, BridgeError) as error:
        logging.warning('Live sync provider or bridge unavailable; retrying after backoff')
        return min(3600, max(near, error.retry_after))
    except Exception:
        # Transport exceptions can contain URLs, headers or response bodies.
        logging.error('Live sync cycle failed; retrying after backoff')
        return min(3600, max(near, 60))


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if os.environ.get('LIVE_SYNC_ENABLED', '0') != '1':
        logging.info('Live sync disabled; set LIVE_SYNC_ENABLED=1 to run dedicated worker')
        return
    try:
        bridge = LiveIngestClient()
    except BridgeError:
        raise SystemExit('LIVE_INGEST_URL and LIVE_INGEST_SECRET must be configured') from None
    client = SportmonksClient()
    intervals = tuple(min(3600, max(15, int(os.environ.get(key, default)))) for key, default in (
        ('LIVE_POLL_SECONDS', '15'), ('LIVE_NEAR_SECONDS', '60')))
    try:
        while True:
            time.sleep(cycle(client, bridge, intervals=intervals))
    except KeyboardInterrupt:
        logging.info('Live sync stopped')


if __name__ == '__main__':
    main()
