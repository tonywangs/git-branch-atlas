#!/usr/bin/env python3
"""Profile and pair frozen/current group searches on deterministic Git fixtures."""
import argparse
from collections import defaultdict
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
BASELINE = ROOT / 'experiments/group_latency/baseline'
SCENARIOS = {'small': (6, 6), 'complete-500': (20, 480), 'balanced-500-limited': (250, 250)}


def clean_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith(('GIT_', 'PYTHON'))}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_ALLOW_PROTOCOL='',
               GIT_NO_LAZY_FETCH='1', LC_ALL='C', PYTHONDONTWRITEBYTECODE='1')
    return env


def fixture(repo, counts):
    repo.mkdir()
    def git(*argv, data=None):
        return subprocess.check_output(['git', '-C', str(repo), *argv], input=data,
                                       env=clean_env(), stderr=subprocess.PIPE)
    git('init', '-b', 'main')
    stream = bytearray(b'commit refs/heads/root\nmark :1\ncommitter Fixture <fixture@example.invalid> 1700000000 +0000\ndata 4\nroot\n\n')
    for side, count in zip(('left', 'right'), counts):
        for i in range(count):
            message = f'{side} {i}'.encode()
            content = (f'payload {i:04d}\n'*79 + (f'edited {side} {i:04d}\n' if i%2 else f'payload {i:04d}\n')).encode()
            stream.extend(f'commit refs/heads/{side}\ncommitter Fixture <fixture@example.invalid> {1700000001+i} +0000\ndata {len(message)}\n'.encode()+message+b'\n')
            if i == 0:
                stream.extend(b'from :1\n')
            stream.extend(f'M 100644 inline file-{i:04d}\ndata {len(content)}\n'.encode()+content+b'\n\n')
    git('fast-import', '--quiet', data=bytes(stream)+b'done\n')
    return {ref: git('rev-parse', ref).decode().strip() for ref in ('root', 'left', 'right')}


def options(scenario):
    return dict(max_count=max(SCENARIOS[scenario]), max_bytes=256*1024*1024, seconds=600,
                max_comparisons=100000, include_groups=True, max_groups=1600,
                max_group_comparisons=1000 if scenario == 'balanced-500-limited' else 100000,
                max_group_candidates=2000)


def source_hashes(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((directory/'git_branch_atlas').glob('*.py'))}


