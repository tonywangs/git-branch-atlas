"""Explicit patch-series review; versioned, bounded heuristics, no forced mapping."""
from __future__ import annotations

from collections import Counter
import heapq
import json
import math
import os
from pathlib import Path
import re
import time

from .git import GitError, safe
from .patches import Budget, Incomplete, NOTICE, inspect_patch

ALGORITHM = 'signed-line-dice-path-jaccard-v1'
FEATURE_LIMIT = 20000
TOP = 5


def check_time(budget):
    if time.monotonic() >= budget.deadline:
        raise Incomplete('runtime limit')


def features(diff, paths, budget):
    """Count signed changed lines, deleting ASCII whitespace; ignore context."""
    lines = Counter()
    in_hunk = False
    count = 0
    for i, line in enumerate(diff.split(b'\n')):
        if i % 1024 == 0:
            check_time(budget)
        if line.startswith(b'diff --git '):
            in_hunk = False
        elif line.startswith(b'@@ '):
            in_hunk = True
        elif in_hunk and line[:1] in (b'+', b'-'):
            count += 1
            if count > FEATURE_LIMIT:
                raise Incomplete('changed-line feature limit')
            lines[line[:1] + line[1:].translate(None, b' \t\r\n\v\f')] += 1
    return lines, frozenset(paths), count


def score(a, b):
    """Integer basis points; one floor after the exact rational weighted sum."""
    x, p, nx = a
    y, q, ny = b
    if len(x) > len(y):
        x, y = y, x
    overlap = sum(min(n, y.get(k, 0)) for k, n in x.items())
    if not overlap:
        return 0
    common, union = len(p & q), len(p | q)
    return (16000 * overlap * union + 2000 * common * (nx + ny)) // ((nx + ny) * union)


