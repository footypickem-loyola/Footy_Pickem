"""Dedicated worker: python live_sync_worker.py. Never started by web workers."""
from datetime import datetime
import logging
import os
import time
from live_sync import ingest, polling_window
from sportmonks_live import SportmonksClient, ProviderError


def cycle(models, client, now=None, intervals=(15, 60, 300)):
    now = now or datetime.utcnow()
    fast, near, idle = intervals
    with models.SessionLocal() as db:
        relevant = {f.id for f in polling_window(db, models, now)}
    # The read session is closed before any network I/O.
    if not relevant:
        return idle
    try:
        items = client.livescores()
        with models.SessionLocal() as db, db.begin():
            count = ingest(db, models, items, client.league_id, now)
            live = db.query(models.LiveFixtureState).filter(
                models.LiveFixtureState.fixture_id.in_(relevant),
                models.LiveFixtureState.is_live == True).count()
        logging.info('Live sync stored %d fixtures', count)
        delay = fast if live else near
        limit = client.rate_limit or {}
        if limit.get('remaining') == 0:
            delay = max(delay, min(3600, int(limit.get('resets_in_seconds') or 60)))
        return delay
    except ProviderError as error:
        logging.warning('%s', error)
        return max(near, error.retry_after)
    except Exception:
        # Exception messages may contain SQL parameters; don't log payloads/secrets.
        logging.error('Live sync transaction failed; previous stored state retained')
        return max(near, 60)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if os.environ.get('LIVE_SYNC_ENABLED', '0') != '1':
        logging.info('Live sync disabled; set LIVE_SYNC_ENABLED=1 to run dedicated worker')
        return
    if not os.environ.get('DB_PATH'):
        raise SystemExit('Explicit shared DB_PATH is required for the live worker')
    import pickem_flask_htmx_tabs as models
    client = SportmonksClient()
    intervals = tuple(max(15, int(os.environ.get(key, default))) for key, default in (
        ('LIVE_POLL_SECONDS', '15'), ('LIVE_NEAR_SECONDS', '60'), ('LIVE_IDLE_SECONDS', '300')))
    try:
        while True:
            time.sleep(cycle(models, client, intervals=intervals))
    except KeyboardInterrupt:
        logging.info('Live sync stopped')


if __name__ == '__main__':
    main()
