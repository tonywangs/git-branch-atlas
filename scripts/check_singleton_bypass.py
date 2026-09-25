#!/usr/bin/env python3
"""Audit persisted singleton-bypass evidence independently of the timing driver."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',type=Path,default=ROOT/'results/singleton-bypass.json')
    args = parser.parse_args()
    evidence = json.loads(args.results.read_text())
    frozen = json.loads((ROOT/'experiments/singleton_bypass/baseline.json').read_text())
    assert frozen['protocol_sha256'] == hashlib.sha256((ROOT/'experiments/singleton_bypass/README.md').read_bytes()).hexdigest()
    assert frozen['files'] == evidence['source_sha256']['singleton']
    rows = evidence['measurements']
    raw = [json.loads(line) for line in args.results.with_suffix('.rows.jsonl').read_text().splitlines()]
    assert rows == raw
    kinds = ('unbatched','batched','singleton')
    cases = ('small','many-files','below-cap','above-cap','batch-overflow','mixed','repeated','limited','singleton-small','singleton-unsupported','singleton-tail')
    keys = {c+'/'+storage for c in cases for storage in ('loose','packed')}
    assert set(evidence['fixtures']) == keys
    repetitions, remainder = divmod(len(rows), len(keys)*len(kinds))
    assert remainder == 0 and 6 <= repetitions <= 20
    for key in keys:
        selected = [r for r in rows if r['workload']==key]
        assert len(selected) == repetitions*len(kinds)
        assert len({r['sha256'] for r in selected}) == 1
        assert all(r['complete'] == (not key.startswith(('limited/', 'singleton-tail/'))) for r in selected)
        for field in ('structural_windows','inspected_groups','comparison_count','comparisons_possible',
                      'candidate_count','candidates_omitted','inspection_bytes_read'):
            assert len({r[field] for r in selected}) == 1, (key,field)
        orders = list(itertools.permutations(kinds))
        for pair in range(1,repetitions+1):
            order = orders[(pair-1)%6]
            block = [r for r in selected if r['pair']==pair]
            assert tuple(r['kind'] for r in block) == order
            assert all(tuple(r['order']) == order for r in block)
        for kind in kinds:
            subset = [r for r in selected if r['kind']==kind]
            summary = evidence['summary'][key][kind]
            assert summary['median_seconds'] == statistics.median(r['wall_seconds'] for r in subset)
            assert summary['max_peak_rss_kib'] == max(r['peak_rss_kib'] for r in subset)
            assert all(r['wall_seconds'] > 0 and r['peak_rss_kib'] > 0 for r in subset)
        fixture = evidence['fixtures'][key]
        assert int(fixture['object_counts']['packs']) > 0 if key.endswith('/packed') else int(fixture['object_counts']['packs']) == 0
        if key.endswith('/packed'): assert int(fixture['object_counts']['count']) == 0
        for name, target in [('below-cap',1048575),('above-cap',1048577)]:
            if key.startswith(name+'/'): assert fixture['calibration']['target_framed_bytes'] == target
        ratios = evidence['summary'][key]['paired_runtime_ratios']
        values = {(r['pair'],r['kind']):r['wall_seconds'] for r in selected}
        for label,a,b in [('batched_over_unbatched','batched','unbatched'),
                          ('singleton_over_batched','singleton','batched'),
                          ('singleton_over_unbatched','singleton','unbatched')]:
            assert ratios[label] == [values[i,a]/values[i,b] for i in range(1,repetitions+1)]
        paired = {kind:{r['pair']:r for r in selected if r['kind']==kind} for kind in kinds}
        check = dict(
            runtime=statistics.median(ratios['singleton_over_batched']) <= (0.9 if key.startswith('above-cap/') else 1.05),
            rss=max(r['peak_rss_kib'] for r in paired['singleton'].values()) <= 1.10*max(r['peak_rss_kib'] for r in paired['batched'].values()),
            subprocesses=all(paired['singleton'][i]['git_subprocesses'] <= paired['batched'][i]['git_subprocesses'] for i in range(1,repetitions+1)),
            bypass=not key.startswith('above-cap/') or all(r['singleton_bypasses'] > 0 and r['batch_disabled_transitions'] == 0 for r in paired['singleton'].values()))
        for r in selected:
            assert sum(r['request_sizes'].values()) == r['batch_calls']
            assert r['singleton_requests'] == r['request_sizes'].get('1',0)
            assert r['singleton_bypasses'] == (r['singleton_requests'] if r['kind']=='singleton' else 0)
        assert evidence['candidate_adoption_checks'][key] == check
    assert evidence['candidate_meets_rule'] == all(all(v.values()) for v in evidence['candidate_adoption_checks'].values())
    assert evidence['harness_sha256'] == hashlib.sha256((ROOT/'scripts/measure_singleton_bypass.py').read_bytes()).hexdigest()
    for path, value in evidence['dependency_sha256'].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == value, path
    for kind, path in [('unbatched',ROOT/'experiments/group_batching/baseline'),
                       ('batched',ROOT/'experiments/batching_limits/baseline'),('singleton',ROOT/'experiments/singleton_bypass/candidate')]:
        hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (path/'git_branch_atlas').glob('*.py')}
        assert evidence['source_sha256'][kind] == hashes
    current = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'git_branch_atlas').glob('*.py')}
    assert current in (evidence['source_sha256']['batched'], evidence['source_sha256']['singleton'])
    if not evidence['candidate_meets_rule']:
        assert current == evidence['source_sha256']['batched'], 'failed candidate must not replace production'
    print(json.dumps(dict(result='passed',measurements=len(rows),workload_storage_combinations=len(keys),
                          candidate_meets_rule=evidence['candidate_meets_rule'])))

if __name__ == '__main__': main()
