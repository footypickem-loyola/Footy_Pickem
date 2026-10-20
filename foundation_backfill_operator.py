"""Private Stage 2 bootstrap. Standard library only until exact bundle verification.

Run in a fresh interpreter with -I -B. The operator itself is a separately
reviewed bootstrap, outside the twelve-file maintenance bundle. Hash approval
must come through a trusted channel: a manifest is not its own trust anchor.
No defaults, backup creation, service controls, replanning during execution,
or retries. Output failure after dispatch is an uncertain execution outcome.
"""
import argparse
import base64
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

FILES = tuple(sorted((
    'foundation_backfill.py', 'foundation_backfill_execute.py',
    'foundation_backfill_manifest.py', 'foundation_backfill_planner.py',
    'foundation_backfill_validation.py', 'foundation_backfill_synthetic.py',
    'foundation_backfill_rehearsal.py', 'foundation_backfill_rehearsal_scoring.py',
    'foundation_backfill_receipt.py', 'foundation_migrations.py',
    'foundation_schema.py', 'pickem_flask_htmx_tabs.py')))
CONTRACT = 'stage2-git-blob-bundle-v1'


class Refusal(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refusal(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'DUPLICATE_JSON_KEY')
            out[key] = value
        return out
    def constant(value):
        raise Refusal('NONFINITE_JSON')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def path(value, *, directory=False):
    p = Path(value)
    need(p.is_absolute() and not str(p).replace('\\', '/').startswith('//'), 'ABSOLUTE_LOCAL_PATH_REQUIRED')
    for part in (p, *p.parents):
        need(not part.is_symlink() and not getattr(part, 'is_junction', lambda: False)(), 'LINK_REFUSED')
    p = p.resolve(strict=True)
    need(p.is_dir() if directory else p.is_file() and p.stat().st_nlink == 1, 'REGULAR_PATH_REQUIRED')
    return p


def private_directory(value):
    p = path(value, directory=True)
    need(not any((x / '.git').exists() for x in (p, *p.parents)), 'PRIVATE_PATH_OUTSIDE_GIT_REQUIRED')
    if os.name == 'nt':
        # Read ACL only; never change permissions or trust a user-supplied boolean.
        literal = str(p).replace("'", "''")
        command = "$ErrorActionPreference='Stop'; $a=[System.IO.Directory]::GetAccessControl('" + literal + "'); "
        command += "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
        command += "$ok=$a.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -eq $sid; foreach($r in $a.Access){if($r.AccessControlType -eq 'Allow'){"
        command += "$s=$r.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value; "
        command += "if($s -notin @($sid,'S-1-3-4','S-1-5-18','S-1-5-32-544')){$ok=$false}}}; if($ok){'PRIVATE'}else{'REFUSED'}"
        encoded = base64.b64encode(command.encode('utf-16le')).decode()
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                capture_output=True, timeout=15)
        need(result.returncode == 0 and result.stdout.strip() == b'PRIVATE', 'PRIVATE_DIRECTORY_ACL_REQUIRED')
    else:
        need(p.stat().st_uid == os.getuid() and stat.S_IMODE(p.stat().st_mode) & 0o077 == 0,
             'PRIVATE_DIRECTORY_MODE_REQUIRED')
    return p


def build_manifest(repository, expected_head):
    """Read exact Git blobs, never worktree bytes. No output files or network."""
    repo = path(repository, directory=True)
    need(re.fullmatch('[0-9a-f]{40}', expected_head) is not None, 'EXACT_REVISION_REQUIRED')
    def git(*args):
        result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, timeout=30)
        need(result.returncode == 0, 'GIT_READ_FAILED')
        return result.stdout
    need(git('rev-parse', 'HEAD').decode().strip() == expected_head, 'HEAD_MISMATCH')
    records = []
    for name in FILES:
        blob = git('show', expected_head + ':' + name)
        records.append(dict(path=name, git_blob=git('rev-parse', expected_head + ':' + name).decode().strip(),
                            sha256=sha(blob), size=len(blob)))
    return dict(contract=CONTRACT, revision=expected_head, files=records)


def verify_bundle(directory, manifest, expected_sha256, expected_revision):
    need(sha(canonical(manifest)) == expected_sha256, 'BUNDLE_MANIFEST_HASH_MISMATCH')
    need(type(manifest) is dict and set(manifest) == {'contract', 'revision', 'files'} and
         manifest['contract'] == CONTRACT and manifest['revision'] == expected_revision and
         re.fullmatch('[0-9a-f]{40}', expected_revision) is not None, 'BUNDLE_REVISION_MISMATCH')
    root = path(directory, directory=True)
    need(sorted(p.name for p in root.iterdir()) == list(FILES), 'BUNDLE_FILE_SET_MISMATCH')
    need(type(manifest['files']) is list and [r['path'] for r in manifest['files']] == list(FILES),
         'BUNDLE_MANIFEST_FILE_SET_MISMATCH')
    sources = {}
    for record in manifest['files']:
        need(set(record) == {'path', 'git_blob', 'sha256', 'size'}, 'BUNDLE_RECORD_INVALID')
        data = path(root / record['path']).read_bytes()
        blob_id = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        need(len(data) == record['size'] and sha(data) == record['sha256'] and blob_id == record['git_blob'],
             'BUNDLE_FILE_HASH_MISMATCH')
        sources[Path(record['path']).stem] = data
    return root, sources


class VerifiedLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Compile verified bytes, not a subsequent unchecked import from sys.path."""
    def __init__(self, root, sources):
        self.root, self.sources = root, sources

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.sources:
            need(fullname != 'pickem_flask_htmx_tabs', 'APPLICATION_IMPORT_REFUSED')
            return importlib.util.spec_from_file_location(fullname, self.root / (fullname + '.py'), loader=self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        exec(compile(self.sources[module.__name__], module.__file__, 'exec'), module.__dict__)


@contextmanager
def silence():
    """Suppress Python and native fd output, including dependency tracebacks."""
    with open(os.devnull, 'w') as sink:
        saved = [os.dup(n) for n in (1, 2)]
        try:
            for n in (1, 2):
                os.dup2(sink.fileno(), n)
            with redirect_stdout(sink), redirect_stderr(sink):
                yield
        finally:
            for n, fd in zip((1, 2), saved):
                os.dup2(fd, n)
                os.close(fd)


def document(filename, expected):
    need(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected), 'FILE_HASH_REQUIRED')
    data = path(filename).read_bytes()
    need(sha(data) == expected, 'INPUT_FILE_HASH_MISMATCH')
    return strict_json(data)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refusal('ARGUMENTS_REQUIRED_OR_INVALID')


def parser():
    p = Parser(description=__doc__)
    p.add_argument('mode', choices=('bundle-manifest', 'prepare', 'execute'))
    for flag in ('repository', 'revision', 'bundle', 'bundle-manifest', 'bundle-sha256', 'request',
                 'request-sha256', 'private-dir', 'output', 'approved-packet', 'approved-packet-sha256',
                 'confirmation', 'operational-evidence', 'operational-evidence-sha256'):
        p.add_argument('--' + flag)
    p.add_argument('--writers-fenced', action='store_true')
    return p


def run(args):
    private = private_directory(args.private_dir)
    output = Path(args.output)
    need(output.is_absolute() and output.parent == private and not output.exists(), 'EXCLUSIVE_PRIVATE_OUTPUT_REQUIRED')
    # Reserve output before any database action. No overwrite, including dangling links.
    with output.open('xb') as sink:
        if os.name != 'nt':
            os.fchmod(sink.fileno(), 0o600)
        if args.mode == 'bundle-manifest':
            result = build_manifest(args.repository, args.revision)
            summary = dict(status='bundle_manifest_created', sha256=sha(canonical(result)))
        else:
            need(args.writers_fenced, 'WRITER_FENCE_ACK_REQUIRED')
            manifest = strict_json(path(args.bundle_manifest).read_bytes())
            root, sources = verify_bundle(args.bundle, manifest, args.bundle_sha256, args.revision)
            need(not any(n in sys.modules for n in sources), 'FRESH_INTERPRETER_REQUIRED')
            request = document(args.request, args.request_sha256)
            required = {'database_path', 'manifest_path', 'manifest_file_sha256', 'plan_path', 'plan_file_sha256',
                        'expected_manifest_checksum', 'expected_plan_sha256', 'expected_source',
                        'expected_stage1_ledger_sha256', 'expected_schema_sha256', 'checkpoint'}
            need(type(request) is dict and set(request) == required, 'EXPLICIT_REQUEST_FIELDS_REQUIRED')
            target = path(request['database_path'])
            need(target != output, 'OUTPUT_TARGET_COLLISION')
            kwargs = {k: request[k] for k in required if k not in
                      {'manifest_path', 'manifest_file_sha256', 'plan_path', 'plan_file_sha256'}}
            kwargs['manifest'] = document(request['manifest_path'], request['manifest_file_sha256'])
            kwargs['plan'] = document(request['plan_path'], request['plan_file_sha256'])
            if args.mode == 'execute':
                packet = strict_json(path(args.approved_packet).read_bytes())
                need(sha(canonical(packet)) == args.approved_packet_sha256, 'APPROVED_PACKET_HASH_MISMATCH')
                need(args.writers_fenced and args.confirmation == 'EXECUTE_EXACT_REVIEWED_STAGE2_PLAN',
                     'EXPLICIT_EXECUTION_ACK_REQUIRED')
                gates = document(args.operational_evidence, args.operational_evidence_sha256)
                if gates.get('mode') == 'offline_simulation':
                    need(Path(tempfile.gettempdir()).resolve() in target.parents, 'OFFLINE_EVIDENCE_REQUIRES_TEMP_TARGET')
                kwargs.update(approved_packet=packet, approved_packet_sha256=args.approved_packet_sha256,
                              confirmation=args.confirmation, writers_fenced=True, operational_evidence=gates)
            loader = VerifiedLoader(root, sources)
            sys.meta_path.insert(0, loader)
            try:
                api = importlib.import_module('foundation_backfill_execute')
                verify_bundle(root, manifest, args.bundle_sha256, args.revision)
                if args.mode == 'prepare':
                    result = api.prepare_execution(**kwargs)
                    summary = dict(status='prepared_human_approval_required', packet_sha256=api.packet_sha256(result))
                else:
                    result = api.execute_plan(**kwargs)
                    summary = dict(status=result['status'], independent_verification_required=True,
                                   writer_resume_authorized=False)
            finally:
                sys.meta_path.remove(loader)
        data = canonical(result)
        sink.write(data)
        sink.flush()
        os.fsync(sink.fileno())
        return summary


def main(argv=None):
    status = 1
    try:
        with silence():
            need(sys.flags.isolated and sys.dont_write_bytecode, 'ISOLATED_FRESH_PROCESS_REQUIRED')
            args = parser().parse_args(argv)
            summary = run(args)
        status = 0
    except BaseException:
        # Never echo arguments, input text, dependency output or exception strings.
        summary = dict(status='refused_or_outcome_uncertain', automatic_retry=False,
                       independent_verification_required=True)
    print(json.dumps(summary, sort_keys=True))
    return status


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
