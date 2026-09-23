#!/usr/bin/env python3
"""Reproducible offline series workloads; GNU time reports process peak RSS."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commits', type=int, default=250, help='commits per side')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 2 <= args.commits <= 1000 or not 1 <= args.repeat <= 10:
        parser.error('commits must be 2..1000; repeat must be 1..10')
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_ALLOW_PROTOCOL='')
    with tempfile.TemporaryDirectory(prefix='atlas-series-bench-') as temp:
        repo = Path(temp) / 'repo'
        repo.mkdir()
        def git(*argv, data=None):
            return subprocess.check_output(['git', '-C', str(repo), *argv], input=data, env=env,
                                           stderr=subprocess.PIPE)
        git('init', '-b', 'main')
        stream = bytearray(b'commit refs/heads/root\nmark :1\ncommitter Fixture <fixture@example.invalid> 1700000000 +0000\ndata 4\nroot\n\n')
        for side in ('left', 'right'):
            for i in range(args.commits):
                message = f'{side} {i}'.encode()
                content = (f'payload {i:04d}\n' * 79 + (f'edited {side} {i:04d}\n' if i % 2 else f'payload {i:04d}\n')).encode()
                stream.extend(f'commit refs/heads/{side}\ncommitter Fixture <fixture@example.invalid> {1700000001+i} +0000\ndata {len(message)}\n'.encode() + message + b'\n')
                if i == 0:
                    stream.extend(b'from :1\n')
                stream.extend(f'M 100644 inline file-{i:04d}\ndata {len(content)}\n'.encode() + content + b'\n\n')
        stream.extend(b'done\n')
        git('fast-import', '--quiet', data=bytes(stream))
        rows = []
        cases = [('complete', ['--max-count', str(args.commits)]),
                 ('commit_limit', ['--max-count', str(args.commits // 2)]),
                 ('byte_limit', ['--max-count', str(args.commits), '--max-diff-bytes', '4096']),
                 ('comparison_limit', ['--max-count', str(args.commits), '--max-comparisons', str(min(1, (args.commits // 2) ** 2 - 1))])]
        for case, options in cases:
            for repetition in range(args.repeat):
                rss_file = Path(temp) / 'rss'
                command = [sys.executable, '-m', 'git_branch_atlas', '--repo', str(repo),
                           'series', 'root', 'left', 'root', 'right', '--json', '--timeout', '120',
                           '--max-comparisons', '100000', '--max-output-bytes', str(16*1024*1024), *options]
                start = time.perf_counter()
                result = subprocess.run(['/usr/bin/time', '-f', '%M', '-o', str(rss_file), *command],
                                        cwd=ROOT, env=env, capture_output=True)
                elapsed = time.perf_counter() - start
                report = json.loads(result.stdout)
                assert result.returncode == (0 if case == 'complete' else 1), result.stderr
                assert report['complete'] == (case == 'complete')
                if case == 'complete':
                    assert len(report['matches']) == (args.commits + 1) // 2
                    assert report['comparison_count'] == (args.commits // 2) ** 2
                    for item in report['left']['commits']:
                        if item['status'] == 'heuristic_candidates':
                            assert item['candidates'][0]['score'] == 9900
                else:
                    assert all(c['status'] != 'no_candidate' for side in ('left', 'right') for c in report[side]['commits'])
                rows.append(dict(case=case, repetition=repetition + 1, seconds=elapsed,
                                 peak_rss_kib=int(rss_file.read_text().splitlines()[-1]),
                                 stdout_bytes=len(result.stdout), stderr_bytes=len(result.stderr),
                                 exit_code=result.returncode, matching_groups=len(report['matches']),
                                 inspection_bytes_read=report['inspection_bytes_read'],
                                 comparison_count=report['comparison_count']))
        output = dict(python=platform.python_version(), platform=platform.platform(),
                      git=git('--version').decode().strip(), commits_per_side=args.commits,
                      fixture_seed='deterministic fast-import, timestamps 1700000000+i',
                      fixture='two independent series adding 80-line files; even patches identical, odd patches edit one line; distinct messages',
                      limitations=['synthetic sequential warm-cache local history',
                                   'GNU time maximum RSS of CLI or an individual child, not summed concurrent process memory',
                                   'no cold-cache or real-world performance claim'], measurements=rows)
        text = json.dumps(output, indent=2) + '\n'
        if args.output:
            args.output.write_text(text)
        print(text, end='')


if __name__ == '__main__':
    main()
