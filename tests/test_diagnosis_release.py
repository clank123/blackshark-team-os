"""Verify the versioned diagnosis payload and its upgrade/conflict boundary."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from test_workspace_update import u, encoded
from test_deliverable_release import package_bytes

ROOT = Path(__file__).resolve().parents[1]
METHOD = '01_shared_context/store-diagnosis/'

class DiagnosisReleaseTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT/'release-manifest.json').read_bytes()
        self.release = json.loads(self.raw)
        self.payload = {n: (ROOT/n).read_bytes() for n in self.release['files']}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.local = Path(self.tmp.name)

    def old(self):
        payload = {k: v for k, v in self.payload.items() if not k.startswith(METHOD)}
        manifest = json.loads(payload['workspace-manifest.json'])
        manifest['release'] = 'v0.6.1'
        payload['workspace-manifest.json'] = encoded(manifest)
        payload['VERSION'] = b'0.6.1\n'
        notice = json.loads(payload['UPDATE.json']); notice['version'] = '0.6.1'
        payload['UPDATE.json'] = encoded(notice)
        for n, data in payload.items():
            p = self.local/n; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
        baseline = dict(workspace='blackshark-team-os',version='0.6.1',files={n:u.digest(b) for n,b in payload.items()})
        (self.local/'release-manifest.json').write_bytes(encoded(baseline))
        p = self.local/'03_my_workspace/04_personal/current.md'
        p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'private current facts')

    def snapshot(self):
        return {p.relative_to(self.local).as_posix():p.read_bytes() for p in self.local.rglob('*') if p.is_file()}

    def test_shared_methods_match_pinned_source_checksums(self):
        for name in ['store-diagnosis','monthly-planning','action-planning']:
            root = ROOT/'01_shared_context'/name
            source = json.loads((root/'SOURCE.json').read_text())
            self.assertFalse(source['local_changes'])
            self.assertEqual(source['version'],'v0.3.0')
            for rel, origin in source['files'].items():
                p = root/rel
                self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), origin['sha256'])
                self.assertIn(p.relative_to(ROOT).as_posix(), self.release['files'])
        changed = dict(self.payload)
        changed[METHOD+'references/data-contract.md'] += b'altered'
        with self.assertRaisesRegex(ValueError,'checksum mismatch'):
            u.unpack(package_bytes(changed,self.raw),self.release['version'])

    def test_new_method_installs_without_private_or_unrelated_local_loss(self):
        self.old()
        existing = self.local/'04_templates/运营推进表模板/schema.json'
        existing.write_bytes(existing.read_bytes()+b'\n')
        before = self.snapshot()
        proposal = u.plan(self.local,self.release,self.payload)
        self.assertEqual(proposal['status'],'ready')
        self.assertEqual(self.snapshot(),before)
        receipt = u.apply(self.local,proposal,self.payload,self.raw)
        self.assertEqual(receipt['verified_version'],self.release['version'])
        self.assertEqual((self.local/(METHOD+'SKILL.md')).read_bytes(),self.payload[METHOD+'SKILL.md'])
        for rel in ['03_my_workspace/04_personal/current.md','04_templates/运营推进表模板/schema.json']:
            self.assertEqual((self.local/rel).read_bytes(),before[rel])

    def test_same_named_member_method_blocks_all_writes(self):
        self.old()
        p = self.local/(METHOD+'SKILL.md')
        p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'member method')
        before = self.snapshot()
        proposal = u.plan(self.local,self.release,self.payload)
        self.assertIn(METHOD+'SKILL.md',proposal['conflicts'])
        with self.assertRaises(ValueError):
            u.apply(self.local,proposal,self.payload,self.raw)
        self.assertEqual(self.snapshot(),before)
