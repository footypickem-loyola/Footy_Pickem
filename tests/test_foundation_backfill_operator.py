"""Offline wrapper tests: exact Git blobs and disposable synthetic inputs only."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import foundation_backfill_operator as operator
import foundation_backfill_execute as execute
import test_foundation_backfill_execute as fixtures

ROOT = Path(__file__).resolve().parents[1]
# Qualify the checked-out commit, not the parent that predates this harness.
# Resolve once so blob extraction and every verification share one exact revision.
# Bundle contents still come exclusively from Git blobs, never worktree bytes.
HEAD = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()


class OperatorTests(unittest.TestCase):
    setUp = fixtures.ExecuteTests.setUp
    manifest = fixtures.ExecuteTests.manifest
    row = fixtures.ExecuteTests.row
    plan = fixtures.ExecuteTests.plan
    change = fixtures.ExecuteTests.change
    prepare = fixtures.ExecuteTests.prepare

    def configure(self):
        args = self.prepare()
        root = Path(self.temp.name)
        if os.name != 'nt':
            os.chmod(root, 0o700)
        bundle = root / 'code'
        bundle.mkdir()
        self.bundle_doc = operator.build_manifest(ROOT, HEAD)
        for name in operator.FILES:
            (bundle / name).write_bytes(subprocess.check_output(['git', 'show', HEAD + ':' + name], cwd=ROOT))
        def save(name, value):
            p = root / name
            p.write_bytes(operator.canonical(value))
            return str(p), operator.sha(p.read_bytes())
        manifest_path, manifest_hash = save('manifest.json', args['manifest'])
        plan_path, plan_hash = save('plan.json', args['plan'])
        bundle_path, _ = save('bundle.json', self.bundle_doc)
        packet_path, _ = save('packet.json', args['approved_packet'])
        gates_path, gates_hash = save('gates.json', args['operational_evidence'])
        self.request = {k: str(v) if isinstance(v, Path) else v for k, v in args.items() if k in
                        ('database_path', 'expected_manifest_checksum', 'expected_plan_sha256', 'expected_source',
                         'expected_stage1_ledger_sha256', 'expected_schema_sha256', 'checkpoint')}
        self.request.update(manifest_path=manifest_path, manifest_file_sha256=manifest_hash,
                            plan_path=plan_path, plan_file_sha256=plan_hash)
        request_path, request_hash = save('request.json', self.request)
        self.base = ['--bundle', str(bundle), '--bundle-manifest', bundle_path,
                     '--bundle-sha256', operator.sha(operator.canonical(self.bundle_doc)), '--revision', HEAD,
                     '--request', request_path, '--request-sha256', request_hash,
                     '--private-dir', str(root)]
        self.execution = ['--approved-packet', packet_path, '--approved-packet-sha256', args['approved_packet_sha256'],
                          '--confirmation', execute.CONFIRMATION, '--writers-fenced',
                          '--operational-evidence', gates_path, '--operational-evidence-sha256', gates_hash]
        self.args, self.bundle = args, bundle
        return args

    def invoke(self, mode, extra=(), output='report.json'):
        return subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'foundation_backfill_operator.py'),
                               mode, *self.base, *(['--writers-fenced'] if mode == 'prepare' else []), '--output', str(Path(self.temp.name) / output), *extra],
                              capture_output=True, text=True, timeout=45, cwd=self.temp.name)

    def request_change(self, **changes):
        self.request.update(changes)
        p = Path(self.temp.name) / 'request.json'
        p.write_bytes(operator.canonical(self.request))
        self.base[self.base.index('--request-sha256') + 1] = operator.sha(p.read_bytes())

    def test_prepare_then_separately_approved_execute(self):
        self.configure()
        before = operator.sha(self.target.read_bytes())
        prepared = self.invoke('prepare', output='prepared.json')
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        self.assertEqual(before, operator.sha(self.target.read_bytes()))
        self.assertEqual(json.loads(prepared.stdout)['packet_sha256'], self.args['approved_packet_sha256'])
        result = self.invoke('execute', self.execution)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'applied')
        self.assertFalse(json.loads(result.stdout)['writer_resume_authorized'])
        with closing(execute._connect(self.target)) as db:
            self.assertEqual(execute.receipt.verify_state(db, **{k:self.args[k] for k in
                ('manifest','plan','expected_manifest_checksum','expected_plan_sha256')})['status'], 'stage2_complete')

    def test_bundle_manifest_is_deterministic_git_blob_not_worktree_bytes(self):
        self.configure()
        self.assertEqual(self.bundle_doc, operator.build_manifest(ROOT, HEAD))
        root, sources = operator.verify_bundle(self.bundle, self.bundle_doc,
            operator.sha(operator.canonical(self.bundle_doc)), HEAD)
        for record in self.bundle_doc['files']:
            self.assertEqual(operator.sha(sources[Path(record['path']).stem]), record['sha256'])
        with self.assertRaises(operator.Refusal):
            operator.build_manifest(ROOT, '0' * 40)

    def test_stale_checkout_revision_and_manifest_refuse(self):
        self.configure()
        stale_head = subprocess.check_output(
            ['git', 'rev-parse', HEAD + '^'], cwd=ROOT, text=True).strip()
        self.assertNotEqual(stale_head, HEAD)
        with self.assertRaisesRegex(operator.Refusal, '^HEAD_MISMATCH$'):
            operator.build_manifest(ROOT, stale_head)
        stale_manifest = deepcopy(self.bundle_doc)
        stale_manifest['revision'] = stale_head
        # A changed manifest cannot reuse the approved manifest checksum.
        with self.assertRaisesRegex(operator.Refusal, '^BUNDLE_MANIFEST_HASH_MISMATCH$'):
            operator.verify_bundle(self.bundle, stale_manifest,
                operator.sha(operator.canonical(self.bundle_doc)), HEAD)
        # Rehashing a stale manifest cannot make its revision current.
        with self.assertRaisesRegex(operator.Refusal, '^BUNDLE_REVISION_MISMATCH$'):
            operator.verify_bundle(self.bundle, stale_manifest,
                operator.sha(operator.canonical(stale_manifest)), HEAD)

    def test_missing_extra_modified_and_revision_mismatched_bundle_refuse(self):
        self.configure()
        original = (self.bundle / operator.FILES[0]).read_bytes()
        for kind in ('missing','extra','modified','revision'):
            with self.subTest(kind=kind):
                expected = HEAD
                if kind == 'missing': (self.bundle / operator.FILES[0]).unlink()
                if kind == 'extra': (self.bundle / 'unreviewed.py').write_text('raise RuntimeError()')
                if kind == 'modified': (self.bundle / operator.FILES[0]).write_bytes(original + b'\n')
                if kind == 'revision': expected = '0' * 40
                with self.assertRaises(operator.Refusal):
                    operator.verify_bundle(self.bundle,self.bundle_doc,operator.sha(operator.canonical(self.bundle_doc)),expected)
                (self.bundle / operator.FILES[0]).write_bytes(original)
                if kind == 'extra': (self.bundle / 'unreviewed.py').unlink()

    def test_missing_explicit_database_refuses(self):
        self.configure()
        self.request_change(database_path='relative.db')
        p = self.invoke('prepare')
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((Path(self.temp.name)/'relative.db').exists())

    def test_wrong_manifest_plan_checkpoint_source_stage1_schema_refuse(self):
        self.configure()
        original = deepcopy(self.request)
        before = operator.sha(self.target.read_bytes())
        for i, changes in enumerate(({'manifest_file_sha256':'0'*64}, {'plan_file_sha256':'0'*64},
                {'checkpoint':dict(original['checkpoint'],sha256='0'*64)},
                {'expected_source':dict(original['expected_source'],data_sha256='0'*64)},
                {'expected_stage1_ledger_sha256':'0'*64}, {'expected_schema_sha256':'0'*64})):
            self.request = deepcopy(original)
            self.request_change(**changes)
            result = self.invoke('prepare', output=f'refused-{i}.json')
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(before,operator.sha(self.target.read_bytes()))

    def test_confirmation_fence_packet_and_fresh_gates_required(self):
        self.configure()
        before = operator.sha(self.target.read_bytes())
        for i, flag in enumerate(('--confirmation','--writers-fenced','--approved-packet-sha256','--operational-evidence-sha256')):
            extra = list(self.execution)
            n = extra.index(flag)
            del extra[n:n + (1 if flag == '--writers-fenced' else 2)]
            self.assertNotEqual(self.invoke('execute',extra,output=f'refused-{i}.json').returncode,0)
        gates = Path(self.temp.name)/'gates.json'
        value=json.loads(gates.read_bytes());value['observed_at']='2000-01-01T00:00:00+00:00'
        gates.write_bytes(operator.canonical(value))
        self.execution[self.execution.index('--operational-evidence-sha256')+1]=operator.sha(gates.read_bytes())
        self.assertNotEqual(self.invoke('execute',self.execution,output='stale.json').returncode,0)
        self.assertEqual(before,operator.sha(self.target.read_bytes()))

    def test_bundle_checked_before_import_and_errors_sanitized(self):
        self.configure()
        secret = 'PRIVATE-CANARY@example.invalid'
        p = self.bundle/'foundation_backfill_execute.py'
        p.write_text("print('"+secret+"'); raise RuntimeError('"+secret+"')")
        result = self.invoke('execute',self.execution)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn(secret,result.stdout+result.stderr)
        self.assertEqual(result.stderr,'')
        self.assertEqual(set(json.loads(result.stdout)),{'status','automatic_retry','independent_verification_required'})

    def test_duplicate_json_and_existing_output_refused(self):
        self.configure()
        output=Path(self.temp.name)/'report.json';output.write_bytes(b'keep')
        self.assertNotEqual(self.invoke('prepare').returncode,0)
        self.assertEqual(output.read_bytes(),b'keep')
        with self.assertRaises(operator.Refusal):operator.strict_json('{"secret":1,"secret":2}')

    def test_raw_exceptions_python_and_native_output_are_suppressed(self):
        secret = 'PRIVATE-OUTPUT-CANARY@example.invalid'
        script = "import sys,os;sys.path.insert(0,sys.argv.pop(1));import foundation_backfill_operator as op\n"
        script += "def bad(args):\n print('"+secret+"');os.write(2,b'"+secret+"');raise RuntimeError('"+secret+"')\n"
        script += "op.run=bad;raise SystemExit(op.main(['prepare']))"
        result = subprocess.run([sys.executable,'-I','-B','-c',script,str(ROOT)],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        self.assertNotIn(secret,result.stdout+result.stderr)
        self.assertEqual(result.stderr,'')
        self.assertFalse(json.loads(result.stdout)['automatic_retry'])

    def test_prepare_requires_fence_ack_and_private_output(self):
        self.configure()
        command=[sys.executable,'-I','-B',str(ROOT/'foundation_backfill_operator.py'),'prepare',
                 *self.base,'--output',str(Path(self.temp.name)/'unfenced.json')]
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        with self.assertRaises(operator.Refusal):operator.private_directory(ROOT)

    def test_application_import_is_not_allowed_by_verified_loader(self):
        self.configure()
        loader=operator.VerifiedLoader(self.bundle,{'pickem_flask_htmx_tabs':b'raise RuntimeError()'})
        with self.assertRaises(operator.Refusal):loader.find_spec('pickem_flask_htmx_tabs')


if __name__=='__main__':unittest.main()