def ordered(rows):
    """Parents before children; lexicographically smallest ready full OID first."""
    parents = {row.split()[0]: row.split()[1:] for row in rows}
    degree = {oid: sum(p in parents for p in ps) for oid, ps in parents.items()}
    children = {oid: [] for oid in parents}
    for oid, ps in parents.items():
        for p in ps:
            if p in children:
                children[p].append(oid)
    ready = [oid for oid in parents if degree[oid] == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        oid = heapq.heappop(ready)
        result.append((oid, parents[oid]))
        for child in children[oid]:
            degree[child] -= 1
            if degree[child] == 0:
                heapq.heappush(ready, child)
    if len(result) != len(parents):
        raise Incomplete('invalid cyclic history')
    return result


def evidence(a, b):
    """A bounded multiset difference of normalized features, not an applyable diff."""
    def sample(items):
        keys = sorted(items)
        return dict(items=[dict(text=k[:160].decode('utf-8', 'backslashreplace'),
                                count=items[k], bytes=len(k), truncated=len(k) > 160)
                           for k in keys[:8]],
                    omitted_items=max(0, len(keys) - 8))
    return dict(kind='normalized-feature-difference-v1',
                source_only=sample(a[0] - b[0]), counterpart_only=sample(b[0] - a[0]),
                paths_source_only=sample(Counter(a[1] - b[1])),
                paths_counterpart_only=sample(Counter(b[1] - a[1])))


def series_compare(repo: Path, left_base: str, left_tip: str, right_base: str, right_tip: str,
                   max_count=30, max_bytes=32 * 1024 * 1024, seconds=30.0,
                   max_comparisons=10000, threshold=5000):
    if not 1 <= max_count <= 1000 or not 1 <= max_bytes <= 256 * 1024 * 1024:
        raise GitError('series limits require 1..1000 commits and 1..268435456 inspection bytes')
    if not math.isfinite(seconds) or not 0 < seconds <= 3600:
        raise GitError('series timeout must be finite, greater than 0, and at most 3600 seconds')
    if not 0 <= max_comparisons <= 100000 or not 1 <= threshold <= 10000:
        raise GitError('series requires 0..100000 comparisons and threshold 1..10000')
    budget = Budget(repo, seconds, max_bytes)
    budget.run(['rev-parse', '--git-dir'])
    graft = os.fsdecode(budget.run(['rev-parse', '--git-path', 'info/grafts']).rstrip(b'\n'))
    graft = Path(graft) if Path(graft).is_absolute() else repo / graft
    if graft.exists() and graft.stat().st_size:
        raise GitError('Legacy grafts are unsupported')

    def resolve(ref):
        if not ref or ref.startswith('-') or len(os.fsencode(ref)) > 4096:
            raise GitError('revision must be a non-option commit name or ID of at most 4096 bytes')
        oid = budget.run(['rev-parse', '--verify', '--end-of-options', ref + '^{commit}'], strict=True).decode('ascii').strip()
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid):
            raise GitError('revision must resolve to one full commit ID')
        return dict(revision=ref, oid=oid)

    endpoints = [resolve(ref) for ref in (left_base, left_tip, right_base, right_tip)]
    report = dict(schema_version=1, kind='series', complete=True, warnings=[], matches=[],
                  notice=NOTICE + ' Heuristic candidates are not equivalence; splits and squashes are not mapped.',
                  algorithm=ALGORITHM, threshold=threshold, score_scale=10000,
                  limits=dict(commits_per_side=max_count, inspection_bytes=max_bytes, seconds=seconds,
                              comparisons=max_comparisons, candidates_per_commit=TOP,
                              changed_lines_per_commit=FEATURE_LIMIT),
                  comparison_count=0)
    history_error = None
    try:
        if budget.run(['rev-parse', '--is-shallow-repository']).strip() == b'true':
            raise Incomplete('shallow history: membership and patch IDs withheld')
        budget.run(['rev-list', '--count', *[e['oid'] for e in endpoints], '--'])
    except Incomplete as exc:
        history_error = str(exc)
        report['warnings'].append(history_error)
        report['complete'] = False
    feature_map, commits, groups = {}, {}, {'left': {}, 'right': {}}
    for side, base, tip in [('left', *endpoints[:2]), ('right', *endpoints[2:])]:
        entry = report[side] = dict(base=base, tip=tip, enumeration_complete=False, commits=[])
        try:
            rows = budget.run(['rev-list', '--topo-order', '--parents', f'--max-count={max_count + 1}',
                               tip['oid'], '^' + base['oid'], '--'], cap=8 * 1024 * 1024).decode('ascii').splitlines()
            entry['enumeration_complete'] = len(rows) <= max_count and history_error is None
            if len(rows) > max_count:
                report['warnings'].append(side + ': commit limit; newest topological sample only')
            report['complete'] &= entry['enumeration_complete']
            for oid, parents in ordered(rows[:max_count]):
                item = dict(oid=oid, parents=parents, status='unclassified')
                entry['commits'].append(item)
                commits[side, oid] = item
                if history_error:
                    item['reason'] = history_error
                    continue
                if len(parents) > 1:
                    item.update(status='excluded', reason='merge')
                    continue
                try:
                    reason, patch_id, diff, paths = inspect_patch(budget, oid, max_bytes)
                    if reason:
                        item.update(status='excluded', reason=reason)
                        continue
                    item['patch_id'] = patch_id
                    groups[side].setdefault(patch_id, []).append(oid)
                    # Feature exhaustion must not discard a successfully computed exact ID.
                    try:
                        feature_map[side, oid] = features(diff, paths, budget)
                    except Incomplete as exc:
                        item['reason'] = str(exc)
                except Incomplete as exc:
                    item['reason'] = str(exc)
                    report['complete'] = False
        except Incomplete as exc:
            report['warnings'].append(side + ': ' + str(exc))
            report['complete'] = False

    exact = set()
    for pid in sorted(groups['left'].keys() & groups['right'].keys()):
        report['matches'].append(dict(patch_id=pid, left=groups['left'][pid], right=groups['right'][pid]))
        for side in ('left', 'right'):
            for oid in groups[side][pid]:
                exact.add((side, oid))
                commits[side, oid].update(status='patch_id_match')
                commits[side, oid].pop('reason', None)
    pools = {}
    for side in ('left', 'right'):
        pools[side] = []
        for item in report[side]['commits']:
            key = side, item['oid']
            if key in exact or item['status'] == 'excluded':
                continue
            if key not in feature_map:
                report['complete'] = False
                continue
            pools[side].append(key)
            item.update(candidates=[], candidate_count=0, top_tie_count=0)
    # Store at most five scores per commit, including both directions; never assign pairs.
    best = {key: [] for side in pools.values() for key in side}
    try:
        for left in pools['left']:
            for right in pools['right']:
                check_time(budget)
                if report['comparison_count'] >= max_comparisons:
                    raise Incomplete('candidate comparison limit')
                value = score(feature_map[left], feature_map[right])
                report['comparison_count'] += 1
                if value < threshold:
                    continue
                for key, other in ((left, right), (right, left)):
                    item, ranked = commits[key], best[key]
                    item['candidate_count'] += 1
                    if not ranked or value > ranked[0][0]:
                        item['top_tie_count'] = 1
                    elif value == ranked[0][0]:
                        item['top_tie_count'] += 1
                    ranked.append((value, other[1]))
                    ranked.sort(key=lambda pair: (-pair[0], pair[1]))
                    del ranked[TOP:]
    except Incomplete as exc:
        report['complete'] = False
        report['warnings'].append(str(exc))
    for key, ranked in best.items():
        item = commits[key]
        item.update(status='heuristic_candidates' if ranked else ('no_candidate' if report['complete'] else 'incomplete_search'),
                    search_complete=report['complete'], ambiguous=item['candidate_count'] > 1,
                    candidates_omitted=max(0, item['candidate_count'] - TOP))
        last_score, rank = None, 0
        for index, (value, oid) in enumerate(ranked):
            if value != last_score:
                rank = index + 1
            last_score = value
            other = ('right' if key[0] == 'left' else 'left', oid)
            item['candidates'].append(dict(oid=oid, score=value, rank=rank,
                                            evidence=evidence(feature_map[key], feature_map[other])))
    report['inspection_bytes_read'] = budget.bytes
    return report


