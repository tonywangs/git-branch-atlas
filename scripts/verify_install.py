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
import re
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
        # Documented before/after rebase review in a second, offline repository.
        repo = base / 'series-example'
        repo.mkdir()
        git('init', '-b', 'main')
        git('config', 'user.name', 'Atlas Demo')
        git('config', 'user.email', 'demo@example.invalid')
        git('commit', '--allow-empty', '-m', 'Root')
        git('branch', 'old-base')
        git('checkout', '-b', 'topic')
        (repo / 'exact.txt').write_text('exact change\n')
        git('add', 'exact.txt')
        git('commit', '-m', 'Exact')
        (repo / 'edited.txt').write_text('alpha\nbeta\nold\n')
        git('add', 'edited.txt')
        git('commit', '-m', 'Edited')
        git('branch', 'before')
        git('checkout', 'main')
        (repo / 'base.txt').write_text('new base\n')
        git('add', 'base.txt')
        git('commit', '-m', 'Advance base')
        git('rebase', '--onto', 'main', 'old-base', 'topic')
        (repo / 'edited.txt').write_text('alpha\nbeta\nnew\n')
        git('add', 'edited.txt')
        git('commit', '--amend', '--no-edit')
        series = json.loads(run([atlas, '--repo', repo, 'series',
                                 'old-base', 'before', 'main', 'topic', '--json']))
        assert series['complete'] and len(series['matches']) == 1
        assert [c['status'] for c in series['left']['commits']] == ['patch_id_match', 'heuristic_candidates']
        assert series['left']['commits'][1]['candidates'][0]['score'] == 7333
        assert 'heuristic_candidates' in run([atlas, '--repo', repo, 'series',
                                             'old-base', 'before', 'main', 'topic'])
        html = run([atlas, '--repo', repo, 'series', 'old-base', 'before', 'main', 'topic', '--html'])
        embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
        assert embedded and json.loads(embedded[1]) == series
        assert "default-src 'none'" in html and 'Inspect bounded evidence' in html
        # Actual split and squash review through only the installed console.
        repo = base / 'group-example'
        repo.mkdir()
        git('init','-b','main');git('config','user.name','Atlas Demo');git('config','user.email','demo@example.invalid')
        git('commit','--allow-empty','-m','Root');git('branch','root')
        git('checkout','-b','pieces')
        (repo/'feature').write_text('alpha\n');git('add','feature');git('commit','-m','First piece')
        first=git('rev-parse','HEAD')
        (repo/'feature').write_text('alpha\nbeta\n');git('add','feature');git('commit','-m','Second piece')
        second=git('rev-parse','HEAD')
        git('checkout','-b','squashed','root');git('merge','--squash','pieces');git('commit','-m','Combined feature')
        singleton=git('rev-parse','HEAD')
        for left,right in [('pieces','squashed'),('squashed','pieces')]:
            command=[atlas,'--repo',repo,'series','root',left,'root',right,'--groups']
            result=json.loads(run([*command,'--json']))
            assert result['complete'] and result['schema_version']==2
            group=result['group_comparison'];assert len(group['candidates'])==1
            candidate=group['candidates'][0]
            assert candidate['single_oid']==singleton and candidate['normalized_patch_agreement']
            assert group['groups'][0]['members']==[first,second]
            assert group['groups'][0]['base_oid']==git('rev-parse','root')
            assert group['groups'][0]['tip_oid']==second
            assert candidate['score']==10000
            assert all(not candidate['evidence'][k]['items'] for k in ['source_only','counterpart_only','paths_source_only','paths_counterpart_only'])
            html=run([*command,'--html'])
            embedded=re.search(r'<script id="report-data" type="application/json">(.*?)</script>',html,re.S)
            assert json.loads(embedded[1])==result
            assert 'Split and squash candidates' in html
            assert 'normalized_patch_agreement=True' in run(command)
        print(json.dumps({'installed_version': run([atlas, '--version']),
                          'python': run([python, '--version']), 'git': git('--version'),
                          'checks': ['isolated installed import', 'console entry point', 'two branches',
                                     'two worktrees', 'locked worktree', 'upstream +1/-1',
                                     'comparison unique counts and merge base', 'legacy graph',
                                     'offline cherry-pick patch group JSON and terminal',
                                     'offline before/after rebase series: exact group and edited candidate, JSON and terminal',
                                     'installed offline HTML embeds identical rebase JSON and bundled viewer',
                                     'actual git merge --squash: split and squash in both orientations, schema 2, exact endpoint evidence, terminal and HTML/JSON parity'],
                          'result': 'passed'}, indent=2))


if __name__ == '__main__':
    main()
