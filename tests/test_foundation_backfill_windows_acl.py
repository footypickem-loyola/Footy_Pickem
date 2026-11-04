"""Windows-only, non-sensitive synthetic clone ACL tests; never applies Stage 2."""
import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from test_foundation_backfill_offline_rehearsal import OfflineRehearsalTests
from test_foundation_backfill_offline import sha
import foundation_backfill_rehearsal as rehearsal
import foundation_backfill_operator as operator
from foundation_backfill_validation import inspect_database

REAL_PRIVATE_DIRECTORY = operator.private_directory


def powershell(code):
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive',
        '-EncodedCommand',base64.b64encode(code.encode('utf-16le')).decode()],
        capture_output=True,timeout=15)
    if result.returncode != 0: raise AssertionError('synthetic ACL setup failed')
    return result.stdout.strip()


def literal(path): return "'"+str(path).replace("'","''")+"'"


@unittest.skipUnless(os.name == 'nt', 'Windows ACL qualification')
class WindowsACLTests(unittest.TestCase):
    manifest = OfflineRehearsalTests.manifest
    plan = OfflineRehearsalTests.plan
    clone = OfflineRehearsalTests.clone

    @classmethod
    def setUpClass(cls):
        value = os.environ.get('FOOTY_SYNTHETIC_ACL_VALIDATOR')
        if not value: raise unittest.SkipTest('Set FOOTY_SYNTHETIC_ACL_VALIDATOR to the qualified local validator')
        path = Path(value)
        expected = '824b5aecd8f3b73b1a309f5e76e8345eb78dc42cbe9b863ec4ab7074a2946c12'
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise AssertionError('qualified validator hash mismatch')
        spec = importlib.util.spec_from_file_location('qualified_file_acl',path)
        cls.validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.validator)  # stdlib definitions only; never main().

    def setUp(self):
        OfflineRehearsalTests.setUp(self)
        # Only this test's freshly created synthetic root is secured. No existing
        # temp parent, approved storage root, source checkpoint or failed copy.
        rehearsal._restrict_new_windows_artifact(Path(self.temp.name),directory=True)
        operator.private_directory.side_effect = REAL_PRIVATE_DIRECTORY
        REAL_PRIVATE_DIRECTORY(self.path.parent)
        REAL_PRIVATE_DIRECTORY(self.execution)

    def parent_security(self):
        return powershell("$ErrorActionPreference='Stop'; " + ';'.join(
            '[System.IO.Directory]::GetAccessControl('+literal(p)+').Sddl'
            for p in (self.path.parent,self.execution)))

    def test_created_copy_passes_unchanged_validators_and_preserves_fingerprints(self):
        plan = self.plan()
        before_hash, before_inspection = sha(self.path), inspect_database(self.path)
        parents = self.parent_security()
        with self.clone(plan) as target:
            REAL_PRIVATE_DIRECTORY(target.path.parent)
            self.validator.file_acl(target.path)
            self.assertEqual(sha(target.path),before_hash)
            self.assertEqual(inspect_database(target.path),before_inspection)
            for p,kind in ((target.path,'File'),(target.path.parent,'Directory')):
                self.assertEqual(powershell('$a=[System.IO.'+kind+']::GetAccessControl('+literal(p)+'); $a.AreAccessRulesProtected; @($a.Access | Where-Object {$_.IsInherited}).Count'),b'True\r\n0')
            self.assertEqual(self.parent_security(),parents)
        self.assertEqual(sha(self.path),before_hash)
        self.assertEqual(inspect_database(self.path),before_inspection)
        self.assertEqual(self.parent_security(),parents)

    def test_existing_validator_rejects_broad_grant(self):
        with self.clone(self.plan()) as target:
            before = sha(target.path)
            powershell("$ErrorActionPreference='Stop'; $p="+literal(target.path)+"; $a=[System.IO.File]::GetAccessControl($p); $sid=New-Object System.Security.Principal.SecurityIdentifier('S-1-1-0'); $a.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'Read','Allow'))); [System.IO.File]::SetAccessControl($p,$a)")
            with self.assertRaises(self.validator.Refused): self.validator.file_acl(target.path)
            self.assertEqual(sha(target.path),before)

    def test_existing_validator_rejects_inherited_owner_rights(self):
        # Reproduce the failed attempt using only a newly created synthetic tree.
        directory = Path(self.temp.name)/'unwanted-inheritance'
        directory.mkdir()
        powershell("$ErrorActionPreference='Stop'; $p="+literal(directory)+"; $a=[System.IO.Directory]::GetAccessControl($p); $sid=New-Object System.Security.Principal.SecurityIdentifier('S-1-3-4'); $a.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))); [System.IO.Directory]::SetAccessControl($p,$a)")
        path = directory/'synthetic.txt'
        with path.open('xb') as f: f.write(b'harmless synthetic ACL test')
        before = sha(path)
        with self.assertRaises(self.validator.Refused): self.validator.file_acl(path)
        self.assertEqual(sha(path),before)

    def test_acl_failure_stops_before_copying_content(self):
        plan, before = self.plan(), sha(self.path)
        real = rehearsal._restrict_new_windows_artifact
        def refuse_file(path, *, directory=False):
            if not directory: raise rehearsal.RehearsalError('NEW_ARTIFACT_ACL_REQUIRED')
            return real(path,directory=True)
        with patch.object(rehearsal,'_restrict_new_windows_artifact',side_effect=refuse_file), \
             patch.object(rehearsal.shutil,'copyfileobj',side_effect=AssertionError('must not copy')):
            with self.assertRaisesRegex(rehearsal.RehearsalError,'NEW_ARTIFACT_ACL_REQUIRED'):
                with self.clone(plan): pass
        self.assertEqual(sha(self.path),before)


if __name__ == '__main__': unittest.main()
