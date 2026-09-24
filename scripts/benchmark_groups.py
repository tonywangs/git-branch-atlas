#!/usr/bin/env python3
"""Repeated bounded group JSON/HTML generation and offline Chromium loads, 12/500 commits."""
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
                       '--max-count', str(count), '--max-comparisons', '100000', '--timeout', '600', '--max-output-bytes', str(16*1024*1024)]
            command += ['--groups']
            scenarios = [('complete', [])] if count == 6 else [
                ('enumeration-output-exhausted', ['--max-groups','60','--max-group-comparisons','4000','--max-group-candidates','2']),
                ('comparison-exhausted', ['--max-groups','1600','--max-group-comparisons','1000','--max-group-candidates','500'])]
            for scenario, extra in scenarios:
                current = command + extra
                j = subprocess.run([*current,'--json'],cwd=ROOT,env=env,capture_output=True)
                assert j.returncode == (0 if count == 6 else 1),j.stderr
                expected = j.stdout
                report = json.loads(expected)
                gc = report['group_comparison']
                assert gc['complete'] == (count == 6)
                if scenario == 'enumeration-output-exhausted':
                    assert gc['windows_omitted'] and gc['candidates_omitted'] and gc['comparisons_unsearched']
                elif scenario == 'comparison-exhausted':
                    assert not gc['windows_omitted'] and gc['comparisons_unsearched']
                for repetition in range(args.repeat):
                    name = f'commits-{count*2}-{scenario}-run-{repetition+1}'
                    names.append(name)
                    rss = directory/'rss'
                    start = time.perf_counter()
                    result = subprocess.run(['/usr/bin/time','-f','%M','-o',str(rss),*current,'--html'],cwd=ROOT,env=env,capture_output=True)
                    elapsed = time.perf_counter()-start
                    assert result.returncode == j.returncode,result.stderr
                    output = result.stdout
                    (directory/(name+'.html')).write_bytes(output)
                    (directory/(name+'.json')).write_bytes(expected)
                    rows.append(dict(name=name,commits=count*2,seconds=elapsed,peak_rss_kib=int(rss.read_text().splitlines()[-1]),
                        html_bytes=len(output),json_bytes=len(expected),complete=report['complete'],
                        structural_windows=gc['structural_windows'],windows_omitted=gc['windows_omitted'],
                        inspected_groups=len(gc['groups']),comparison_count=gc['comparison_count'],
                        comparisons_unsearched=gc['comparisons_unsearched'],candidate_count=gc['candidate_count'],
                        candidates_omitted=gc['candidates_omitted'],inspection_bytes_read=report['inspection_bytes_read'],
                        group_limits=gc['limits']))

        browser = subprocess.run(['node', str(ROOT/'scripts/verify_html_browser.cjs'), str(directory), *names], capture_output=True, text=True)
        if browser.returncode:
            raise RuntimeError(browser.stderr)
        output = dict(python=platform.python_version(),platform=platform.platform(),git=git('--version').decode().strip(),
                      fixture='Deterministic timestamps 1700000000+i; two independent 80-line file-add series, even patches exact, odd patches edited; 6 or 250 commits per side',
                      limitations=['synthetic warm-cache workloads, not real-world or cold-cache estimates',
                                   'GNU time maximum RSS of CLI or one child; not aggregate concurrent memory',
                                   '500-commit group workloads deliberately exhaust enumeration/output/comparison limits; no full-search timing claim',
                                   'browser load includes navigation overhead; DOM counts after interaction; no browser peak-memory measurement'],
                      generation=rows,browser=json.loads(browser.stdout))
        text = json.dumps(output,indent=2)+'\n'
        if args.output:
            args.output.write_text(text)
        print(text,end='')


if __name__ == '__main__':
    main()
