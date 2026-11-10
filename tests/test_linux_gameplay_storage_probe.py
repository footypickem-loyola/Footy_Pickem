"""Standalone Linux SQLite diagnostics. No application or runtime guard imports."""
from contextlib import closing
from dataclasses import asdict, dataclass
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
import unittest


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    mount: int


def descriptor_identity(fd):
    value = os.fstat(fd)
    fields = Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines()
    mount_ids = [int(line.split()[1]) for line in fields if line.startswith('mnt_id:')]
    if len(mount_ids) != 1:
        raise AssertionError('procfs did not expose a unique mount ID for the descriptor')
    return FileIdentity(value.st_dev, value.st_ino, mount_ids[0])


def regular_descriptors():
    result = {}
    for entry in Path('/proc/self/fd').iterdir():
        fd = int(entry.name)
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                result[fd] = descriptor_identity(fd)
        except OSError as error:
            if error.errno not in (errno.EBADF, errno.ENOENT):
                raise
    return result


def opened_descriptor(before):
    # Observational attribution only. The harness opens exactly one SQLite
    # connection on this thread, against a standalone DELETE-mode fixture.
    after = regular_descriptors()
    opened = [(fd, value) for fd, value in after.items() if before.get(fd) != value]
    if len(opened) != 1:
        raise AssertionError(f'Cannot attribute SQLite descriptor uniquely: {len(opened)} candidates')
    return opened[0]


def open_fixture(path):
    root = Path(tempfile.gettempdir()).resolve(strict=True)
    if (sys.platform != 'linux' or path.parent.parent != root
            or not path.parent.name.startswith('footy-linux-proof-')
            or path.resolve(strict=True).parent != path.parent):
        raise ValueError('SYNTHETIC_PROBE_PATH_REQUIRED')
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)


def observe(path):
    before = regular_descriptors()
    with closing(open_fixture(path)) as db:
        db.execute('SELECT count(*) FROM sample').fetchone()
        return opened_descriptor(before)[1]


@unittest.skipUnless(sys.platform == 'linux', 'Requires Linux procfs')
class LinuxGameplayStorageProbe(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='footy-linux-proof-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve(strict=True)
        self.database = self.root / 'game.sqlite'
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=DELETE').fetchone(), ('delete',))
            db.execute('CREATE TABLE sample (id INTEGER PRIMARY KEY)')
        self.database.chmod(0o600)
        # Record the reference via a separate OS open before the SQLite probe.
        # This synthetic reference does not grant application authority.
        with os.fdopen(os.open(self.database, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
            self.expected = descriptor_identity(source.fileno())

    def copy(self):
        return Path(shutil.copyfile(self.database, self.root / 'copy.sqlite'))

    def test_raw_descriptor_matches_reference_and_mountinfo(self):
        actual = observe(self.database)
        self.assertEqual(actual, self.expected)
        mounts = {int(line.split()[0]): line.split()[2]
                  for line in Path('/proc/self/mountinfo').read_text().splitlines()}
        self.assertEqual(mounts[actual.mount], f'{os.major(actual.device)}:{os.minor(actual.device)}')

    def test_byte_identical_copy_has_different_file_identity(self):
        duplicate = self.copy()
        self.assertEqual(duplicate.read_bytes(), self.database.read_bytes())
        self.assertNotEqual(observe(duplicate), self.expected)

    def test_symlink_alias_can_reach_the_same_inode(self):
        alias = self.root / 'alias.sqlite'
        alias.symlink_to(self.database)
        self.assertNotEqual(alias, alias.resolve(strict=True))
        with self.assertRaises(OSError) as refused:
            os.open(alias, os.O_RDONLY | os.O_NOFOLLOW)
        self.assertEqual(refused.exception.errno, errno.ELOOP)
        self.assertEqual(observe(alias), self.expected)

    def test_reused_connection_retains_original_descriptor_after_replacement(self):
        before = regular_descriptors()
        with closing(open_fixture(self.database)) as reused:
            self.assertEqual(reused.execute('SELECT count(*) FROM sample').fetchone(), (0,))
            fd, actual = opened_descriptor(before)
            self.assertEqual(actual, self.expected)
            os.replace(self.copy(), self.database)
            self.assertNotEqual(observe(self.database), self.expected)
            reused.execute('BEGIN')
            try:
                self.assertEqual(reused.execute('SELECT count(*) FROM sample').fetchone(), (0,))
                self.assertEqual(descriptor_identity(fd), self.expected)
            finally:
                reused.rollback()

    def test_fresh_process_reopen_matches_then_detects_replacement(self):
        command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--observe', str(self.database)]

        def child():
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return FileIdentity(**json.loads(result.stdout))

        self.assertEqual(child(), self.expected)
        os.replace(self.copy(), self.database)
        self.assertNotEqual(child(), self.expected)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--observe':
        print(json.dumps(asdict(observe(Path(sys.argv[2])))))
    else:
        unittest.main(verbosity=2)