def format_series_report(report, as_json=False, max_output=2 * 1024 * 1024):
    if not 1024 <= max_output <= 16 * 1024 * 1024:
        raise GitError('series output limit must be 1024..16777216 bytes')
    if as_json:
        output = json.dumps(report, ensure_ascii=True, indent=2)
    else:
        lines = ['Patch series: ' + ('complete' if report['complete'] else 'INCOMPLETE'), report['notice'],
                 f"Scorer: {report['algorithm']}; threshold {report['threshold']}/10000; comparisons {report['comparison_count']}"]
        for group in report['matches']:
            lines.append('Patch-ID group ' + group['patch_id'] + ' left=' + ','.join(group['left']) + ' right=' + ','.join(group['right']))
        for side in ('left', 'right'):
            entry = report[side]
            lines.append(side + ': ' + entry['base']['oid'] + '..' + entry['tip']['oid'])
            for item in entry['commits']:
                lines.append('  ' + item['oid'] + ' ' + item['status'] + ' ' + item.get('reason', ''))
                if 'candidate_count' in item:
                    lines.append(f"    candidates={item['candidate_count']} omitted={item['candidates_omitted']} ambiguous={item['ambiguous']} top_ties={item['top_tie_count']} search_complete={item['search_complete']}")
                for candidate in item.get('candidates', []):
                    lines.append(f"    rank {candidate['rank']}: {candidate['oid']} score={candidate['score']}/10000")
                    lines.append('      normalized evidence (not an applicable patch):')
                    for category, label in [('source_only', 'lines only in source'),
                                            ('counterpart_only', 'lines only in counterpart'),
                                            ('paths_source_only', 'paths only in source'),
                                            ('paths_counterpart_only', 'paths only in counterpart')]:
                        sample = candidate['evidence'][category]
                        lines.append('        ' + label + ':')
                        if not sample['items']:
                            lines.append('          (none)')
                        for value in sample['items']:
                            suffix = f" [truncated from {value['bytes']} bytes]" if value['truncated'] else ''
                            lines.append(f"          {value['count']} x " + value['text'] + suffix)
                        if sample['omitted_items']:
                            lines.append(f"          ... {sample['omitted_items']} distinct items omitted")
        lines.extend('Warning: ' + warning for warning in report['warnings'])
        output = '\n'.join(safe(line) for line in lines)
    if len((output + '\n').encode('utf-8')) > max_output:
        raise GitError('output byte limit; no report emitted (increase --max-output-bytes)')
    return output
