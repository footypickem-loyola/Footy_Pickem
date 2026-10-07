"""Tiny operational lease/heartbeat store, separate from content and game data."""
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from pre_match_brief import require, utc
from shared_brief_store import transaction

APP_ID = 1347240759


def connect(path, mode='rw'):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode='+mode, uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    return db


def check(db):
    require(db.execute('PRAGMA application_id').fetchone()[0] == APP_ID and
            {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {'scheduler'},
            'Not a brief scheduler database')


def initialize(path):
    if Path(path).exists():
        with closing(connect(path, 'ro')) as db:
            check(db)
        return
    with closing(connect(path, 'rwc')) as db, transaction(db):
        require(not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), 'Unexpected scheduler database')
        db.execute('CREATE TABLE scheduler(id INTEGER PRIMARY KEY CHECK(id=1),token TEXT,lease_until TEXT,heartbeat TEXT,report TEXT)')
        db.execute('INSERT INTO scheduler(id) VALUES(1)')
        db.execute(f'PRAGMA application_id={APP_ID}')


def claim(path, now):
    with closing(connect(path)) as db, transaction(db):
        check(db)
        r=db.execute('SELECT token,lease_until FROM scheduler WHERE id=1').fetchone()
        if r['token'] and utc(r['lease_until']) > utc(now):
            return None
        token=uuid4().hex
        db.execute('UPDATE scheduler SET token=?,lease_until=? WHERE id=1', (token,(utc(now)+timedelta(minutes=5)).isoformat()))
        return token


def finish(path, token, now, report):
    with closing(connect(path)) as db, transaction(db):
        check(db)
        db.execute('UPDATE scheduler SET token=NULL,lease_until=NULL,heartbeat=?,report=? WHERE id=1 AND token=?',
                   (utc(now).isoformat(),json.dumps(report,sort_keys=True),token))


def read_status(path, now):
    try:
        with closing(connect(path,'ro')) as db:
            check(db)
            r=db.execute('SELECT heartbeat,report FROM scheduler WHERE id=1').fetchone()
            report=json.loads(r['report']) if r['report'] else dict(rounds=[])
            stale=not r['heartbeat'] or utc(now)-utc(r['heartbeat']) > timedelta(minutes=5)
            return dict(report, heartbeat=r['heartbeat'], heartbeat_stale=stale,
                        intervention_required=stale or report.get('intervention_required',False))
    except Exception:
        return dict(rounds=[],heartbeat=None,heartbeat_stale=True,intervention_required=True,error_code='scheduler_status_unavailable')
