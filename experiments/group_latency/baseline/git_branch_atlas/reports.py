"""Versioned, offline branch and ancestry reports built on Git plumbing."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .git import GitError, run_git, safe

SEMANTICS = ("Counts describe commit ancestry, not patch equivalence: cherry-picked commits "
             "remain distinct. Ancestry does not establish conflict-free merging.")


def resolve(repo: Path, revision: str) -> str:
    """Resolve once; reject ambiguity instead of accepting Git's preferred ref."""
    if not revision or revision.startswith('-'):
        raise GitError("revision must be a non-option commit name or full object ID")
    result = run_git(repo, ["rev-parse", "--verify", "--end-of-options", revision + "^{commit}"], check=False)
    if result.returncode or result.stderr.strip():
        raise GitError(f"cannot unambiguously resolve {revision!r} to a commit; use refs/heads/NAME, "
                       "refs/tags/NAME or a full object ID. " + result.stderr.strip())
    return result.stdout.strip()


def history_state(repo: Path) -> tuple[bool, list[str]]:
    shallow = run_git(repo, ["rev-parse", "--is-shallow-repository"]).stdout.strip() == "true"
    graft = run_git(repo, ["rev-parse", "--git-path", "info/grafts"]).stdout.rstrip('\n')
    graft_path = Path(graft)
    if not graft_path.is_absolute():
        graft_path = repo / graft_path
    if graft_path.exists() and graft_path.stat().st_size:
        raise GitError("legacy info/grafts alters ancestry; inspect a copy without grafts")
    warnings = ["Shallow history: ancestry counts are unavailable. Obtain full history separately and rerun."] if shallow else []
    return shallow, warnings


def validate_history(repo: Path, oids: list[str]) -> None:
    if not oids:
        return
    # Traverse parents even when comparison would short-circuit at identical tips.
    # Disabling commit-graph acceleration ensures missing parent objects are seen.
    result = run_git(repo, ["rev-list", "--count", *dict.fromkeys(oids), "--"], check=False)
    if result.returncode:
        raise GitError("Incomplete or corrupt commit history; restore missing objects separately and rerun. "
                       "No objects were downloaded. " + result.stderr.strip())


def counts(repo: Path, left: str, right: str) -> tuple[int, int]:
    result = run_git(repo, ["rev-list", "--left-right", "--count", f"{left}...{right}", "--"])
    a, b = result.stdout.split()
    return int(a), int(b)


def worktrees(repo: Path) -> list[dict[str, Any]]:
    output = run_git(repo, ["worktree", "list", "--porcelain", "-z"]).stdout
    records = []
    for block in output.split('\0\0'):
        if not block:
            continue
        values = {}
        for field in block.split('\0'):
            key, _, value = field.partition(' ')
            values[key] = value
        records.append({"path": values['worktree'], "oid": values.get('HEAD'),
                        "branch": values.get('branch'), "bare": 'bare' in values,
                        "detached": 'detached' in values, "locked": 'locked' in values,
                        "lock_reason": values.get('locked'), "prunable": 'prunable' in values,
                        "prune_reason": values.get('prunable')})
    return records


def summary(repo: Path) -> dict[str, Any]:
    shallow, warnings = history_state(repo)
    symbolic = run_git(repo, ["symbolic-ref", "--quiet", "HEAD"], check=False)
    head_ref = symbolic.stdout.strip() if symbolic.returncode == 0 else None
    head = run_git(repo, ["rev-parse", "--verify", "HEAD^{commit}"], check=False)
    head_oid = head.stdout.strip() if head.returncode == 0 else None
    if head_oid is None and head_ref is None:
        raise GitError("HEAD cannot be resolved; repair the repository HEAD separately")
    trees = worktrees(repo)
    fmt = '%(refname)%00%(objectname)%00%(upstream)%00%(upstream:remotename)%00%(upstream:remoteref)'
    rows = run_git(repo, ['for-each-ref', '--format=' + fmt, 'refs/heads/']).stdout
    local = []
    tips = [head_oid] if head_oid else []
    for row in rows.split('\n'):
        if not row:
            continue
        ref, oid, upstream, remote, remote_ref = row.split('\0')
        upstream_oid = None
        state = 'none'
        if upstream:
            check = run_git(repo, ['show-ref', '--verify', '--hash', upstream], check=False)
            if check.returncode == 0:
                upstream_oid = resolve(repo, upstream)
                state = 'present'
                tips.append(upstream_oid)
            else:
                state = 'missing'
        # Preserve upstream configuration even if its remote/refspec cannot map a tracking ref.
        name = ref.removeprefix('refs/heads/')
        configured_remote = run_git(repo, ['config', '--get', f'branch.{name}.remote'], check=False).stdout.rstrip('\n')
        configured_merge = run_git(repo, ['config', '--get-all', f'branch.{name}.merge'], check=False).stdout.splitlines()
        if state == 'none' and (configured_remote or configured_merge):
            state = 'unmapped'
        tips.append(oid)
        local.append({'name': name, 'ref': ref, 'oid': oid, 'current': ref == head_ref,
                      'upstream': {'ref': upstream or None, 'oid': upstream_oid, 'state': state,
                                   'remote': configured_remote or remote or None,
                                   'merge_refs': configured_merge},
                      'ahead': None, 'behind': None,
                      'worktrees': [tree['path'] for tree in trees if tree['branch'] == ref]})
    validate_history(repo, tips)
    if head_ref and head_oid is None and any(item['ref'] == head_ref for item in local):
        raise GitError('HEAD branch exists but its commit is unavailable; restore missing objects separately')
    if not shallow:
        for branch in local:
            if branch['upstream']['oid']:
                branch['ahead'], branch['behind'] = counts(repo, branch['oid'], branch['upstream']['oid'])
    return {'schema_version': 1, 'command': 'summary',
            'bare': run_git(repo, ['rev-parse', '--is-bare-repository']).stdout.strip() == 'true',
            'head': {'state': 'unborn' if head_oid is None else ('attached' if head_ref else 'detached'),
                     'ref': head_ref, 'oid': head_oid},
            'history': {'complete': not shallow, 'shallow': shallow},
            'warnings': warnings, 'semantics': SEMANTICS, 'branches': local, 'worktrees': trees}


