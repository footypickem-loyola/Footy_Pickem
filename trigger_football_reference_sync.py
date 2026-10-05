"""Optional cron entry point; one POST to Flask, no database or provider access."""
import json
import os
from urllib.request import build_opener

from sportmonks_season import NoRedirect
from trigger_score_sync import trigger_score_sync


def main():
    try:
        result = trigger_score_sync(os.environ.get('FOOTBALL_REFERENCE_SYNC_URL', ''),
                                    os.environ.get('SYNC_SECRET', ''),
                                    opener=build_opener(NoRedirect()).open)
        if result.get('status') not in ('completed', 'fresh', 'cooldown', 'already_running'):
            raise ValueError()
        print(json.dumps({'ok': True, 'status': result['status']}))
        return 0
    except Exception:
        # Transport exceptions can contain URLs, credentials, or response bodies.
        print(json.dumps({'ok': False, 'error_code': 'reference_task_failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
