"""Bounded singleton versus parent-linked endpoint comparisons (series schema 2)."""
from __future__ import annotations

from .patches import Incomplete, NOTICE, inspect_patch
from .series import ALGORITHM, FEATURE_LIMIT, check_time, evidence, features, score

GROUP_NOTICE = (NOTICE + ' Group candidates compare normalized endpoint patches, not behavior or '
                'proof of historical correspondence. Overlapping and competing candidates are retained; '
                'no unique pairing or globally consistent assignment is inferred.')


def windows(commits):
    """Enumerate linear windows by topological last member, then length 2, 3, 4.

    Never infer adjacency from display order: follow sole parents in the sample.
    At most 3*N windows; merges cannot be members or be crossed.
    """
    by_oid = {c['oid']: c for c in commits}
    for last in commits:
        chain = [last]
        while len(chain) < 4:
            first = chain[0]
            if len(first['parents']) != 1 or len(last['parents']) > 1:
                break
            parent = by_oid.get(first['parents'][0])
            if parent is None or len(parent['parents']) > 1:
                break
            chain.insert(0, parent)
            yield list(chain)


def compare_groups(report, budget, feature_map, max_groups, max_comparisons, max_candidates):
    """Append an independently bounded extension after preserving individual results."""
    result = dict(schema_version=1, algorithm=ALGORITHM,
                  scope='one commit versus 2..4 sole-parent-linked commits inside sampled ranges',
                  notice=GROUP_NOTICE, complete=True, warnings=[],
                  limits=dict(groups=max_groups, comparisons=max_comparisons, candidates=max_candidates,
                              group_lengths=[2, 3, 4], changed_lines_per_patch=FEATURE_LIMIT,
                              evidence_keys_per_category=8, evidence_bytes_per_key=160),
                  search_order='group side left then right; topological last member then length; opposite singles in series order',
                  groups=[], singles=[], candidates=[], candidate_count=0, candidates_omitted=0,
                  comparison_count=0, comparisons_possible=0, comparisons_unsearched=0,
                  structural_windows=0, windows_omitted=0, sampled_scope={}, excluded_members=[])
    report['schema_version'] = 2
    report['individual_complete'] = report['complete']
    report['notice'] = GROUP_NOTICE
    report['group_comparison'] = result
    start_bytes = budget.bytes
    empty_tree = None

    def base_of(first):
        nonlocal empty_tree
        if first['parents']:
            return first['parents'][0], 'commit'
        if empty_tree is None:
            empty_tree = budget.run(['hash-object', '-t', 'tree', '--stdin'], data=b'').decode().strip()
        return empty_tree, 'empty_tree'

    def incomplete(reason):
        result['complete'] = False
        if reason not in result['warnings']:
            result['warnings'].append(reason)

    group_features, single_features = {}, {}
    all_windows = []
    for side in ('left', 'right'):
        entry = report[side]
        result['sampled_scope'][side] = dict(commits=len(entry['commits']),
                                            enumeration_complete=entry['enumeration_complete'],
                                            structural_windows=0)
        if not entry['enumeration_complete']:
            incomplete(side + ': incomplete commit enumeration; windows outside sample unknown')
        for item in entry['commits']:
            key = (side, item['oid'])
            if item['status'] == 'excluded':
                result['excluded_members'].append(dict(side=side, oid=item['oid'], reason=item['reason']))
            elif key not in feature_map:
                incomplete(side + ': singleton features unavailable: ' + item.get('reason', 'changed-line features not retained'))
            if key in feature_map:
                try:
                    base, kind = base_of(item)
                    single = dict(side=side, oid=item['oid'], base_oid=base, base_kind=kind,
                                  tip_oid=item['oid'], patch_id=item['patch_id'], candidate_count=0)
                    result['singles'].append(single)
                    single_features[key] = feature_map[key]
                except Incomplete as exc:
                    incomplete(str(exc))
        for chain in windows(entry['commits']):
            all_windows.append((side, chain))
            result['sampled_scope'][side]['structural_windows'] += 1
    result['structural_windows'] = len(all_windows)
    result['windows_omitted'] = max(0, len(all_windows) - max_groups)
    if result['windows_omitted']:
        incomplete('group enumeration limit')
    for side, chain in all_windows[:max_groups]:
        members = [c['oid'] for c in chain]
        group = dict(id=side + ':' + members[0] + '..' + members[-1], side=side,
                     members=members, base_oid=None, base_kind=None, tip_oid=members[-1],
                     status='unclassified', candidate_count=0)
        result['groups'].append(group)
        try:
            check_time(budget)
            base, kind = base_of(chain[0])
            group.update(base_oid=base, base_kind=kind)
            # Transient unsupported changes cannot disappear behind supported endpoints.
            excluded = [c for c in chain if c['status'] == 'excluded' and c['reason'] != 'empty']
            unknown = [c for c in chain if c['status'] != 'excluded' and (side, c['oid']) not in feature_map]
            if excluded:
                group.update(status='excluded', reason='unsupported member: ' + excluded[0]['reason'],
                             excluded_members=[c['oid'] for c in excluded])
                continue
            if unknown:
                raise Incomplete('member inspection or features unavailable')
            reason, pid, diff, paths = inspect_patch(budget, group['tip_oid'], budget.max_bytes, base=base)
            if reason:
                group.update(status='excluded', reason='empty aggregate patch' if reason == 'empty' else reason)
                continue
            f = features(diff, paths, budget)
            group_features[group['id']] = f
            group.update(status='eligible', patch_id=pid, changed_lines=f[2])
        except Incomplete as exc:
            group['reason'] = str(exc)
            incomplete(str(exc))
    singles = {side: [s for s in result['singles'] if s['side'] == side] for side in ('left', 'right')}
    eligible = [g for g in result['groups'] if g['status'] == 'eligible']
    result['comparisons_possible'] = sum(len(singles['right' if g['side'] == 'left' else 'left']) for g in eligible)
    try:
        for group in eligible:
            other = 'right' if group['side'] == 'left' else 'left'
            for single in singles[other]:
                check_time(budget)
                if result['comparison_count'] >= max_comparisons:
                    raise Incomplete('group comparison limit')
                a = single_features[other, single['oid']]
                b = group_features[group['id']]
                value = score(a, b)
                agreement = single['patch_id'] == group['patch_id']
                result['comparison_count'] += 1
                if not agreement and value < report['threshold']:
                    continue
                result['candidate_count'] += 1
                single['candidate_count'] += 1
                group['candidate_count'] += 1
                if len(result['candidates']) < max_candidates:
                    result['candidates'].append(dict(single_side=other, single_oid=single['oid'],
                        group_id=group['id'], score=value, normalized_patch_agreement=agreement,
                        evidence=evidence(a, b)))
    except Incomplete as exc:
        incomplete(str(exc))
    result['comparisons_unsearched'] = result['comparisons_possible'] - result['comparison_count']
    result['candidates_omitted'] = result['candidate_count'] - len(result['candidates'])
    if result['candidates_omitted']:
        incomplete('group candidate output limit; observed count includes omitted candidates')
    # Overlaps refer to all observed candidates, even those outside the export cap.
    for side in ('left', 'right'):
        active = [g for g in eligible if g['side'] == side and g['candidate_count']]
        member_counts = {}
        for group in active:
            for oid in group['members']:
                member_counts[oid] = member_counts.get(oid, 0) + 1
        for group in active:
            group['overlapping_candidate_groups'] = any(member_counts[o] > 1 for o in group['members'])
    group_map = {g['id']: g for g in eligible}
    single_map = {(s['side'], s['oid']): s for s in result['singles']}
    for candidate in result['candidates']:
        group = group_map[candidate['group_id']]
        single = single_map[candidate['single_side'], candidate['single_oid']]
        candidate.update(ambiguous=single['candidate_count'] > 1 or group['candidate_count'] > 1
                         or group.get('overlapping_candidate_groups', False))
    result['inspection_bytes_read'] = budget.bytes - start_bytes
    report['complete'] = report['individual_complete'] and result['complete']
    report['inspection_bytes_read'] = budget.bytes
