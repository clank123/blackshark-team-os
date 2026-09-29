import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('updater', ROOT / 'scripts/workspace_update.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


def fixture(root):
    old = {'README.md': b'original', 'workspace-manifest.json': encoded({'workspace': 'blackshark-team-os', 'release': 'v0.3'})}
    for name, value in old.items():
        (root / name).write_bytes(value)
    for name in ['03_my_workspace/01_projects/private.md', '00_workspace/工作台地图.local.md', '03_projects/current.md']:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'keep private data')
    baseline = {'workspace': 'blackshark-team-os', 'version': '0.3', 'files': {n: u.digest(b) for n, b in old.items()}}
    incoming = {'README.md': b'new public version',
                'workspace-manifest.json': encoded({'workspace': 'blackshark-team-os', 'release': 'v0.4.0'}),
                'VERSION': b'0.4.0\n', 'UPDATE.json': encoded({'version': '0.4.0'}),
                'scripts/new.py': b'# public helper', 'upgrade-baselines/v0.3.json': encoded(baseline)}
    release = {'workspace': 'blackshark-team-os', 'version': '0.4.0', 'files': {n: u.digest(b) for n, b in incoming.items()}}
    return release, incoming, encoded(release)


def archive(incoming, raw):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name, value in {**incoming, 'release-manifest.json': raw}.items():
            z.writestr('blackshark-team-os-v0.4.0/' + name, value)
    return out.getvalue()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release, self.incoming, self.raw = fixture(self.root)

    def test_numeric_versions_and_legacy_version(self):
        self.assertGreater(u.version('0.10.0'), u.version('v0.9.9'))
        self.assertEqual(u.version('v0.3'), (0, 3, 0))
        for bad in ['main', 'v0.4.0;whoami', '../0.4', '01.2.3']:
            with self.assertRaises(ValueError):
                u.version(bad)

    def test_one_check_per_day_and_failures_do_not_interrupt(self):
        calls = []
        def network(url, limit):
            calls.append(url)
            return encoded({'workspace': 'blackshark-team-os', 'version': '0.4.0', 'summary': '更清楚地拆分任务'})
        self.assertEqual(u.check(self.root, now=100, fetcher=network)['status'], 'update_available')
        self.assertEqual(u.check(self.root, now=101, fetcher=network)['status'], 'cached')
        self.assertEqual(len(calls), 1)
        self.assertEqual(u.check(self.root, now=101, force=True, fetcher=network)['latest'], '0.4.0')
        def broken(*args):
            raise OSError('offline')
        self.assertEqual(u.check(self.root, force=True, fetcher=broken)['status'], 'unavailable')

    def test_changed_local_version_does_not_reuse_stale_cache(self):
        notice = lambda *_: encoded({'workspace': 'blackshark-team-os', 'version': '0.4.0', 'summary': '更新'})
        u.check(self.root, now=100, fetcher=notice)
        (self.root / 'workspace-manifest.json').write_bytes(self.incoming['workspace-manifest.json'])
        self.assertEqual(u.check(self.root, now=101, fetcher=notice)['status'], 'current')

    def test_bad_notice_is_quiet(self):
        for data in [b'not-json', encoded({'workspace': 'wrong', 'version': '9.0.0', 'summary': 'x'}), encoded({'workspace': 'blackshark-team-os', 'version': '9.0.0', 'summary': 'a\nb'})]:
            self.assertEqual(u.check(self.root, force=True, fetcher=lambda *_: data)['status'], 'unavailable')

    def test_preview_writes_no_public_or_personal_file(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = u.plan(self.root, self.release, self.incoming)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_update_preserves_private_data_and_backs_up_public_files(self):
        result = u.apply(self.root, u.plan(self.root, self.release, self.incoming), self.incoming, self.raw)
        self.assertEqual(result['verified_version'], '0.4.0')
        self.assertEqual((self.root / 'README.md').read_bytes(), self.incoming['README.md'])
        self.assertEqual((Path(result['backup']) / 'README.md').read_bytes(), b'original')
        for p in [self.root/'03_my_workspace/01_projects/private.md', self.root/'00_workspace/工作台地图.local.md', self.root/'03_projects/current.md']:
            self.assertEqual(p.read_bytes(), b'keep private data')

    def test_local_edit_conflict_causes_no_write(self):
        (self.root/'README.md').write_bytes(b'my edits')
        plan = u.plan(self.root, self.release, self.incoming)
        self.assertEqual(plan['conflicts'], ['README.md'])
        with self.assertRaises(ValueError):
            u.apply(self.root, plan, self.incoming, self.raw)
        self.assertEqual(u.installed(self.root), '0.3')
        self.assertFalse((self.root/'.blackshark-update').exists())

    def test_unrelated_local_edit_is_retained(self):
        self.incoming['README.md'] = b'original'
        self.release['files']['README.md'] = u.digest(b'original')
        (self.root/'README.md').write_bytes(b'my edits')
        plan = u.plan(self.root, self.release, self.incoming)
        self.assertEqual(plan['status'], 'ready')
        self.assertIn('README.md', plan['preserved_local_changes'])
        u.apply(self.root, plan, self.incoming, encoded(self.release))
        self.assertEqual((self.root/'README.md').read_bytes(), b'my edits')

    def test_second_update_uses_installed_manifest(self):
        u.apply(self.root, u.plan(self.root, self.release, self.incoming), self.incoming, self.raw)
        self.incoming['VERSION'] = b'0.4.1'
        self.incoming['workspace-manifest.json'] = encoded({'workspace':'blackshark-team-os','release':'v0.4.1'})
        self.incoming['UPDATE.json'] = encoded({'version':'0.4.1'})
        release = {'workspace':'blackshark-team-os','version':'0.4.1','files':{n:u.digest(b) for n,b in self.incoming.items()}}
        result = u.apply(self.root, u.plan(self.root, release, self.incoming), self.incoming, encoded(release))
        self.assertEqual(result['verified_version'], '0.4.1')

    def test_personal_and_traversal_paths_rejected(self):
        for name in ['../leak', '/tmp/leak', '.git/config', '03_my_workspace/private.md', '00_workspace/工作台地图.local.md', '03_projects/current.md']:
            bad = dict(self.release, files={name: u.digest(b'x')})
            with self.assertRaises(ValueError):
                u.inventory(bad, '0.4.0')

    def test_symlink_does_not_escape_workspace(self):
        (self.root/'README.md').unlink()
        (self.root/'README.md').symlink_to(self.root/'03_projects/current.md')
        with self.assertRaises(ValueError):
            u.plan(self.root, self.release, self.incoming)
        self.assertEqual((self.root/'03_projects/current.md').read_bytes(), b'keep private data')

    def test_parent_symlink_is_rejected(self):
        (self.root/'scripts').symlink_to(self.root/'03_projects', target_is_directory=True)
        with self.assertRaises(ValueError):
            u.plan(self.root, self.release, self.incoming)

    def test_concurrent_change_is_not_overwritten(self):
        plan = u.plan(self.root, self.release, self.incoming)
        (self.root/'README.md').write_bytes(b'concurrent edit')
        with self.assertRaises(ValueError):
            u.apply(self.root, plan, self.incoming, self.raw)
        self.assertEqual((self.root/'README.md').read_bytes(), b'concurrent edit')

    def test_partial_write_failure_rolls_back(self):
        original = u.atomic_write
        def fail_once(path, data):
            if path == self.root/'VERSION':
                raise OSError('simulated disk failure')
            return original(path,data)
        with patch.object(u, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                u.apply(self.root, u.plan(self.root, self.release, self.incoming), self.incoming, self.raw)
        self.assertEqual(u.installed(self.root), '0.3')
        self.assertEqual((self.root/'README.md').read_bytes(), b'original')
        self.assertFalse((self.root/'VERSION').exists())

    def test_archive_checksums_and_version(self):
        release, data, _ = u.unpack(archive(self.incoming, self.raw), '0.4.0')
        self.assertEqual(data, self.incoming)
        tampered = dict(self.incoming, **{'README.md': b'tampered'})
        with self.assertRaises(ValueError):
            u.unpack(archive(tampered, self.raw), '0.4.0')
        with self.assertRaises(ValueError):
            u.unpack(archive(self.incoming, self.raw), '0.5.0')

    def test_unknown_baseline_and_downgrade_rejected(self):
        (self.root/'workspace-manifest.json').write_bytes(encoded({'workspace':'blackshark-team-os','release':'v0.2'}))
        with self.assertRaises(ValueError):
            u.plan(self.root,self.release,self.incoming)
        (self.root/'workspace-manifest.json').write_bytes(self.incoming['workspace-manifest.json'])
        with self.assertRaises(ValueError):
            u.plan(self.root,self.release,self.incoming)

    def staged_resolution(self, name, data):
        source = '.blackshark-update/merges/' + name
        p = self.root / source
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {'from':'0.3','to':'0.4.0','files':{name:{
            'expected_local_sha256':u.digest((self.root/name).read_bytes()),
            'incoming_sha256':self.release['files'][name],
            'merged_file':source, 'merged_sha256':u.digest(data)}}}

    def test_reviewed_merge_keeps_local_notes_and_advances_version(self):
        (self.root/'README.md').write_bytes(b'original + local notes')
        merged=b'new public version + local notes'
        resolution=self.staged_resolution('README.md',merged)
        proposal=u.plan(self.root,self.release,self.incoming,resolution)
        result=u.apply(self.root,proposal,self.incoming,self.raw)
        self.assertEqual(result['resolved_local_changes'],['README.md'])
        self.assertEqual((self.root/'README.md').read_bytes(),merged)
        self.assertEqual(u.installed(self.root),'0.4.0')

    def test_staged_metadata_merge_does_not_advance_version_early(self):
        name='workspace-manifest.json'
        local=json.loads((self.root/name).read_bytes());local['local_setting']='keep'
        (self.root/name).write_bytes(encoded(local))
        merged=json.loads(self.incoming[name]);merged['local_setting']='keep'
        resolution=self.staged_resolution(name,encoded(merged))
        proposal=u.plan(self.root,self.release,self.incoming,resolution)
        self.assertEqual(u.installed(self.root),'0.3')
        u.apply(self.root,proposal,self.incoming,self.raw)
        self.assertEqual(u.installed(self.root),'0.4.0')
        self.assertEqual(u.read_json(self.root/name)['local_setting'],'keep')

    def test_stale_merge_inputs_and_changed_staged_file_are_rejected(self):
        (self.root/'README.md').write_bytes(b'local notes')
        resolution=self.staged_resolution('README.md',b'new + notes')
        proposal=u.plan(self.root,self.release,self.incoming,resolution)
        (self.root/resolution['files']['README.md']['merged_file']).write_bytes(b'changed after review')
        with self.assertRaises(ValueError):
            u.apply(self.root,proposal,self.incoming,self.raw)
        with self.assertRaises(ValueError):
            u.plan(self.root,self.release,self.incoming,resolution)
        self.assertEqual(u.installed(self.root),'0.3')

    def test_receipt_failure_restores_original_files(self):
        original=u.atomic_write
        def failure(path,data):
            if path.name=='receipt.json':
                raise OSError('receipt failed')
            return original(path,data)
        with patch.object(u,'atomic_write',side_effect=failure):
            with self.assertRaises(OSError):
                u.apply(self.root,u.plan(self.root,self.release,self.incoming),self.incoming,self.raw)
        self.assertEqual(u.installed(self.root),'0.3')
        self.assertEqual((self.root/'README.md').read_bytes(),b'original')

    def test_v01_identity_without_manifest(self):
        (self.root/'workspace-manifest.json').unlink()
        (self.root/'README.md').write_text('# 黑鲨团队 AI 工作台 v0.1\nhttps://github.com/clank123/blackshark-team-os')
        p=self.root/'.agents/skills/blackshark-monthly-ops-compiler/SKILL.md'
        p.parent.mkdir(parents=True);p.write_text('---\nname: blackshark-monthly-ops-compiler\n---\n')
        self.assertEqual(u.installed(self.root),'0.1')
        p.write_text('another skill')
        with self.assertRaises(ValueError):
            u.installed(self.root)


if __name__ == '__main__':
    unittest.main()
