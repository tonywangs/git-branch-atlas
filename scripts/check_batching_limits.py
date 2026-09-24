#!/usr/bin/env python3
"""Audit persisted batching-limit evidence independently of the timing driver."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',type=Path,default=ROOT/'results/batching-limits.json')
    args = parser.parse_args()
    evidence = json.loads(args.results.read_text())
    rows = evidence['measurements']
    raw = [json.loads(line) for line in args.results.with_suffix('.rows.jsonl').read_text().splitlines()]
    assert rows == raw
    kinds = ('unbatched','batched','quarter')
    cases = ('small','many-files','below-cap','above-cap','batch-overflow','mixed','repeated','limited')
    keys = {c+'/'+storage for c in cases for storage in ('loose','packed')}
    assert set(evidence['fixtures']) == keys
    assert len(rows) == 288
    for key in keys:
        selected = [r for r in rows if r['workload']==key]
        assert len(selected) == 18
        assert len({r['sha256'] for r in selected}) == 1
        assert all(r['complete'] == (not key.startswith('limited/')) for r in selected)
        for field in ('structural_windows','inspected_groups','comparison_count','comparisons_possible',
                      'candidate_count','candidates_omitted','inspection_bytes_read'):
            assert len({r[field] for r in selected}) == 1, (key,field)
        for pair, order in enumerate(itertools.permutations(kinds),1):
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
                          ('quarter_over_batched','quarter','batched'),
                          ('quarter_over_unbatched','quarter','unbatched')]:
            assert ratios[label] == [values[i,a]/values[i,b] for i in range(1,7)]
        check = statistics.median(ratios['quarter_over_batched']) <= (0.9 if key.startswith('batch-overflow/') else 1.05)
        assert evidence['candidate_adoption_checks'][key] == check
    assert evidence['candidate_meets_rule'] == all(evidence['candidate_adoption_checks'].values())
    assert evidence['harness_sha256'] == hashlib.sha256((ROOT/'scripts/measure_batching_limits.py').read_bytes()).hexdigest()
    for path, value in evidence['dependency_sha256'].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == value, path
    for kind, path in [('unbatched',ROOT/'experiments/group_batching/baseline'),
                       ('batched',ROOT/'experiments/batching_limits/baseline'),('quarter',ROOT/'experiments/batching_limits/baseline')]:
        hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (path/'git_branch_atlas').glob('*.py')}
        assert evidence['source_sha256'][kind] == hashes
    current = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'git_branch_atlas').glob('*.py')}
    assert current == evidence['source_sha256']['batched']
    print(json.dumps(dict(result='passed',measurements=len(rows),workload_storage_combinations=len(keys),
                          candidate_meets_rule=evidence['candidate_meets_rule'])))

if __name__ == '__main__': main()
