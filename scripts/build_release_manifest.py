#!/usr/bin/env python3
"""Build/check the exact public-file inventory before tagging a workspace release."""
import argparse
import json
from pathlib import Path
import subprocess
from workspace_update import digest, installed, managed, version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    current = installed(root)
    notice = json.loads((root/'UPDATE.json').read_text(encoding='utf-8'))
    assert version((root/'VERSION').read_text().strip()) == version(current), 'VERSION differs'
    assert notice['workspace'] == 'blackshark-team-os' and version(notice['version']) == version(current), 'UPDATE.json differs'
    expected_url = f'https://github.com/clank123/blackshark-team-os/releases/tag/v{current}'
    assert notice['release_url'] == expected_url, 'Release URL differs'
    assert json.loads((root/'workspace-manifest.json').read_text(encoding='utf-8'))['release_url'] == expected_url
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root).decode('utf-8').split('\0')
    files = {}
    for name in sorted(set(n for n in names if n and managed(n))):
        path = root / name
        assert path.is_file() and not path.is_symlink(), f'Not a regular public file: {name}'
        assert not any(p.is_symlink() for p in path.parents if p != root.parent), f'Symlink parent: {name}'
        files[name] = digest(path.read_bytes())
    body = json.dumps({'schema_version': 1, 'workspace': 'blackshark-team-os', 'version': current, 'files': files}, ensure_ascii=False, indent=2) + '\n'
    target = root/'release-manifest.json'
    if args.check:
        assert target.read_text(encoding='utf-8') == body, 'Release manifest is stale; rebuild it'
        print(f'Checked {len(files)} public files for v{current}')
    else:
        target.write_text(body, encoding='utf-8')
        print(f'Built {len(files)} public files for v{current}')


if __name__ == '__main__':
    main()