def worker(args):
    sys.path.insert(0, str(args.implementation.resolve()))
    from git_branch_atlas import patches, series, group_compare
    totals, calls = defaultdict(float), defaultdict(int)
    subprocess_by_stage = defaultdict(int)
    active_git = None
    stage = 'individual'
    def timed(module, name, label):
        original = getattr(module, name)
        def wrapped(*a, **kw):
            start = time.perf_counter()
            try:
                return original(*a, **kw)
            finally:
                key = stage + ':' + label
                totals[key] += time.perf_counter()-start
                calls[key] += 1
        setattr(module, name, wrapped)
    original_run = patches.Budget.run
    def run(self, argv, **kwargs):
        nonlocal active_git
        key = stage + ':git:' + argv[0]
        if argv[0] == 'diff-tree':
            key += ':raw' if '--raw' in argv else ':patch'
        active_git = key
        start = time.perf_counter()
        try:
            return original_run(self, argv, **kwargs)
        finally:
            totals[key] += time.perf_counter()-start
            calls[key] += 1
    patches.Budget.run = run
    real_popen = subprocess.Popen
    subprocess_count = 0
    def popen(*a, **kw):
        nonlocal subprocess_count
        subprocess_count += 1
        subprocess_by_stage[active_git] += 1
        return real_popen(*a, **kw)
    subprocess.Popen = popen
    original_group = group_compare.compare_groups
    def groups(*a, **kw):
        nonlocal stage
        stage = 'groups'
        start = time.perf_counter()
        try:
            return original_group(*a, **kw)
        finally:
            totals['groups:total'] = time.perf_counter()-start
    group_compare.compare_groups = groups
    for module in (series, group_compare):
        for name in ('features', 'score', 'evidence'):
            timed(module, name, name)
    start = time.perf_counter()
    report = series.series_compare(args.repo, 'root', 'left', 'root', 'right', **options(args.scenario))
    computed = time.perf_counter()
    output = (series.format_series_report(report, True, 16*1024*1024)+'\n').encode()
    totals['serialization'] = time.perf_counter()-computed
    totals['individual:total'] = computed-start-totals['groups:total']
    gc = report['group_comparison']
    if args.report:
        args.report.write_bytes(output)
    print(json.dumps(dict(seconds=time.perf_counter()-start, stages_seconds=dict(totals), calls=dict(calls),
                         git_subprocesses=subprocess_count, subprocesses_by_stage=dict(subprocess_by_stage), sha256=hashlib.sha256(output).hexdigest(),
                         output_bytes=len(output), complete=report['complete'],
                         structural_windows=gc['structural_windows'], inspected_groups=len(gc['groups']),
                         comparison_count=gc['comparison_count'], comparisons_possible=gc['comparisons_possible'],
                         candidate_count=gc['candidate_count'], candidates_omitted=gc['candidates_omitted'],
                         inspection_bytes_read=report['inspection_bytes_read'])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat', type=int, default=6)
    parser.add_argument('--profile-only', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--implementation', type=Path)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--scenario', choices=SCENARIOS)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if not 1 <= args.repeat <= 20:
        parser.error('repeat must be 1..20')
    assert source_hashes(BASELINE) == json.loads((BASELINE.parent/'baseline.json').read_text())['files'], 'frozen source changed'
    rows, fixtures = [], {}
    raw_path = args.output.with_suffix('.rows.jsonl') if args.output else None
    if raw_path:
        raw_path.write_text('')
    with tempfile.TemporaryDirectory(prefix='atlas-latency-') as tmp:
        directory = Path(tmp)
        for scenario, counts in SCENARIOS.items():
            repo = directory/scenario
            fixtures[scenario] = fixture(repo, counts)
            for pair in range(1 if args.profile_only else args.repeat):
                order = ['baseline'] if args.profile_only else (['baseline', 'current'] if pair%2 == 0 else ['current', 'baseline'])
                pair_rows = []
                for implementation in order:
                    source = BASELINE if implementation == 'baseline' else ROOT
                    rss = directory/'rss'
                    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--implementation', str(source),
                               '--repo', str(repo), '--scenario', scenario]
                    started = time.perf_counter()
                    result = subprocess.run(['/usr/bin/time', '-f', '%M', '-o', str(rss), *command],
                                            env=clean_env(), capture_output=True, text=True, check=True)
                    row = json.loads(result.stdout)
                    row.update(scenario=scenario, pair=pair+1, implementation=implementation, order=order,
                               wall_seconds=time.perf_counter()-started,
                               peak_rss_kib=int(rss.read_text().splitlines()[-1]), command=command)
                    rows.append(row)
                    if raw_path:
                        with raw_path.open('a') as raw:
                            raw.write(json.dumps(row)+'\n')
                    pair_rows.append(row)
                    print(f'{scenario} {pair+1} {implementation}: {row["wall_seconds"]:.3f}s, {row["git_subprocesses"]} Git processes', file=sys.stderr, flush=True)
                if len(pair_rows) == 2:
                    assert pair_rows[0]['sha256'] == pair_rows[1]['sha256'], pair_rows
                for row in pair_rows:
                    assert row['inspected_groups'] == row['structural_windows']
                    assert row['complete'] == (scenario != 'balanced-500-limited'), row
    summary = {}
    for scenario in SCENARIOS:
        values = {kind: [r['wall_seconds'] for r in rows if r['scenario']==scenario and r['implementation']==kind]
                  for kind in ('baseline', 'current')}
        summary[scenario] = {kind+'_median_seconds': statistics.median(v) for kind, v in values.items() if v}
        if values['current']:
            summary[scenario]['median_speedup'] = statistics.median(values['baseline'])/statistics.median(values['current'])
            summary[scenario]['paired_speedups'] = [a/b for a,b in zip(values['baseline'],values['current'])]
    output = dict(python=platform.python_version(), platform=platform.platform(),
                  git=subprocess.check_output(['git','--version'],text=True).strip(),
                  baseline_tree='2ca8e13f793267ea5fc9b20ad3704d3598af93ec',
                  source_sha256={kind:source_hashes(path) for kind,path in [('baseline',BASELINE),('current',ROOT)]},
                  fixtures=fixtures, scenarios=SCENARIOS, options={s:options(s) for s in SCENARIOS},
                  limitations=['Synthetic warm-cache measurements; host not machine-isolated.',
                               'GNU time peak RSS is maximum individual process, not summed process-tree memory.',
                               'Stage wrappers add instrumentation overhead; total stages include their sub-stages.',
                               'Balanced 500 case exhausts comparisons; complete 500 uses 20/480 within unchanged 100000 cap.'],
                  summary=summary, measurements=rows)
    text = json.dumps(output, indent=2)+'\n'
    if args.output:
        args.output.write_text(text)
    print(text, end='')


if __name__ == '__main__':
    main()
