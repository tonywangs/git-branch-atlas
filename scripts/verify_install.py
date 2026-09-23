#!/usr/bin/env python3
"""Install into a clean venv and exercise a multi-branch, multi-worktree example.

Bootstraps pinned, hash-verified build wheels from PyPI (or --wheelhouse).
All generated repositories and environments are temporary. No global install.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import venv

ROOT = Path(__file__).resolve().parents[1]
WHEELS = [
    ('https://files.pythonhosted.org/packages/b7/3f/945ef7ab14dc4f9d7f40288d2df998d1837ee0888ec3659c813487572faa/pip-25.2-py3-none-any.whl',
     '6d67a2b4e7f14d8b31b8b52648866fa717f45a1eb70e83002f4331d07e953717'),
    ('https://files.pythonhosted.org/packages/a3/dc/17031897dae0efacfea57dfd3a82fdd2a2aeb58e0ff71b77b87e44edc772/setuptools-80.9.0-py3-none-any.whl',
     '062d34222ad13e0cc312a4c02d73f059e86a4acbfbdea8f8f76b28c99f306922'),
    ('https://files.pythonhosted.org/packages/0b/2c/87f3254fd8ffd29e4c02732eee68a83a1d3c346ae39bc6822dcbcb697f2b/wheel-0.45.1-py3-none-any.whl',
     '708e7481cc80179af0e556bbf0cc00b8444c7321e2700b8d8580231d13017248'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse', type=Path, help='use these exact wheels offline; never download')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='atlas-isolated-') as temp:
        base = Path(temp)
        wheelhouse = args.wheelhouse or base / 'wheels'
        if not args.wheelhouse:
            wheelhouse.mkdir()
        paths = []
        for url, digest in WHEELS:
            path = wheelhouse / url.rsplit('/', 1)[1]
            if not args.wheelhouse:
                with urllib.request.urlopen(url, timeout=60) as response:
                    path.write_bytes(response.read())
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f'wheel hash mismatch: {path.name}')
            paths.append(path)
        target = base / 'venv'
        venv.EnvBuilder(with_pip=False).create(target)
        binaries = target / ('Scripts' if os.name == 'nt' else 'bin')
        python = binaries / ('python.exe' if os.name == 'nt' else 'python')
        env = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'PIP_', 'GIT_'))}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        env['PIP_CONFIG_FILE'] = os.devnull
        env['PYTHONPATH'] = str(paths[0])
        def run(argv, cwd=base, **kwargs):
            result = subprocess.run([str(a) for a in argv], cwd=cwd, env=env,
                                    capture_output=True, text=True, **kwargs)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            return result.stdout.strip()
        run([python, '-m', 'pip', 'install', '--no-index', '--no-deps', *paths])
        del env['PYTHONPATH']
        # Build from a copy so setuptools cannot leave build files in the checkout.
        source = base / 'source'
        source.mkdir()
        for name in ('pyproject.toml', 'README.md'):
            shutil.copy2(ROOT / name, source / name)
        shutil.copytree(ROOT / 'git_branch_atlas', source / 'git_branch_atlas',
                        ignore=shutil.ignore_patterns('__pycache__'))
        run([python, '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-build-isolation', source])
        atlas = binaries / ('git-branch-atlas.exe' if os.name == 'nt' else 'git-branch-atlas')
        module = run([python, '-c', 'import git_branch_atlas; print(git_branch_atlas.__file__)'])
        assert str(target) in module, module
        repo = base / 'example'
        repo.mkdir()
        def git(*args):
            return run(['git', '-C', repo, *args])
        git('init', '-b', 'main')
        git('config', 'user.name', 'Atlas Demo')
        git('config', 'user.email', 'demo@example.invalid')
        git('commit', '--allow-empty', '-m', 'Root')
        git('branch', 'topic')
        linked = base / 'linked café'
        git('worktree', 'add', linked, 'topic')
        run(['git', '-C', linked, 'commit', '--allow-empty', '-m', 'Topic'])
        git('commit', '--allow-empty', '-m', 'Main')
        git('branch', '--set-upstream-to=main', 'topic')
        git('worktree', 'lock', '--reason', 'demo hold', linked)
        summary = json.loads(run([atlas, '--repo', repo, 'summary', '--json']))
        topic = next(branch for branch in summary['branches'] if branch['name'] == 'topic')
        assert (topic['ahead'], topic['behind']) == (1, 1)
        assert len(summary['worktrees']) == 2
        assert next(w for w in summary['worktrees'] if w['path'] == str(linked))['locked']
        comparison = json.loads(run([atlas, '--repo', repo, 'compare', 'main', 'topic', '--json']))
        assert comparison['left']['unique_count'] == comparison['right']['unique_count'] == 1
        assert len(comparison['merge_bases']) == 1
        graph = run([atlas, '--repo', repo, '--all', '--no-color'])
        assert 'Main' in graph and 'Topic' in graph
        # Offline cherry-pick case: the console runs in an unrelated working directory.
        (repo / 'feature.txt').write_text('useful change\n')
        git('add', 'feature.txt')
        git('commit', '-m', 'Feature')
        original = git('rev-parse', 'HEAD')
        run(['git', '-C', linked, 'cherry-pick', original])
        copied = run(['git', '-C', linked, 'rev-parse', 'HEAD'])
        env.update(GIT_ALLOW_PROTOCOL='', GIT_NO_LAZY_FETCH='1',
                   http_proxy='http://127.0.0.1:9', https_proxy='http://127.0.0.1:9')
        patches = json.loads(run([atlas, '--repo', repo, 'patches', 'main', 'topic', '--json']))
        assert patches['complete']
        assert len(patches['matches']) == 1
        assert patches['matches'][0]['left'] == [original]
        assert patches['matches'][0]['right'] == [copied]
        assert 'Matching groups: 1' in run([atlas, '--repo', repo, 'patches', 'main', 'topic'])
        print(json.dumps({'installed_version': run([atlas, '--version']),
                          'python': run([python, '--version']), 'git': git('--version'),
                          'checks': ['isolated installed import', 'console entry point', 'two branches',
                                     'two worktrees', 'locked worktree', 'upstream +1/-1',
                                     'comparison unique counts and merge base', 'legacy graph',
                                     'offline cherry-pick patch group JSON and terminal'],
                          'result': 'passed'}, indent=2))


if __name__ == '__main__':
    main()
