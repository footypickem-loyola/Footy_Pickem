"""Dedicated, explicitly enabled process on the Flask-owned persistent volume."""
import json
import os
from pathlib import Path
import time
from sqlalchemy.engine import make_url

from pre_match_brief import require
from shared_brief_store import BriefStore
from shared_brief_workflow import clock
from shared_brief_scheduler import tick
import shared_brief_operations as operations


def configuration():
    url=make_url(os.environ.get('DB_PATH',''))
    require(url.drivername=='sqlite' and url.database and url.database!=':memory:', 'File SQLite source required')
    paths=[Path(url.database),Path(os.environ.get('SHARED_BRIEF_STORE','')),
           Path(os.environ.get('SHARED_BRIEF_OPERATIONS_STORE',''))]
    require(all(p.is_absolute() for p in paths) and len({p.resolve() for p in paths})==3,
            'Explicit distinct absolute source/content/operations paths required')
    require(len({p.resolve().parent for p in paths})==1, 'All brief files must use the source volume directory')
    require(paths[0].is_file() and paths[1].is_file(), 'Source and initialized content store required')
    version=os.environ.get('SHARED_BRIEF_CONTENT_VERSION','').strip()
    model=os.environ.get('OPENAI_MODEL','').strip()
    require(version and model and os.environ.get('OPENAI_API_KEY','').strip(), 'Generation configuration missing')
    return paths,dict(content_version=version,model=model,season_year=2026)


def main():
    if os.environ.get('SHARED_BRIEF_AUTOMATION_ENABLED')!='1':
        print(json.dumps(dict(status='disabled')))
        return 0
    try:
        paths,config=configuration()
        store=BriefStore(paths[1]); store.close()
        operations.initialize(paths[2])
    except Exception:
        print(json.dumps(dict(status='configuration_error',intervention_required=True)),flush=True)
        return 1
    while True:
        try:
            report=tick(*paths,**config)
            print(json.dumps(report,sort_keys=True),flush=True)
        except Exception:
            # Never log provider/transport exception text, credentials or file contents.
            print(json.dumps(dict(status='worker_cycle_failed',intervention_required=True)),flush=True)
        time.sleep(60)


if __name__=='__main__':
    raise SystemExit(main())
