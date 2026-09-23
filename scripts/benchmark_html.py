#!/usr/bin/env python3
"""Repeated complete HTML generation and offline Chromium loads, 12/500 commits."""
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
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 10:
        parser.error('--repeat must be 1..10')
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_ALLOW_PROTOCOL='')
    rows, names = [], []
    with tempfile.TemporaryDirectory(prefix='atlas-html-bench-') as tmp:
        directory = Path(tmp)
        for count in (6, 250):
            repo = directory / str(count)
            repo.mkdir()
            def git(*argv, data=None):
                return subprocess.check_output(['git', '-C', str(repo), *argv], input=data, env=env, stderr=subprocess.PIPE)
            git('init', '-b', 'main')
            stream = bytearray(b'commit refs/heads/root\nmark :1\ncommitter Fixture <fixture@example.invalid> 1700000000 +0000\ndata 4\nroot\n\n')
            for side in ('left', 'right'):
                for i in range(count):
                    message = f'{side} {i}'.encode()
                    content = (f'payload {i:04d}\n'*79 + (f'edited {side} {i:04d}\n' if i%2 else f'payload {i:04d}\n')).encode()
                    stream.extend(f'commit refs/heads/{side}\ncommitter Fixture <fixture@example.invalid> {1700000001+i} +0000\ndata {len(message)}\n'.encode()+message+b'\n')
                    if i == 0:
                        stream.extend(b'from :1\n')
                    stream.extend(f'M 100644 inline file-{i:04d}\ndata {len(content)}\n'.encode()+content+b'\n\n')
            git('fast-import', '--quiet', data=bytes(stream)+b'done\n')
            command = [sys.executable, '-m', 'git_branch_atlas', '--repo', str(repo), 'series', 'root', 'left', 'root', 'right',
                       '--max-count', str(count), '--max-comparisons', '100000', '--timeout', '120', '--max-output-bytes', str(16*1024*1024)]
            expected = subprocess.check_output([*command, '--json'], cwd=ROOT, env=env)
            for repetition in range(args.repeat):
                name = f'commits-{count*2}-run-{repetition+1}'
                names.append(name)
                rss = directory/'rss'
                start = time.perf_counter()
                output = subprocess.check_output(['/usr/bin/time', '-f', '%M', '-o', str(rss), *command, '--html'], cwd=ROOT, env=env)
                elapsed = time.perf_counter()-start
                (directory/(name+'.html')).write_bytes(output)
                (directory/(name+'.json')).write_bytes(expected)
                rows.append(dict(name=name,commits=count*2,seconds=elapsed,peak_rss_kib=int(rss.read_text().splitlines()[-1]),html_bytes=len(output)))
        browser = subprocess.run(['node', str(ROOT/'scripts/verify_html_browser.cjs'), str(directory), *names], capture_output=True, text=True)
        if browser.returncode:
            raise RuntimeError(browser.stderr)
        output = dict(python=platform.python_version(),platform=platform.platform(),git=git('--version').decode().strip(),
                      fixture='Deterministic timestamps 1700000000+i; two independent 80-line file-add series, even patches exact, odd patches edited; 6 or 250 commits per side',
                      limitations=['synthetic warm-cache workloads, not real-world or cold-cache estimates',
                                   'GNU time maximum RSS of CLI or one child; not aggregate concurrent memory',
                                   'browser load includes navigation overhead; DOM counts after interaction; no browser peak-memory measurement'],
                      generation=rows,browser=json.loads(browser.stdout))
        text = json.dumps(output,indent=2)+'\n'
        if args.output:
            args.output.write_text(text)
        print(text,end='')


if __name__ == '__main__':
    main()