def compare(repo: Path, left: str, right: str, limit: int) -> dict[str, Any]:
    shallow, _ = history_state(repo)
    if shallow:
        raise GitError('Shallow history cannot establish divergence or merge bases. Obtain full history separately and rerun.')
    a, b = resolve(repo, left), resolve(repo, right)
    validate_history(repo, [a, b])
    a_count, b_count = counts(repo, a, b)
    bases = run_git(repo, ['merge-base', '--all', a, b], check=False)
    if bases.returncode not in (0, 1):
        raise GitError('merge-base failed: ' + bases.stderr.strip())
    def side(label: str, oid: str, other: str, count: int) -> dict[str, Any]:
        listing = run_git(repo, ['log', '--no-notes', '--topo-order', f'--max-count={limit}',
                                '--format=%H%x00%s', oid, '^' + other, '--']).stdout
        commits = []
        for row in listing.split('\n'):
            if row:
                commit, _, subject = row.partition('\0')
                commits.append({'oid': commit, 'subject': subject})
        return {'input': label, 'oid': oid, 'unique_count': count, 'commits': commits,
                'truncated': count > len(commits)}
    return {'schema_version': 1, 'command': 'compare', 'history': {'complete': True, 'shallow': False},
            'left': side(left, a, b, a_count), 'right': side(right, b, a, b_count),
            'merge_bases': sorted(bases.stdout.split()), 'unrelated': bases.returncode == 1,
            'limit_per_side': limit, 'warnings': [], 'semantics': SEMANTICS}


def render(report: dict[str, Any]) -> str:
    lines = []
    if report['command'] == 'summary':
        head = report['head']
        lines.append(f"Branch summary  HEAD: {head['state']} {safe(head['ref'] or head['oid'] or '')}")
        if report['bare']:
            lines.append('Bare repository')
        for branch in report['branches']:
            upstream = branch['upstream']
            delta = f"+{branch['ahead']}/-{branch['behind']}" if branch['ahead'] is not None else 'counts unavailable'
            lines.append(f"{'*' if branch['current'] else ' '} {safe(branch['name'])}  {branch['oid']}  "
                         f"upstream: {safe(upstream['ref'] or upstream['remote'] or 'none')} [{upstream['state']}]  {delta}")
        if not report['branches']:
            lines.append('  (no local branches)')
        lines.append('Worktrees')
        for tree in report['worktrees']:
            flags = [key for key in ('bare', 'detached', 'locked', 'prunable') if tree[key]]
            lines.append(f"  {safe(tree['path'])}  {safe(tree['branch'] or tree['oid'] or '')}  {', '.join(flags)}")
            for key in ('lock_reason', 'prune_reason'):
                if tree[key]:
                    lines.append('    ' + safe(tree[key]))
    else:
        lines.append('Commit comparison')
        for name in ('left', 'right'):
            side = report[name]
            lines.append(f"{name}: {safe(side['input'])} -> {side['oid']}  unique: {side['unique_count']}")
            for commit in side['commits']:
                lines.append(f"  {commit['oid']} {safe(commit['subject'])}")
            if side['truncated']:
                lines.append(f"  (showing {len(side['commits'])} of {side['unique_count']}; raise --max-count)")
        lines.append('Merge bases: ' + (', '.join(report['merge_bases']) or 'none (unrelated histories)'))
    lines.extend('Warning: ' + safe(warning) for warning in report['warnings'])
    lines.append(report['semantics'])
    return '\n'.join(lines)
