"""Check the real package payload and updater contract for shared assessment rules.

The old workspace below is deliberately synthetic; this does not claim a public
release download, team installation, or model behavioral acceptance.
"""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from test_workspace_update import u, encoded

ROOT = Path(__file__).resolve().parents[1]
RULE = '01_shared_context/关键产出验收与跟进.md'
ENTRYPOINTS = (
    'AGENTS.md',
    '.agents/skills/blackshark-ops-router/SKILL.md',
    '.agents/skills/blackshark-monthly-ops-compiler/SKILL.md',
)
PROTECTED = (
    '00_workspace/工作台地图.local.md',
    '03_my_workspace/00_inbox/录音原文.md',
    '03_my_workspace/01_projects/已有项目.md',
    '03_my_workspace/02_outputs/已有产物.md',
    '03_my_workspace/03_reviews/本人确认.md',
    '03_my_workspace/04_personal/stores/当前记录.md',
    '03_projects/旧项目.md',
    '05_upstream_candidates/本地候选.md',
)


def package_bytes(payload, manifest):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in {**payload, 'release-manifest.json': manifest}.items():
            archive.writestr('blackshark-team-os/' + name, content)
    return result.getvalue()


class DeliverableReleaseTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT / 'release-manifest.json').read_bytes()
        self.release = json.loads(self.raw)
        self.payload = {name: (ROOT / name).read_bytes() for name in self.release['files']}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name)

    def old_workspace(self):
        old = dict(self.payload)
        del old[RULE]
        manifest = json.loads(old['workspace-manifest.json'])
        manifest['release'] = 'v0.6.0'
        old['workspace-manifest.json'] = encoded(manifest)
        old['VERSION'] = b'0.6.0\n'
        notice = json.loads(old['UPDATE.json'])
        notice['version'] = '0.6.0'
        old['UPDATE.json'] = encoded(notice)
        for name, content in old.items():
            path = self.local / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        baseline = {'workspace': 'blackshark-team-os', 'version': '0.6.0',
                    'files': {name: u.digest(content) for name, content in old.items()}}
        (self.local / 'release-manifest.json').write_bytes(encoded(baseline))
        for name in PROTECTED:
            path = self.local / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('private fixture: ' + name).encode())

    def snapshot(self):
        return {str(path.relative_to(self.local)): path.read_bytes()
                for path in self.local.rglob('*') if path.is_file()}

    def test_existing_entrypoints_resolve_one_managed_shared_rule(self):
        self.assertIn(RULE, self.release['files'])
        self.assertEqual(u.digest(self.payload[RULE]), self.release['files'][RULE])
        for entrypoint in ENTRYPOINTS:
            with self.subTest(entrypoint=entrypoint):
                # Check the dependency edge, not instructional prose.
                self.assertIn('`' + RULE + '`', (ROOT / entrypoint).read_text())
        self.assertEqual(
            {path.parent.name for path in (ROOT / '.agents/skills').glob('*/SKILL.md')},
            {'blackshark-ops-router', 'blackshark-monthly-ops-compiler'},
        )
        for name in PROTECTED:
            self.assertNotIn(name, self.release['files'])
            self.assertFalse(u.managed(name))

    def test_real_release_archive_roundtrips_and_rejects_changed_rule(self):
        release, payload, raw = u.unpack(
            package_bytes(self.payload, self.raw), self.release['version'])
        self.assertEqual(release, self.release)
        self.assertEqual(payload, self.payload)
        self.assertEqual(raw, self.raw)
        changed = dict(self.payload)
        changed[RULE] += b'changed after packaging'
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            u.unpack(package_bytes(changed, self.raw), self.release['version'])

    def test_real_payload_adds_rule_and_preserves_private_and_local_changes(self):
        self.old_workspace()
        local_template = '04_templates/运营推进表模板/schema.json'
        schema = json.loads((self.local / local_template).read_bytes())
        schema['local_custom_field'] = 'keep existing team field'
        (self.local / local_template).write_bytes(encoded(schema))
        before = self.snapshot()
        proposal = u.plan(self.local, self.release, self.payload)
        self.assertEqual(proposal['status'], 'ready')
        self.assertIn(local_template, proposal['preserved_local_changes'])
        self.assertIn({'path': RULE, 'operation': 'write'}, proposal['actions'])
        self.assertEqual(self.snapshot(), before, 'preview must not write')
        receipt = u.apply(self.local, proposal, self.payload, self.raw)
        self.assertEqual(receipt['verified_version'], self.release['version'])
        self.assertEqual((self.local / RULE).read_bytes(), self.payload[RULE])
        for name in (*PROTECTED, local_template):
            with self.subTest(path=name):
                self.assertEqual((self.local / name).read_bytes(), before[name])

    def test_same_named_local_rule_blocks_all_writes(self):
        self.old_workspace()
        (self.local / RULE).write_bytes('existing local method'.encode())
        before = self.snapshot()
        proposal = u.plan(self.local, self.release, self.payload)
        self.assertEqual(proposal['conflicts'], [RULE])
        with self.assertRaises(ValueError):
            u.apply(self.local, proposal, self.payload, self.raw)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
