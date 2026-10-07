"""Isolated shared content storage. No imports of game models or provider clients."""
from contextlib import contextmanager, closing
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
from uuid import uuid4
from datetime import timedelta

from pre_match_brief import utc, require, serialize, CorrespondentError

APP_ID = 1347240514
TABLES = {'brief_batches', 'team_briefs'}
KEY = 'provider,competition,season_year,external_fixture_id,team_id,content_version'
WHERE = 'provider=? AND competition=? AND season_year=? AND external_fixture_id=? AND team_id=? AND content_version=?'


def key_values(key, version):
    return tuple(key[k] for k in ('provider', 'competition', 'season_year', 'external_fixture_id', 'team_id')) + (version,)


def digest(context):
    return sha256(serialize(context).encode()).hexdigest()


def _check(db, empty=False):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    app = db.execute('PRAGMA application_id').fetchone()[0]
    require((empty and not tables and app == 0) or (tables == TABLES and app == APP_ID
            and db.execute('PRAGMA user_version').fetchone()[0] == 1), 'Not a shared brief content database')


@contextmanager
def transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def initialize(path):
    """Explicit only. Refuse all existing game/reference/unrelated databases."""
    path = Path(path).resolve()
    if path.exists():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as check:
            _check(check, empty=True)
    with closing(sqlite3.connect(path)) as db:
        with transaction(db):
            _check(db, empty=True)
            db.execute('''CREATE TABLE IF NOT EXISTS brief_batches (
                provider TEXT NOT NULL,competition TEXT NOT NULL,season_year INTEGER NOT NULL,
                matchweek INTEGER NOT NULL,content_version TEXT NOT NULL,as_of TEXT NOT NULL,
                model TEXT NOT NULL,prompt_sha256 TEXT NOT NULL,
                PRIMARY KEY(provider,competition,season_year,matchweek,content_version))''')
            db.execute('''CREATE TABLE IF NOT EXISTS team_briefs (
                provider TEXT NOT NULL,competition TEXT NOT NULL,season_year INTEGER NOT NULL,
                external_fixture_id INTEGER NOT NULL,team_id INTEGER NOT NULL,content_version TEXT NOT NULL,
                matchweek INTEGER NOT NULL,home TEXT NOT NULL,away TEXT NOT NULL,team TEXT NOT NULL,kickoff TEXT NOT NULL,
                context_json TEXT,context_sha256 TEXT,
                status TEXT NOT NULL CHECK(status IN ('pending','blocked','running','failed','uncertain','succeeded','expired')),
                attempts INTEGER NOT NULL DEFAULT 0,claim_token TEXT,lease_until TEXT,error_code TEXT,
                generated_json TEXT,completed_at TEXT,
                PRIMARY KEY(provider,competition,season_year,external_fixture_id,team_id,content_version))''')
            db.execute(f'PRAGMA application_id={APP_ID}')
            db.execute('PRAGMA user_version=1')


