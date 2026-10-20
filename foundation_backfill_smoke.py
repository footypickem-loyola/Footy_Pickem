"""Application smoke on verified disposable copies in a fresh child interpreter.

Defense in depth for reviewed application code, not an adversarial Python sandbox.
Every blocked attempt makes writer_resume_authorized false, even if swallowed.
Sources are read only; application connections are restricted to private copies.
Local proof does not authorize production use or writer resume.
"""
from contextlib import closing
import ctypes  # Initialize the stdlib loader before denying native loads in app code.
import hashlib
import gc
import importlib
import json
import os
from pathlib import Path
import sqlite3
import socket
import sqlite3.dbapi2
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

# Standalone -I invocation trusts only the explicitly launched tooling directory.
if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from foundation_backfill_operator import Refusal, need, path, sha, strict_json, silence


class SmokeGuard:
    def __init__(self, databases, session_root, *, clone_root=None, protected_sources=()):
        self.databases = {path(p) for p in databases}
        self.sessions = path(session_root, directory=True)
        self.clone_root = path(clone_root, directory=True) if clone_root else None
        if self.clone_root:
            need(all(self.clone_root in p.parents for p in self.databases), 'CLONES_ONLY')
        self.protected = {str(path(p)) for p in protected_sources}
        self.initialization = []
        self.attempts = []
        self.connections = []
        self.active = False
        self.connecting = False
        self.original = sqlite3.connect
        self.patches = []
        self.session_fds = {}
        self.original_os_open = os.open

    def deny(self, category):
        self.attempts.append(category)
        raise Refusal('SMOKE_SIDE_EFFECT_BLOCKED')

    def writable(self, value):
        if isinstance(value, int):
            try:
                info = os.fstat(value)
                return self.session_fds.get(value) == (info.st_dev, info.st_ino)
            except OSError:
                return False
        try:
            p = Path(value).resolve()
            return p == self.sessions or self.sessions in p.parents
        except (ValueError, TypeError):
            return False

    def audit(self, event, args):
        if not self.active:
            return
        if event.startswith('socket.'):
            self.deny('network')
        if event in ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty',
                     'os.exec', 'os.spawn', 'ctypes.dlopen', 'ctypes.dlsym'):
            self.deny('subprocess_or_native')
        if event == 'sqlite3.connect' and not self.connecting:
            self.deny('unguarded_sqlite_connection')
        if event in ('sqlite3.load_extension', 'sqlite3.enable_load_extension'):
            self.deny('sqlite_extension')
        if event == 'open':
            filename, mode, flags = args
            if not isinstance(filename, int):
                candidate = str(Path(filename).resolve())
                if any(candidate == p or candidate in {p + '-wal', p + '-shm', p + '-journal'} for p in self.protected):
                    self.deny('source_file_access')
            write = (mode is not None and any(c in mode for c in 'wax+')) or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if write and not self.writable(filename):
                self.deny('filesystem_write')
        if event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.utime', 'os.truncate'):
            if not self.writable(args[0]):
                self.deny('filesystem_write')
        if event in ('os.rename', 'os.link', 'os.symlink'):
            if event != 'os.rename' or not all(self.writable(p) for p in args[:2]):
                self.deny('filesystem_write')

    def authorizer(self, action, first, second, database, source, target=None):
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_TRANSACTION,
                   sqlite3.SQLITE_RECURSIVE, sqlite3.SQLITE_FUNCTION}
        if action == sqlite3.SQLITE_FUNCTION and (second or '').lower() == 'load_extension':
            self.attempts.append('sql_function_write')
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            name = (first or '').lower()
            if self.clone_root and ((name == 'journal_mode' and str(second).lower() == 'wal') or
                                    (name == 'synchronous' and str(second).lower() in {'normal', '1'})):
                self.initialization.append(dict(store=target.name, pragma=name, value=str(second).lower()))
                return sqlite3.SQLITE_OK
            # Schema PRAGMAs take a table argument; other PRAGMA assignments are denied.
            if name in {'table_info', 'table_xinfo', 'index_list', 'index_info', 'foreign_key_list'}:
                return sqlite3.SQLITE_OK
            if second is None and name in {'read_uncommitted', 'journal_mode', 'synchronous',
                                          'query_only', 'foreign_keys', 'application_id', 'user_version'}:
                return sqlite3.SQLITE_OK
            self.attempts.append('sql_pragma_write')
            return sqlite3.SQLITE_DENY
        if action not in allowed:
            self.attempts.append('sql_write')
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def connect(self, database, *args, **kwargs):
        raw = str(database)
        if raw.startswith('file:'):
            raw = unquote(urlsplit(raw).path)
            if os.name == 'nt' and raw.startswith('/'):
                raw = raw[1:]
        try:
            target = path(raw)
        except Exception:
            self.deny('unapproved_database')
        if target not in self.databases:
            self.deny('unapproved_database')
        owner = self
        class GuardedConnection(sqlite3.Connection):
            def set_authorizer(self, *a, **kw):
                owner.deny('sql_guard_removal')
            def enable_load_extension(self, *a, **kw):
                owner.deny('sqlite_extension')
            def load_extension(self, *a, **kw):
                owner.deny('sqlite_extension')
            def backup(self, *a, **kw):
                owner.deny('sqlite_backup')
            def deserialize(self, *a, **kw):
                owner.deny('sqlite_deserialize')
        self.connecting = True
        try:
            db = self.original(target.as_uri() + ('?mode=rw' if self.clone_root else '?mode=ro'), uri=True, timeout=2,
                               check_same_thread=False, factory=GuardedConnection)
        finally:
            self.connecting = False
        if not self.clone_root:
            db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA temp_store=MEMORY')
        sqlite3.Connection.set_authorizer(db, lambda *a: self.authorizer(*a, target=target))
        self.connections.append(db)
        return db

    def session_open(self, filename, *args, **kwargs):
        fd = self.original_os_open(filename, *args, **kwargs)
        if self.writable(filename):
            info = os.fstat(fd)
            self.session_fds[fd] = (info.st_dev, info.st_ino)
        return fd

    def __enter__(self):
        sys.addaudithook(self.audit)
        self.active = True
        for module in (sqlite3, sqlite3.dbapi2):
            p = patch.object(module, 'connect', self.connect)
            p.start()
            self.patches.append(p)
        p = patch.object(os, 'open', self.session_open)
        p.start()
        self.patches.append(p)
        # Explicit patches also isolate callers when an outer test runner has
        # installed its own transport stubs; the audit hook covers cached aliases.
        for owner, name in ((socket, 'create_connection'), (socket, 'getaddrinfo'),
                            (socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                            (socket.socket, 'sendto'), (socket.socket, 'sendmsg')):
            if hasattr(owner, name):
                p = patch.object(owner, name, lambda *a, **k: self.deny('network'))
                p.start()
                self.patches.append(p)
        # Background worker creation is not needed for any read/render path.
        import threading
        p = patch.object(threading.Thread, 'start', lambda *a, **k: self.deny('thread_start'))
        p.start()
        self.patches.append(p)
        return self

    def __exit__(self, *args):
        for db in self.connections:
            try:
                db.close()
            except sqlite3.Error:
                pass
        for p in reversed(self.patches):
            p.stop()
        self.active = False


def fingerprint(filename):
    from foundation_migrations import digest, schema, row_manifest, table_columns
    with closing(sqlite3.connect(path(filename).as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return digest(dict(schema=schema(db), rows=row_manifest(db, table_columns(db))))


def completed(filename, evidence):
    from foundation_backfill_receipt import verify_state
    with closing(sqlite3.connect(path(filename).as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        result = verify_state(db, require_completed=True, **evidence)
        return result


def _render_copies(request):
    need(request['acknowledge_disposable'] is True, 'DISPOSABLE_ACK_REQUIRED')
    need('pickem_flask_htmx_tabs' not in sys.modules, 'FRESH_INTERPRETER_REQUIRED')
    root = path(request['disposable_root'], directory=True)
    temp_root = Path(tempfile.gettempdir()).resolve()
    need(temp_root in root.parents, 'LOCAL_DISPOSABLE_TEMP_ROOT_REQUIRED')
    names = ('database', 'brief_store', 'scheduler_store')
    stores = {key: path(request[key]) for key in names}
    need(len(set(stores.values())) == 3 and all(root in p.parents for p in stores.values()), 'DISPOSABLE_STORES_REQUIRED')
    need(all(sha(p.read_bytes()) == request['expected_file_sha256'][key] for key, p in stores.items()), 'STORE_HASH_MISMATCH')
    need(not any(Path(str(p) + suffix).exists() for p in stores.values() for suffix in ('-wal', '-shm', '-journal')),
         'STANDALONE_DISPOSABLE_STORES_REQUIRED')
    app_root = path(request['application_root'], directory=True)
    need(sha((app_root / 'pickem_flask_htmx_tabs.py').read_bytes()) == request['application_sha256'], 'APPLICATION_HASH_MISMATCH')
    evidence = request['stage2_evidence']
    initial_state = completed(stores['database'], evidence)
    before = {key: fingerprint(p) for key, p in stores.items()}
    # Authentication identity stays in memory, never in output or a /join request.
    with closing(sqlite3.connect(stores['database'].as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT name FROM players WHERE id=?', (request['player_id'],)).fetchone()
        need(row is not None, 'REVIEWED_TEST_PLAYER_REQUIRED')
        player_name = row[0]
    responses, failed = [], False
    with tempfile.TemporaryDirectory(prefix='smoke-sessions-', dir=root) as session_dir:
        if os.name != 'nt':
            os.chmod(session_dir, 0o700)
        # Environment isolation prevents ambient provider credentials or DB defaults.
        env = {'SYSTEMROOT': os.environ.get('SYSTEMROOT', ''), 'WINDIR': os.environ.get('WINDIR', ''),
               'DB_PATH': 'sqlite:///' + str(stores['database']), 'FLASK_SECRET': 'disposable-smoke-only',
               'INIT_ON_START': '0', 'SHARED_BRIEF_AUTOMATION_ENABLED': '0',
               'SHARED_BRIEF_STORE': str(stores['brief_store']),
               'SHARED_BRIEF_OPERATIONS_STORE': str(stores['scheduler_store'])}
        # Resolve installed framework/Windows console libraries in the trusted bootstrap,
        # before app import. Application code cannot load additional native libraries.
        with patch.dict(os.environ, env, clear=True):
            import flask_session, sqlalchemy, pandas, bleach, markdown, colorama
            if os.name == 'nt':
                import click._winconsole
        with patch.dict(os.environ, env, clear=True), SmokeGuard(stores.values(), session_dir, clone_root=root, protected_sources=request['_protected_sources']) as guard:
            try:
                import flask_session
                original_session = flask_session.Session
                class IsolatedSession(original_session):
                    def init_app(self, app):
                        app.config['SESSION_FILE_DIR'] = session_dir
                        return super().init_app(app)
                with patch.object(flask_session, 'Session', IsolatedSession):
                    sys.path.insert(0, str(app_root))
                    try:
                        app_module = importlib.import_module('pickem_flask_htmx_tabs')
                    finally:
                        sys.path.pop(0)
                app_module.app.config.update(TESTING=True)
                routes = ['/', '/tab/current', '/tab/season',
                          '/partials/fixtures/' + str(request['week_number']),
                          '/partials/scores/' + str(request['week_number'])]
                with app_module.app.test_client() as client:
                    for route in routes:
                        response = client.get(route)
                        responses.append(dict(route=route, status=response.status_code,
                                              nonempty=bool(response.data.strip()), authenticated=False))
                    with client.session_transaction() as session:
                        session['player_name'] = player_name
                    response = client.get('/tab/current')
                    responses.append(dict(route='/tab/current', status=response.status_code,
                                          nonempty=bool(response.data.strip()), authenticated=True))
                app_module.SessionLocal.remove()
                app_module.engine.dispose()
            except BaseException:
                failed = True
        attempts = list(guard.attempts)
        gc.collect()  # Release caught dependency exceptions before Windows temp cleanup.
    after = {key: fingerprint(p) for key, p in stores.items()}
    final_state = completed(stores['database'], evidence)
    same = before == after and initial_state == final_state
    rendered = len(responses) == 6 and all(r['status'] == 200 and r['nonempty'] for r in responses)
    return dict(status='passed' if not failed and not attempts and same and rendered else 'refused',
                writer_resume_authorized=False, production_supported=False,
                responses=responses, render_paths_passed=rendered, app_error=failed,
                blocked_attempts=attempts, disposable_initialization=guard.initialization, fingerprints_before=before, fingerprints_after=after,
                fingerprints_unchanged=same, completed_stage2_state_valid=True)


def source_bytes(stores):
    return {key: {suffix: sha(Path(str(p) + suffix).read_bytes())
                  if Path(str(p) + suffix).exists() else None
                  for suffix in ('', '-wal', '-shm', '-journal')} for key, p in stores.items()}


def consistent_copy(source, destination):
    # Exclusive new artifact; SQLite backup includes committed WAL state.
    with destination.open('xb'):
        pass
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
    with closing(sqlite3.connect(destination.as_uri() + '?mode=ro', uri=True)) as check:
        need(check.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'COPY_INTEGRITY')
        need(check.execute('PRAGMA foreign_key_check').fetchall() == [], 'COPY_FOREIGN_KEYS')


def run_smoke(request):
    from foundation_backfill_operator import private_directory
    need(request['acknowledge_disposable'] is True, 'DISPOSABLE_ACK_REQUIRED')
    root = private_directory(request['disposable_root'])
    need(Path(tempfile.gettempdir()).resolve() in root.parents, 'LOCAL_DISPOSABLE_TEMP_ROOT_REQUIRED')
    stores = {key: path(request[key]) for key in ('database', 'brief_store', 'scheduler_store')}
    need(len(set(stores.values())) == 3, 'DISTINCT_SOURCES_REQUIRED')
    before_bytes = source_bytes(stores)
    need(all(before_bytes[k][''] == request['expected_file_sha256'][k] for k in stores), 'STORE_HASH_MISMATCH')
    before = {key: fingerprint(p) for key, p in stores.items()}
    initial = completed(stores['database'], request['stage2_evidence'])
    # A new private area holds all copies and sessions, never the original stores.
    try:
        with tempfile.TemporaryDirectory(prefix='smoke-copies-', dir=root) as area:
            private_directory(area)
            copies = {key: Path(area) / (key + '.db') for key in stores}
            for key in stores:
                consistent_copy(stores[key], copies[key])
            need({key: fingerprint(p) for key, p in copies.items()} == before, 'COPY_STATE_MISMATCH')
            need(completed(copies['database'], request['stage2_evidence']) == initial, 'COPY_RECEIPT_MISMATCH')
            clone_request = dict(request, disposable_root=area,
                                 **{key: str(p) for key, p in copies.items()})
            clone_request['expected_file_sha256'] = {key: sha(p.read_bytes()) for key, p in copies.items()}
            clone_request['_protected_sources'] = list(stores.values())
            result = _render_copies(clone_request)
    finally:
        # Check original sources even when import, rendering or copy validation fails.
        after = {key: fingerprint(p) for key, p in stores.items()}
        after_bytes = source_bytes(stores)
        unchanged = before == after and before_bytes == after_bytes
        need(unchanged, 'SOURCE_CHANGED_DURING_SMOKE')
    result.update(source_fingerprints_before=before, source_fingerprints_after=after,
                  source_bytes_before=before_bytes, source_bytes_after=after_bytes,
                  source_fingerprints_unchanged=unchanged, copies_verified_before_import=True)
    if not unchanged:
        result['status'] = 'refused'
    return result


def main():
    try:
        with silence():
            need(sys.flags.isolated and sys.dont_write_bytecode, 'ISOLATED_FRESH_PROCESS_REQUIRED')
            need(len(sys.argv) == 2, 'EXPLICIT_REQUEST_PATH_REQUIRED')
            request = strict_json(path(sys.argv[1]).read_bytes())
            result = run_smoke(request)
        code = 0 if result['status'] == 'passed' else 2
    except BaseException:
        result = dict(status='refused', writer_resume_authorized=False, detail='SMOKE_REFUSED')
        code = 2
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    # -I deliberately excludes cwd; only this reviewed tooling directory is added.
    raise SystemExit(main())
