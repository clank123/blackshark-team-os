#!/usr/bin/env python3
"""Check pinned BlackShark releases and merge public files without replacing user data."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import time
import urllib.request
import zipfile

REPO = 'clank123/blackshark-team-os'
UPDATE_URL = f'https://raw.githubusercontent.com/{REPO}/main/UPDATE.json'
TTL = 86400
ROOT_FILES = {'.gitignore', 'AGENTS.md', 'README.md', 'workspace-manifest.json',
              'VERSION', 'UPDATE.json', 'CHANGELOG.md', 'RELEASING.md', '团队接入卡.md', 'LICENSE'}
PUBLIC_DIRS = {'00_workspace', '01_shared_context', '02_store_cards', '04_templates',
               '05_reviews', 'scripts', 'tests', 'upgrade-baselines'}
SKILL_DIRS = {'blackshark-ops-router', 'blackshark-monthly-ops-compiler'}


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?', value):
        raise ValueError('Invalid version')
    parts = tuple(map(int, value.lstrip('v').split('.')))
    return parts + (0,) * (3 - len(parts))


def managed(name):
    p = PurePosixPath(name)
    if (not name or p.is_absolute() or name != p.as_posix() or '\\' in name
            or any(c in name for c in '\r\n\t\x00')
            or any(x in {'.', '..', '.git', '__pycache__'} for x in p.parts)
            or any('.local.' in x for x in p.parts)):
        return False
    return (name in ROOT_FILES or (len(p.parts) > 1 and p.parts[0] in PUBLIC_DIRS)
            or (len(p.parts) > 3 and p.parts[:2] == ('.agents', 'skills') and p.parts[2] in SKILL_DIRS))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def installed(root):
    manifest_file = checked_path(root, 'workspace-manifest.json')
    if not manifest_file.exists():
        # The published v0.1 predates workspace-manifest.json. Require both of its
        # known entrypoints; file-level comparisons still use its official hashes.
        readme = checked_path(root, 'README.md').read_text(encoding='utf-8')
        compiler = checked_path(root, '.agents/skills/blackshark-monthly-ops-compiler/SKILL.md').read_text(encoding='utf-8')
        if (readme.splitlines()[:1] == ['# 黑鲨团队 AI 工作台 v0.1']
                and 'https://github.com/clank123/blackshark-team-os' in readme
                and re.search(r'^name: blackshark-monthly-ops-compiler\s*$', compiler, re.M)
                and not (root / 'VERSION').exists()):
            return '0.1'
        raise ValueError('Workspace identity missing; old v0.1 entrypoints do not match')
    manifest = read_json(manifest_file)
    if manifest.get('workspace') != 'blackshark-team-os':
        raise ValueError('Not a BlackShark workspace')
    current = manifest['release'].lstrip('v')
    version(current)
    return current


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'blackshark-team-os-updater'})
    with urllib.request.urlopen(request, timeout=5) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Download exceeds size limit')
    return data


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.blackshark-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def checked_path(root, relative):
    p = root
    for part in PurePosixPath(relative).parts:
        p = p / part
        if p.is_symlink():
            raise ValueError(f'Symlink conflict: {relative}')
    if p.exists() and not p.is_file():
        raise ValueError(f'Not a regular file: {relative}')
    return p


def check(root, force=False, now=None, fetcher=fetch):
    current = installed(root)
    now = time.time() if now is None else now
    cache = checked_path(root, '.blackshark-update/check.json')
    if cache.exists() and not force:
        try:
            previous = read_json(cache)
            if previous['local_version'] == current and 0 <= now - previous['checked_at'] < TTL:
                return {'status': 'cached', 'current': current}
        except (ValueError, KeyError, TypeError):
            pass
    result = {'status': 'unavailable', 'current': current}
    try:
        data = json.loads(fetcher(UPDATE_URL, 16384))
        latest = data['version']
        notice = data['summary']
        if data.get('workspace') != 'blackshark-team-os' or not isinstance(notice, str) or not 1 <= len(notice) <= 160 or any(ord(c) < 32 for c in notice):
            raise ValueError('Invalid update notice')
        result = {'status': 'update_available' if version(latest) > version(current) else 'current',
                  'current': current, 'latest': latest.lstrip('v'), 'summary': notice,
                  'release_url': f'https://github.com/{REPO}/releases/tag/v{latest.lstrip("v")}'}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        atomic_write(cache, json.dumps({'local_version': current, 'checked_at': now}).encode())
    except OSError:
        pass
    return result


def inventory(data, expected):
    if data.get('workspace') != 'blackshark-team-os' or version(data['version']) != version(expected):
        raise ValueError('Release identity/version mismatch')
    files = data.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('Invalid release inventory')
    for name, sha in files.items():
        if not managed(name) or not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha):
            raise ValueError('Invalid managed path or hash')
    return files


def unpack(data, expected):
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 1500 or sum(e.file_size for e in entries) > 20_000_000:
            raise ValueError('Archive exceeds size limit')
        prefix = None
        for entry in entries:
            p = PurePosixPath(entry.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in entry.filename or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('Unsafe archive entry')
            if not p.parts:
                continue
            if prefix is None:
                prefix = p.parts[0]
            if prefix != p.parts[0]:
                raise ValueError('Multiple archive roots')
            if entry.is_dir():
                continue
            name = '/'.join(p.parts[1:])
            if not name or name in files:
                raise ValueError('Duplicate or invalid archive path')
            files[name] = archive.read(entry)
    release = json.loads(files['release-manifest.json'])
    hashes = inventory(release, expected)
    for name, sha in hashes.items():
        if name not in files or digest(files[name]) != sha:
            raise ValueError(f'Release file checksum mismatch: {name}')
    workspace = json.loads(files['workspace-manifest.json'])
    if workspace.get('workspace') != 'blackshark-team-os' or version(workspace['release']) != version(expected):
        raise ValueError('Workspace version mismatch')
    if version(files['VERSION'].decode().strip()) != version(expected):
        raise ValueError('VERSION mismatch')
    if version(json.loads(files['UPDATE.json'])['version']) != version(expected):
        raise ValueError('Notice version mismatch')
    return release, {n: files[n] for n in hashes}, files['release-manifest.json']


def plan(root, release, incoming, resolutions=None):
    current = installed(root)
    if version(release['version']) <= version(current):
        raise ValueError('Target must be newer than the installed version')
    target = inventory(release, release['version'])
    if set(target) != set(incoming) or any(digest(incoming[n]) != h for n, h in target.items()):
        raise ValueError('Incoming files do not match release inventory')
    manifest_path = checked_path(root, 'release-manifest.json')
    if manifest_path.exists():
        baseline = inventory(read_json(manifest_path), current)
    else:
        name = f'upgrade-baselines/v{current}.json'
        if name not in incoming:
            raise ValueError('Unknown old baseline; use a separate new workspace and preserve the old one')
        baseline = inventory(json.loads(incoming[name]), current)
    merges = {}
    if resolutions is not None:
        if version(resolutions['from']) != version(current) or version(resolutions['to']) != version(release['version']):
            raise ValueError('Merge resolutions are for another version')
        merges = resolutions['files']
        if not isinstance(merges, dict) or any(not managed(n) for n in merges):
            raise ValueError('Invalid merge resolution paths')
    actions, conflicts, preserved, observed, resolved = [], [], [], {}, []
    for name in sorted(set(baseline) | set(target)):
        path = checked_path(root, name)
        local = digest(path.read_bytes()) if path.exists() else None
        before, after = baseline.get(name), target.get(name)
        observed[name] = local
        if local == after:
            continue
        if before == after:
            preserved.append(name)
        elif local == before:
            actions.append({'path': name, 'operation': 'write' if after else 'remove'})
        elif name in merges:
            item = merges[name]
            if not isinstance(item, dict):
                raise ValueError('Invalid merge resolution')
            if item.get('expected_local_sha256') != local or item.get('incoming_sha256') != after:
                raise ValueError(f'Merge resolution no longer matches inputs: {name}')
            source = item['merged_file']
            p = PurePosixPath(source)
            if (p.parts[:2] != ('.blackshark-update', 'merges') or len(p.parts) < 3
                    or '..' in p.parts or source != p.as_posix() or '\\' in source):
                raise ValueError('Merged file must be staged under .blackshark-update/merges/')
            payload = checked_path(root, source).read_bytes()
            if digest(payload) != item['merged_sha256']:
                raise ValueError(f'Merged file changed since review: {name}')
            actions.append({'path': name, 'operation': 'merge', 'source': source, 'sha256': digest(payload)})
            resolved.append(name)
        else:
            conflicts.append(name)
    if set(merges) != set(resolved):
        raise ValueError('Merge resolutions contain stale or non-conflicting entries')
    return {'status': 'conflict' if conflicts else 'ready', 'from': current,
            'to': release['version'], 'actions': actions, 'conflicts': conflicts,
            'preserved_local_changes': preserved, 'resolved_local_changes': resolved, '_observed': observed}


def apply(root, proposal, incoming, manifest_bytes):
    if proposal['status'] != 'ready':
        raise ValueError('Resolve conflicting files before updating')
    for name, expected in proposal['_observed'].items():
        path = checked_path(root, name)
        actual = digest(path.read_bytes()) if path.exists() else None
        if actual != expected:
            raise ValueError(f'File changed after planning: {name}')
    changes = proposal['actions'] + [{'path': 'release-manifest.json', 'operation': 'write'}]
    merged_payloads = {}
    for action in changes:
        if action['operation'] == 'merge':
            payload = checked_path(root, action['source']).read_bytes()
            if digest(payload) != action['sha256']:
                raise ValueError('Merged file changed after planning')
            merged_payloads[action['path']] = payload
    # Validate backup parents too; never follow an alternate local data path.
    receipt_probe = checked_path(root, '.blackshark-update/backups/probe/receipt.json')
    backup_parent = receipt_probe.parent.parent
    backup_parent.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=f'{proposal["from"]}-to-{proposal["to"]}-', dir=backup_parent))
    before = {}
    for action in changes:
        path = checked_path(root, action['path'])
        old = path.read_bytes() if path.exists() else None
        before[action['path']] = old
        if old is not None:
            atomic_write(backup / action['path'], old)
    completed = []
    try:
        for action in changes:
            name = action['path']
            path = checked_path(root, name)
            old = before[name]
            if (path.read_bytes() if path.exists() else None) != old:
                raise ValueError(f'File changed during update: {name}')
            completed.append(name)
            if action['operation'] == 'remove':
                path.unlink()
            else:
                payload = manifest_bytes if name == 'release-manifest.json' else merged_payloads.get(name, incoming.get(name))
                atomic_write(path, payload)
                if path.read_bytes() != payload:
                    raise ValueError('Written file verification failed')
        if version(installed(root)) != version(proposal['to']):
            raise ValueError('Installed version verification failed')
        if version((root/'VERSION').read_text().strip()) != version(proposal['to']) or version(read_json(root/'UPDATE.json')['version']) != version(proposal['to']):
            raise ValueError('Installed version metadata is inconsistent')
        receipt = {k: v for k, v in proposal.items() if not k.startswith('_')}
        receipt.update(status='updated', backup=str(backup), verified_version=installed(root))
        atomic_write(backup / 'receipt.json', json.dumps(receipt, ensure_ascii=False, indent=2).encode())
    except Exception:
        for name in reversed(completed):
            if before[name] is None:
                (root / name).unlink(missing_ok=True)
            else:
                atomic_write(root / name, before[name])
        raise
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, help='Explicit existing workspace for first upgrade from an older ZIP')
    sub = parser.add_subparsers(dest='command', required=True)
    checker = sub.add_parser('check')
    checker.add_argument('--force', action='store_true')
    checker.add_argument('--json', action='store_true')
    updater = sub.add_parser('update')
    updater.add_argument('--version', required=True)
    updater.add_argument('--resolutions', type=Path, help='Reviewed staged merges locked to local and incoming hashes')
    updater.add_argument('--apply', action='store_true', help='Apply an explicitly requested update; default is preview')
    args = parser.parse_args(argv)
    root = args.workspace.resolve() if args.workspace else Path(__file__).resolve().parents[1]
    try:
        if args.command == 'check':
            result = check(root, force=args.force)
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            elif result['status'] == 'update_available':
                print(f'黑鲨工作台有新版 v{result["latest"]}（当前 v{result["current"]}）：{result["summary"]}。说“更新黑鲨工作台到 v{result["latest"]}”即可升级。')
            return 0
        version(args.version)
        target = args.version.lstrip('v')
        payload = fetch(f'https://codeload.github.com/{REPO}/zip/refs/tags/v{target}', 10_000_000)
        release, incoming, raw_manifest = unpack(payload, target)
        resolutions = read_json(args.resolutions) if args.resolutions else None
        proposal = plan(root, release, incoming, resolutions)
        if args.apply and proposal['status'] == 'ready':
            result = apply(root, proposal, incoming, raw_manifest)
        else:
            result = {k: v for k, v in proposal.items() if not k.startswith('_')}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result['status'] == 'conflict' else 0
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        if args.command == 'check':
            if args.json:
                print(json.dumps({'status': 'unavailable'}, ensure_ascii=False))
            return 0
        print(json.dumps({'status': 'error', 'message': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