class BriefStore:
    def __init__(self, path):
        self.db = sqlite3.connect(Path(path).resolve(strict=True).as_uri()+'?mode=rw', uri=True, timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            _check(self.db)
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def batch(self, season_year, matchweek, version):
        row = self.db.execute('''SELECT * FROM brief_batches WHERE provider='football-data' AND competition='PL'
            AND season_year=? AND matchweek=? AND content_version=?''', (season_year, matchweek, version)).fetchone()
        return dict(row) if row else None

    def prepare(self, slots, *, season_year, matchweek, version, as_of, model, prompt_sha):
        require(isinstance(version, str) and 1 <= len(version) <= 100 and version.strip() == version, 'Content version required')
        require(len(slots) == 20 and len({key_values(s['identity'], version) for s in slots}) == 20, 'Twenty canonical slots required')
        with transaction(self.db):
            old = self.batch(season_year, matchweek, version)
            if old:
                require((old['as_of'], old['model'], old['prompt_sha256']) == (as_of, model, prompt_sha),
                        'Content version already uses different cutoff/model/prompt; choose a new version')
            else:
                self.db.execute('INSERT INTO brief_batches VALUES(?,?,?,?,?,?,?,?)',
                    ('football-data', 'PL', season_year, matchweek, version, as_of, model, prompt_sha))
            for slot in slots:
                values = key_values(slot['identity'], version)
                old = self.db.execute(f'SELECT * FROM team_briefs WHERE {WHERE}', values).fetchone()
                if old:
                    require((old['matchweek'], old['kickoff'], old['home'], old['away'], old['team']) ==
                            (matchweek, slot['kickoff'], slot['home'], slot['away'], slot['team']),
                            'Canonical schedule changed; use a new content version')
                    # Never replace ready contexts/successes just because the source refreshed.
                    if old['status'] == 'blocked' and slot['context']:
                        self.db.execute(f"UPDATE team_briefs SET context_json=?,context_sha256=?,status='pending',error_code=NULL WHERE {WHERE}",
                            (serialize(slot['context']), digest(slot['context'])) + values)
                    continue
                c = slot['context']
                self.db.execute(f'''INSERT INTO team_briefs ({KEY},matchweek,home,away,team,kickoff,
                    context_json,context_sha256,status,error_code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    values + (matchweek, slot['home'], slot['away'], slot['team'], slot['kickoff'],
                              serialize(c) if c else None, digest(c) if c else None,
                              'pending' if c else 'blocked', slot['error']))

    def claim(self, key, version, now, *, retry_failed=False, retry_uncertain=False):
        now = utc(now)
        values = key_values(key, version)
        with transaction(self.db):
            row = self.db.execute(f'SELECT * FROM team_briefs WHERE {WHERE}', values).fetchone()
            require(row is not None, 'Unprepared shared entry')
            status = row['status']
            if status == 'succeeded':
                return None
            if now >= utc(row['kickoff']):
                self.db.execute(f"UPDATE team_briefs SET status='expired',claim_token=NULL,error_code='kickoff_passed' WHERE {WHERE}", values)
                return None
            if status == 'running' and now >= utc(row['lease_until']):
                self.db.execute(f"UPDATE team_briefs SET status='uncertain',claim_token=NULL,error_code='lease_expired' WHERE {WHERE}", values)
                status = 'uncertain'
            eligible = status == 'pending' or (retry_failed and status == 'failed') or (retry_uncertain and status == 'uncertain')
            if not eligible:
                return None
            token = uuid4().hex
            self.db.execute(f"UPDATE team_briefs SET status='running',claim_token=?,lease_until=?,attempts=attempts+1,error_code=NULL WHERE {WHERE}",
                (token, (now+timedelta(minutes=10)).isoformat()) + values)
            return dict(token=token, context=json.loads(row['context_json']))

    def finish(self, key, version, token, now, *, generated=None, error=None, uncertain=False):
        now = utc(now)
        values = key_values(key, version)
        with transaction(self.db):
            row = self.db.execute(f'SELECT * FROM team_briefs WHERE {WHERE}', values).fetchone()
            if row is None or row['status'] != 'running' or row['claim_token'] != token:
                return False  # Fenced old worker cannot change the current owner/result.
            if now >= utc(row['kickoff']):
                status, error, generated = 'expired', 'kickoff_passed', None
            elif now >= utc(row['lease_until']):
                status, error, generated = 'uncertain', 'lease_expired', None
            else:
                status = 'succeeded' if generated is not None else ('uncertain' if uncertain else 'failed')
            self.db.execute(f'''UPDATE team_briefs SET status=?,generated_json=?,completed_at=?,error_code=?,claim_token=NULL,
                lease_until=NULL WHERE {WHERE}''', (status, serialize(generated) if generated else None,
                now.isoformat(), error) + values)
            return status == 'succeeded'

    def summary(self, season_year, matchweek, version):
        rows = self.db.execute('''SELECT status,COUNT(*) AS n FROM team_briefs WHERE provider='football-data'
            AND competition='PL' AND season_year=? AND matchweek=? AND content_version=? GROUP BY status''',
            (season_year, matchweek, version)).fetchall()
        return {r['status']: r['n'] for r in rows}


def read_saved(path, key, version, *, kickoff, home, away, team):
    """Read-only lookup. Missing/unconfigured/corrupt content is unavailable, not work."""
    if not path or not version:
        return None
    try:
        uri = Path(path).resolve(strict=True).as_uri()+'?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as db:
            _check(db)
            row = db.execute(f'''SELECT generated_json,kickoff,home,away,team,context_json,context_sha256 FROM team_briefs
                WHERE {WHERE} AND status='succeeded' ''', key_values(key, version)).fetchone()
            if not row or (utc(row[1]), row[2], row[3], row[4]) != (utc(kickoff), home, away, team):
                return None
            generated = json.loads(row[0])
            if not isinstance(generated, dict) or generated.get('identity') != key or not isinstance(generated.get('body'), str) or not generated['body'].strip():
                return None
            from shared_brief_context import validate_team_context
            from correspondent.pre_match_writer import validate_fixture_output
            context = validate_team_context(json.loads(row[5]))
            if context['identity'] != key or digest(context) != row[6] or generated.get('context_sha256') != row[6]:
                return None
            validate_fixture_output(generated['output'], context['fixture'])
            if generated['body'] != ' '.join(s['text'] for s in generated['output']['sentences']):
                return None
            return generated['body']
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, CorrespondentError):
        return None
