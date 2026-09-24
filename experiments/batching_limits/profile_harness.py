#!/usr/bin/env python3
"""Reproduce bounded loose/packed batch transport comparisons (fresh processes)."""
import argparse
from contextlib import redirect_stdout
import hashlib
import io
import itertools
import json
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time

import measure_group_latency as latency

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT/'experiments/batching_limits/baseline'
PLAIN = ROOT/'experiments/group_batching/baseline'
CASES = ('small', 'many-files', 'below-cap', 'above-cap', 'batch-overflow', 'mixed', 'repeated', 'limited')
SOURCES = {'unbatched': PLAIN, 'batched': FROZEN, 'quarter': FROZEN}


def options(case):
    return dict(max_count=32, max_bytes=256*1024*1024, seconds=600,
                max_comparisons=100000, include_groups=True, max_groups=1600,
                max_group_comparisons=0 if case == 'limited' else 100000,
                max_group_candidates=2000)


def worker(args):
    sys.path.insert(0, str(args.implementation.resolve()))
    extra = dict(batch_calls=0, batch_disabled_transitions=0, batch_empty_returns=0,
                 batch_requests=0, repeated_requests=0, hints_returned=0,
                 batch_seconds=0., standalone_patch_calls=0)
    from git_branch_atlas import patches
    run = patches.Budget.run
    def wrapped_run(self, argv, **kwargs):
        if argv[0] == 'diff-tree' and '-p' in argv and '--stdin' not in argv:
            extra['standalone_patch_calls'] += 1
        return run(self, argv, **kwargs)
    patches.Budget.run = wrapped_run
    if args.kind != 'unbatched':
        from git_branch_atlas import batching
        if args.kind == 'quarter':
            # Experimental candidate: four requests, same byte cap and fallback.
            batching.REQUESTS = 4
        original = batching.prefetch
        def prefetch(budget, requests):
            started = time.perf_counter()
            disabled = getattr(budget, '_batch_disabled', False)
            extra['batch_calls'] += 1
            extra['batch_requests'] += len(requests)
            extra['repeated_requests'] += len(requests)-len(set(requests))
            try:
                hints = original(budget, requests)
                extra['hints_returned'] += len(hints)
                extra['batch_empty_returns'] += not bool(hints)
                extra['batch_disabled_transitions'] += not disabled and getattr(budget, '_batch_disabled', False)
                return hints
            finally:
                extra['batch_seconds'] += time.perf_counter()-started
        batching.prefetch = prefetch
    latency.options = options
    output = io.StringIO()
    with redirect_stdout(output):
        latency.worker(args)
    row = json.loads(output.getvalue())
    row.update(extra)
    print(json.dumps(row))


def fixture(case, seed):
    # Imported only in the parent: worker imports must use the selected source.
    sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
    import test_patches
    test_patches.ENV = latency.clean_env()
    test_patches.ENV.update(GIT_AUTHOR_DATE='1700000000 +0000', GIT_COMMITTER_DATE='1700000000 +0000')
    f = test_patches.PatchTests(); f.setUp()
    root = f.node({})
    rng = random.Random(seed)
    payload = ''.join(rng.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=256)).encode()
    refs = {'root':root}
    count = 1 if case in ('below-cap','above-cap') else 6
    target = 1024*1024 + (-1 if case == 'below-cap' else 1)
    calibration = None
    for side in ('left','right'):
        parent, files = root, {}
        for i in range(count):
            modes = {}
            if case in ('below-cap','above-cap'):
                # A single line: patch header size is independent of line length.
                trial = f.node({'f':b'x\n'}, (root,))
                from git_branch_atlas.patches import DIFF
                command = ['git','-C',str(f.repo),'diff-tree','--root','--no-commit-id','-r','-p',*DIFF,trial,'--']
                size = len(subprocess.check_output(command,env=latency.clean_env()))
                # Two 17-byte framing markers also consume the batch cap.
                marker_bytes = len(b'atlas-batch-0000\natlas-batch-0001\n')
                files = {'f':payload[:1]*(target-size-marker_bytes+1)+b'\n'}
                calibration = dict(target_framed_bytes=target, marker_bytes=marker_bytes)
            elif case == 'repeated':
                files = {'f':payload+b'\n' if i%2 else payload+b'\nextra\n'}
            else:
                for j in range(32 if case == 'many-files' else 1):
                    content = payload+b'\n'+f'{i} {j}\n'.encode()
                    if case == 'batch-overflow': content = (payload+b'\n')*900
                    files[f'f{i:02d}-{j:02d}'] = content
                if case == 'mixed' and i%3 == 1:
                    files['binary'] = b'\0'+payload
                    files['link'] = b'target'; modes['link'] = '120000'
                elif case == 'mixed':
                    files.pop('binary',None); files.pop('link',None)
            if side == 'right' and i == count-1 and case not in ('below-cap','above-cap'):
                files['edited'] = b'right\n'
            parent = f.node(files,(parent,),modes)
            if calibration:
                command[-2] = parent
                actual = len(subprocess.check_output(command,env=latency.clean_env()))+calibration['marker_bytes']
                assert actual == target, (actual,target)
        refs[side] = parent
    if case == 'repeated': refs['right'] = refs['left']
    for ref, oid in refs.items(): f.git('update-ref','refs/heads/'+ref,oid)
    f.git('read-tree',refs['left'])
    (f.repo/'untracked').write_text('preserve me\n')
    return f, dict(refs=refs, seed=seed, commits_per_side=count, calibration=calibration)


