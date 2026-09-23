#!/usr/bin/env python3
"""Generate a deterministic Git fixture and time whole CLI processes; no network."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ENV = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
FIXTURE_ENV.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')


def git(repo, *args, input=None):
    return subprocess.run(['git', '-C', str(repo), *args], input=input,
                          capture_output=True, check=True, env=FIXTURE_ENV).stdout.decode().strip()


def generate(repo: Path, commits: int, branches: int) -> dict:
    """One linear trunk, then branches from deterministic earlier trunk commits."""
    if branches < 2 or commits < branches + 1:
        raise ValueError('need at least 2 branches and commits > branches')
    repo.mkdir()
    git(repo, 'init', '--object-format=sha1', '-b', 'main')
    trunk = commits - branches + 1
    chunks = []
    for index in range(1, commits + 1):
        is_main = index <= trunk
        branch = 'main' if is_main else f'topic-{index - trunk:03d}'
        parent = index - 1 if is_main else max(1, trunk - (index - trunk) * 37)
        subject = f'fixture commit {index}\n'
        chunks.append(f'commit refs/heads/{branch}\nmark :{index}\n'
                      f'committer Atlas Fixture <fixture@example.invalid> {1700000000 + index} +0000\n'
                      f'data {len(subject)}\n{subject}')
        if index > 1:
            chunks.append(f'from :{parent}\n')
        chunks.append('deleteall\n\n')
    stream = ''.join(chunks).encode()
    git(repo, 'fast-import', '--quiet', input=stream)
    for index in range(1, branches):
        name = f'topic-{index:03d}'
        git(repo, 'config', f'branch.{name}.remote', '.')
        git(repo, 'config', f'branch.{name}.merge', 'refs/heads/main')
    return {'commits': commits, 'branches': branches, 'trunk_commits': trunk,
            'generator': 'linear trunk plus one commit per topic, fork spacing 37; empty trees',
            'fast_import_sha256': hashlib.sha256(stream).hexdigest(),
            'main_oid': git(repo, 'rev-parse', 'main')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commits', type=int, default=5000)
    parser.add_argument('--branches', type=int, default=100)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error('--repeat must be positive')
    with tempfile.TemporaryDirectory(prefix='atlas-benchmark-') as temp:
        repo = Path(temp) / 'fixture'
        fixture = generate(repo, args.commits, args.branches)
        assert int(git(repo, 'rev-list', '--all', '--count')) == args.commits
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT)
        samples = {}
        for name, options in [('summary_json', ['summary', '--json']),
                              ('compare_json', ['compare', 'topic-001', f'topic-{args.branches - 1:03d}', '--json']),
                              ('graph', ['graph', '--all', '--max-count', '30', '--no-color'])]:
            times = []
            for _ in range(args.repeat):
                start = time.perf_counter()
                result = subprocess.run([sys.executable, '-m', 'git_branch_atlas', '--repo', str(repo), *options],
                                        env=env, cwd=temp, capture_output=True, check=True)
                times.append(time.perf_counter() - start)
                if name == 'summary_json':
                    report = json.loads(result.stdout)
                    assert len(report['branches']) == args.branches
                    for branch in report['branches']:
                        if branch['name'] != 'main':
                            index = int(branch['name'].split('-')[1])
                            assert branch['ahead'] == 1
                            assert branch['behind'] == min(index * 37, fixture['trunk_commits'] - 1)
                elif name == 'compare_json':
                    report = json.loads(result.stdout)
                    assert report['right']['unique_count'] == 1
                    expected = (min((args.branches - 1) * 37, fixture['trunk_commits'] - 1)
                                - min(37, fixture['trunk_commits'] - 1) + 1)
                    assert report['left']['unique_count'] == expected
                    assert len(report['merge_bases']) == 1
            samples[name] = {'seconds': times, 'median_seconds': statistics.median(times),
                             'last_stdout_bytes': len(result.stdout)}
        report = {'fixture': fixture, 'python': platform.python_version(),
                  'git': git(repo, '--version'), 'platform': platform.platform(),
                  'timing': 'wall-clock, full subprocess including Python startup; sequential warm-cache runs; no cache flushing',
                  'samples': samples}
        output = json.dumps(report, indent=2) + '\n'
        if args.output:
            args.output.write_text(output)
        print(output, end='')


if __name__ == '__main__':
    main()
