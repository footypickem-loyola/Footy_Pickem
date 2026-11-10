"""Synthetic Linux proof of a protected pathname, not a runtime guard.

Run on an isolated Linux runner: sudo -n "$(command -v python)" -I -B tests/test_linux_gameplay_namespace_proof.py
The root harness owns the fixtures. SQLite acquisition and mutation attempts
run under a separate unprivileged UID. No mount is created or replaced.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import ctypes
import errno
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest

SERVICE_ID = 65534
PR_SET_NO_NEW_PRIVS = 38


class BindingRefused(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise BindingRefused(reason)


def file_identity(path):
    # Setup/admission only, outside live SQLite transactions. O_PATH does not
    # open the database for I/O. Never infer a SQLite descriptor from this one.
    fd = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        value = os.fstat(fd)
        fields = Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines()
        mount, = (int(line.split()[1]) for line in fields if line.startswith('mnt_id:'))
        return {'device': value.st_dev, 'inode': value.st_ino, 'mount': mount}
    finally:
        os.close(fd)


def mount_namespace():
    return os.readlink('/proc/self/ns/mnt')


def admit(database, approval):
    try:
        record = approval.lstat()
        require(stat.S_ISREG(record.st_mode) and record.st_uid == 0
                and not record.st_mode & 0o022 and record.st_nlink == 1, 'UNTRUSTED_BINDING')
        require(approval.resolve(strict=True) == approval, 'UNTRUSTED_BINDING')
        for parent in (*approval.parents, *database.parents):
            value = parent.lstat()
            require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0
                    and (not value.st_mode & 0o022 or value.st_mode & stat.S_ISVTX),
                    'UNPROTECTED_ANCESTRY')
        try:
            expected = json.loads(approval.read_text(encoding='utf-8'))
        except (UnicodeError, json.JSONDecodeError):
            raise BindingRefused('INVALID_BINDING') from None
        require(type(expected) is dict and set(expected) == {'path', 'namespace', 'file'}
                and type(expected['path']) is str and type(expected['namespace']) is str
                and type(expected['file']) is dict and set(expected['file']) == {'device', 'inode', 'mount'}
                and all(type(value) is int for value in expected['file'].values()), 'INVALID_BINDING')
        require(expected['path'] == str(database) and database.resolve(strict=True) == database,
                'PATH_MISMATCH')
        require(expected['namespace'] == mount_namespace(), 'NAMESPACE_MISMATCH')
        value = database.lstat()
        require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and value.st_uid == 0
                and value.st_gid == SERVICE_ID and stat.S_IMODE(value.st_mode) == 0o660,
                'UNPROTECTED_DATABASE')
        parent = database.parent.lstat()
        require(parent.st_gid == SERVICE_ID and stat.S_IMODE(parent.st_mode) == 0o1770,
                'UNPROTECTED_DIRECTORY')
        actual = file_identity(database)
        require(actual['mount'] != file_identity(approval)['mount'], 'BINDING_ON_DATABASE_MOUNT')
        require(actual['mount'] == expected['file']['mount'], 'MOUNT_MISMATCH')
        require(actual == expected['file'], 'FILE_MISMATCH')
    except (FileNotFoundError, PermissionError):
        raise BindingRefused('BINDING_OR_TARGET_UNAVAILABLE') from None


def assert_denied(action):
    try:
        action()
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EPERM):
            return
        raise
    raise AssertionError('Unprivileged pathname replacement succeeded')


def sidecars(database, expected=()):
    for suffix in ('-wal', '-shm', '-journal'):
        path = database.with_name(database.name + suffix)
        if suffix not in expected:
            if path.exists() or path.is_symlink():
                raise AssertionError(f'Unexpected SQLite sidecar: {suffix}')
            continue
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and (info.st_uid, info.st_gid) == (SERVICE_ID, SERVICE_ID)
                and not info.st_mode & 0o007):
            raise AssertionError(f'Sidecar must be service-owned and private to the service group: {suffix}')


def read_commits(database, mode):
    # A final read/write handle can clean WAL state when it closes.
    with closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True)) as db:
        if db.execute('PRAGMA journal_mode').fetchone() != (mode,):
            raise AssertionError('Unexpected journal mode on reopen')
        if db.execute('SELECT id FROM sample ORDER BY id').fetchall() != [(1,), (2,)]:
            raise AssertionError('Concurrent commits missing on reopen')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise AssertionError('SQLite integrity check failed')
    sidecars(database)


def exercise(database, mode):
    candidate = database.with_name('candidate.sqlite')
    alias = database.with_name('alias.sqlite')
    sidecars(database)
    shutil.copyfile(database, candidate)
    alias.symlink_to(candidate)
    # DELETE and WAL are selected by the root fixture before the child opens.
    # All connections use the stock VFS and the same canonical filename.
    opened = Barrier(3, timeout=10)
    checked = Barrier(3, timeout=10)

    def write(value):
        with closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True, timeout=5,
                                     isolation_level=None)) as db:
            if db.execute('PRAGMA journal_mode').fetchone() != (mode,):
                raise AssertionError('Unexpected journal mode')
            opened.wait()
            checked.wait()
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO sample VALUES (?)', (value,))
            sidecars(database, ('-wal', '-shm') if mode == 'wal' else ('-journal',))
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(write, n) for n in (1, 2)]
        opened.wait()
        try:
            assert_denied(lambda: os.replace(candidate, database))
            assert_denied(lambda: os.replace(alias, database))
            assert_denied(lambda: database.unlink())
            assert_denied(lambda: database.rename(database.with_name('moved.sqlite')))
            assert_denied(lambda: database.parent.rename(database.parent.with_name('moved-volume')))
        finally:
            checked.wait()
        for result in results:
            result.result()
    sidecars(database)
    read_commits(database, mode)


def child(database, approval, mode):
    if (mode not in ('admit', 'delete', 'wal', 'reopen-delete', 'reopen-wal') or database.name != 'game.sqlite'
            or database.parent.name != 'volume' or database.parent.parent.parent != Path('/tmp')
            or not database.parent.parent.name.startswith('footy-namespace-')
            or approval.name != 'approved.json' or approval.parent.parent != Path('/dev/shm')
            or not approval.parent.name.startswith('footy-binding-')):
        raise AssertionError('Synthetic fixture paths required')
    # PR_SET_NO_NEW_PRIVS prevents the child from regaining privileges via exec.
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    prctl.argtypes = (ctypes.c_int,) + (ctypes.c_ulong,) * 4
    if prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), 'Cannot set no_new_privs')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines()
                  if ':' in line)
    if (os.getresuid() != (SERVICE_ID,) * 3 or os.getresgid() != (SERVICE_ID,) * 3
            or os.getgroups() or status['NoNewPrivs'].strip() != '1'
            or any(int(status[key].strip(), 16) for key in ('CapEff', 'CapPrm', 'CapInh', 'CapAmb'))):
        raise AssertionError('Proof child must have no elevated identity or capabilities')
    try:
        admit(database, approval)
    except BindingRefused as error:
        print(error)
        return 2
    assert_denied(lambda: approval.open('wb').close())
    assert_denied(lambda: approval.unlink())
    assert_denied(lambda: approval.rename(approval.with_name('moved.json')))
    assert_denied(lambda: approval.with_name('forged.json').touch(exist_ok=False))
    if mode.startswith('reopen-'):
        read_commits(database, mode.removeprefix('reopen-'))
    elif mode != 'admit':
        exercise(database, mode)
    print('ACCEPTED')
    return 0


@unittest.skipUnless(sys.platform == 'linux', 'Linux permission and mount proof')
class ProtectedNamespaceProof(unittest.TestCase):
    def setUp(self):
        if os.geteuid() != 0 or not Path('/dev/shm').is_dir():
            self.fail('Proof needs isolated root setup and a separate /dev/shm mount')
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='footy-namespace-', dir='/tmp')))
        self.external = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='footy-binding-', dir='/dev/shm')))
        self.root.chmod(0o755)
        self.external.chmod(0o755)
        volume = self.root / 'volume'
        volume.mkdir()
        os.chown(volume, 0, SERVICE_ID)
        volume.chmod(0o1770)
        self.database = volume / 'game.sqlite'
        self.approval = self.external / 'approved.json'
        self.script = self.root / 'probe.py'
        shutil.copyfile(__file__, self.script)
        self.script.chmod(0o444)

    def fixture(self, mode):
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute(f'PRAGMA journal_mode={mode}').fetchone(), (mode,))
            db.execute('CREATE TABLE sample (id INTEGER PRIMARY KEY)')
        os.chown(self.database, 0, SERVICE_ID)
        self.database.chmod(0o660)
        self.approved = {'path': str(self.database), 'namespace': mount_namespace(),
                         'file': file_identity(self.database)}
        self.record()

    def record(self, **changes):
        self.approval.write_text(json.dumps(self.approved | changes))
        self.approval.chmod(0o644)
        self.assertNotEqual(file_identity(self.approval)['mount'], self.approved['file']['mount'],
                            'Approval must use a separate mount')

    def run_child(self, mode='admit', expected='ACCEPTED'):
        result = subprocess.run([sys.executable, '-I', '-B', str(self.script), '--child',
                                 str(self.database), str(self.approval), mode],
                                user=SERVICE_ID, group=SERVICE_ID, extra_groups=[],
                                cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0 if expected == 'ACCEPTED' else 2, result.stderr)
        self.assertEqual(result.stdout.strip(), expected)

    def test_concurrent_wal_and_protected_path(self):
        self.fixture('wal')
        self.run_child('wal')
        self.run_child('reopen-wal')

    def test_concurrent_delete_and_protected_path(self):
        self.fixture('delete')
        self.run_child('delete')
        self.run_child('reopen-delete')

    def test_binding_required_and_wrong_mount_refused(self):
        self.fixture('delete')
        self.approval.unlink()
        self.run_child(expected='BINDING_OR_TARGET_UNAVAILABLE')
        self.record()
        self.approval.write_text('{}')
        self.run_child(expected='INVALID_BINDING')
        self.record(path=str(self.database.with_name('wrong.sqlite')))
        self.run_child(expected='PATH_MISMATCH')
        self.record(file=self.approved['file'] | {'mount': file_identity(self.external)['mount']})
        self.run_child(expected='MOUNT_MISMATCH')
        self.record()
        os.chown(self.approval, 0, SERVICE_ID)
        self.approval.chmod(0o664)
        self.run_child(expected='UNTRUSTED_BINDING')

    def test_stale_binding_refuses_operator_replacement_after_close(self):
        self.fixture('delete')
        self.run_child('delete')
        copy = Path(shutil.copyfile(self.database, self.root / 'replacement.sqlite'))
        os.chown(copy, 0, SERVICE_ID)
        copy.chmod(0o660)
        os.replace(copy, self.database)
        self.run_child(expected='FILE_MISMATCH')


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--child':
        sys.exit(child(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]))
    unittest.main(verbosity=2)