def snapshot(repo):
    return {str(p.relative_to(repo)):(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest())
            for p in repo.rglob('*') if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat',type=int,default=6)
    parser.add_argument('--output',type=Path,default=ROOT/'results/batching-limits.json')
    parser.add_argument('--profile-only',action='store_true')
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--implementation',type=Path)
    parser.add_argument('--repo',type=Path)
    parser.add_argument('--scenario',choices=CASES)
    parser.add_argument('--kind',choices=SOURCES)
    parser.add_argument('--report',type=Path)
    args = parser.parse_args()
    if args.worker: return worker(args)
    if not 6 <= args.repeat <= 20: parser.error('repeat must be 6..20')
    for source in (PLAIN,FROZEN):
        assert latency.source_hashes(source) == json.loads((source.parent/'baseline.json').read_text())['files']
    assert latency.source_hashes(ROOT) == latency.source_hashes(FROZEN), 'production source changed; review protocol'
    rows, fixtures = [], {}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    raw = args.output.with_suffix('.rows.jsonl'); raw.write_text('')
    permutations = list(itertools.permutations(SOURCES))
    with tempfile.TemporaryDirectory(prefix='atlas-limits-') as tmp:
        directory = Path(tmp)
        for case_index, case in enumerate(CASES):
            f, meta = fixture(case,23000+case_index)
            try:
                for storage in ('loose','packed'):
                    packing = []
                    if storage == 'packed':
                        packing = [['git','-C',str(f.repo),'repack','-ad'],['git','-C',str(f.repo),'prune-packed'],['git','-C',str(f.repo),'prune','--expire=now']]
                        for command in packing: subprocess.run(command,env=latency.clean_env(),check=True,capture_output=True)
                    counts = dict(line.split(': ',1) for line in f.git('count-objects','-v').decode().splitlines())
                    assert int(counts['packs']) > 0 if storage == 'packed' else int(counts['packs']) == 0
                    if storage == 'packed': assert int(counts['count']) == 0
                    key = case+'/'+storage
                    fixtures[key] = dict(meta,storage=storage,object_counts=counts,packing_commands=packing)
                    before = snapshot(f.repo)
                    for pair in range(1 if args.profile_only else args.repeat):
                        order = ('batched',) if args.profile_only else permutations[pair%6]
                        pair_rows = []
                        for kind in order:
                            rss = directory/'rss'
                            report = directory/'report.json'
                            command = [sys.executable,str(Path(__file__).resolve()),'--worker','--implementation',str(SOURCES[kind]),
                                       '--repo',str(f.repo),'--scenario',case,'--kind',kind,'--report',str(report)]
                            timed = ['/usr/bin/time','-f','%M','-o',str(rss),*command]
                            start = time.perf_counter()
                            result = subprocess.run(timed,env=latency.clean_env(),capture_output=True,text=True,check=True)
                            row = json.loads(result.stdout)
                            row.update(workload=key,seed=meta['seed'],pair=pair+1,kind=kind,order=order,command=timed,
                                       wall_seconds=time.perf_counter()-start,peak_rss_kib=int(rss.read_text().strip()))
                            data = report.read_bytes()
                            assert hashlib.sha256(data).hexdigest() == row['sha256']
                            assert row['complete'] == (case != 'limited'), (key,row)
                            if pair_rows: assert data == previous, (key,pair,kind,'output mismatch')
                            previous = data
                            rows.append(row); pair_rows.append(row)
                            with raw.open('a') as stream: stream.write(json.dumps(row)+'\n')
                            print(f'{key} {pair+1} {kind}: {row["wall_seconds"]:.3f}s; fallback transitions {row["batch_disabled_transitions"]}',file=sys.stderr,flush=True)
                    assert snapshot(f.repo) == before, (key,'repository changed')
            finally: f.doCleanups()
    summary = {}
    for key in fixtures:
        summary[key] = {}
        for kind in SOURCES:
            values = [r for r in rows if r['workload']==key and r['kind']==kind]
            if not values: continue
            summary[key][kind] = dict(median_seconds=statistics.median(r['wall_seconds'] for r in values),
                max_peak_rss_kib=max(r['peak_rss_kib'] for r in values),
                subprocesses=sorted(set(r['git_subprocesses'] for r in values)),
                fallback_transitions=sorted(set(r['batch_disabled_transitions'] for r in values)))
    evidence = dict(python=platform.python_version(),platform=platform.platform(),
        git=subprocess.check_output(['git','--version'],text=True).strip(),
        source_sha256={k:latency.source_hashes(v) for k,v in SOURCES.items()},
        harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        candidate='Frozen batching source with REQUESTS=4; no production modification',
        cache='Fresh Python/Git processes. Fixture creation, calibration, packing and file snapshots warm filesystem caches. No cache eviction, host-wide settings or discarded warmups.',
        rss_scope='GNU time maximum individual process KiB, including waited-for children; not summed process-tree memory.',
        protocol='All six permutations of three implementations; byte-identical full JSON required within each block; no dropped rows; 600s nonbinding comparison deadline.',
        options={c:options(c) for c in CASES},fixtures=fixtures,summary=summary,measurements=rows,
        repository_contents_index_refs_configuration_unchanged=True)
    args.output.write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__ == '__main__': main()
