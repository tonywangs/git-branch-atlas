"""Bounded, offline stable patch-ID comparison. No repository writes."""
from __future__ import annotations

from collections import OrderedDict
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import tempfile
import time

from .git import GitError, safe

# Explicit text diff settings; renames are delete/add. Attributes cannot enable
# external programs or binary suppression under --text / --no-textconv.
DIFF = ['--no-ext-diff', '--no-textconv', '--text', '--no-renames', '--no-relative',
        '--output-indicator-new=+', '--output-indicator-old=-', '--output-indicator-context= ',
        '--diff-algorithm=myers', '--no-indent-heuristic', '--unified=3',
        '--inter-hunk-context=0', '--no-function-context', '--full-index',
        '--src-prefix=a/', '--dst-prefix=b/', '--no-color', '--submodule=short',
        '--ignore-submodules=none']
NOTICE = ('Stable patch IDs ignore whitespace and hunk line numbers. Matches do not establish '
          'semantic equivalence, current branch contents, safe cherry-picking, or conflict-free merging.')


class Incomplete(GitError):
    pass


class Budget:
    """Stream bounded pipes; enforce one deadline across all Git subprocesses."""
    BLOB_CACHE_BYTES = 4 * 1024 * 1024
    BLOB_CACHE_ENTRIES = 1024
    BLOB_CACHE_ITEM_BYTES = 256 * 1024

    def __init__(self, repo: Path, seconds: float, max_bytes: int, *, cache_blobs=False):
        self.repo = repo
        self.deadline = time.monotonic() + seconds
        self.max_bytes = max_bytes
        self.bytes = 0
        # Only immutable, successfully read full-OID blobs, local to this budget.
        # No failures, refs, diffs, attributes or patch IDs are memoized.
        self._cache_blobs = cache_blobs
        self._blob_cache = OrderedDict()
        self._blob_cache_bytes = 0
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_PAGER='cat', GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0',
                        GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='', GIT_NO_REPLACE_OBJECTS='1',
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_ATTR_NOSYSTEM='1', LC_ALL='C')

    def run(self, args, *, data=None, inspection=False, cap=1024 * 1024, strict=False):
        if inspection and self.bytes >= self.max_bytes:
            raise Incomplete('inspection byte limit')
        if time.monotonic() >= self.deadline:
            raise Incomplete('runtime limit')
        blob = (args[2] if self._cache_blobs and inspection and data is None
                and len(args) == 3 and args[:2] == ['cat-file', 'blob']
                and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', args[2]) else None)
        cached = self._blob_cache.get(blob)
        if cached is not None and len(cached) <= cap and self.bytes + len(cached) <= self.max_bytes:
            # Replay logical inspection work, including repeated reads. Near a
            # byte/output boundary use the original streaming path so its exact
            # failure precedence and partial-byte accounting remain unchanged.
            self.bytes += len(cached)
            self._blob_cache.move_to_end(blob)
            return cached
        argv = ['git', '--no-pager', '-C', str(self.repo),
                '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.commitGraph=false',
                '-c', 'core.attributesFile=' + os.devnull, '-c', 'diff.orderFile=' + os.devnull,
                '-c', 'diff.suppressBlankEmpty=false', '-c', 'diff.mnemonicPrefix=false',
                '-c', 'diff.relative=false', '-c', 'diff.noprefix=false', '-c', 'core.quotePath=true',
                '-c', 'color.ui=false', *args]
        # A bounded input file avoids stdin/stdout pipe deadlocks for patch-id.
        with tempfile.TemporaryFile() as source:
            if data is not None:
                source.write(data)
                source.seek(0)
            with subprocess.Popen(argv, stdin=source, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=self.env) as process:
                streams = [bytearray(), bytearray()]
                try:
                    with selectors.DefaultSelector() as poll:
                        poll.register(process.stdout, selectors.EVENT_READ, 0)
                        poll.register(process.stderr, selectors.EVENT_READ, 1)
                        while poll.get_map():
                            remaining = self.deadline - time.monotonic()
                            if remaining <= 0:
                                raise Incomplete('runtime limit')
                            ready = poll.select(min(remaining, 0.1))
                            for key, _ in ready:
                                read_size = min(65536, self.max_bytes - self.bytes + 1) if inspection and key.data == 0 else 65536
                                chunk = os.read(key.fd, read_size)
                                if not chunk:
                                    poll.unregister(key.fileobj)
                                    continue
                                index = key.data
                                if inspection and index == 0:
                                    self.bytes += len(chunk)
                                    if self.bytes > self.max_bytes:
                                        raise Incomplete('inspection byte limit')
                                if len(streams[index]) + len(chunk) > (cap if index == 0 else 65536):
                                    raise Incomplete('subprocess output limit')
                                streams[index].extend(chunk)
                    process.wait(timeout=max(0.001, self.deadline - time.monotonic()))
                except BaseException as exc:
                    process.kill()
                    process.wait()
                    if isinstance(exc, subprocess.TimeoutExpired):
                        raise Incomplete('runtime limit') from exc
                    raise
                if process.returncode or (strict and streams[1]):
                    # Do not echo potentially huge/untrusted Git diagnostics.
                    raise Incomplete('Git failed (missing object, invalid revision, or repository error)')
                output = bytes(streams[0])
                if (blob is not None and not streams[1]
                        and len(output) <= min(self.BLOB_CACHE_ITEM_BYTES, self.BLOB_CACHE_BYTES)):
                    old = self._blob_cache.pop(blob, b'')
                    self._blob_cache_bytes -= len(old)
                    while self._blob_cache and (len(self._blob_cache) >= self.BLOB_CACHE_ENTRIES
                            or self._blob_cache_bytes + len(output) > self.BLOB_CACHE_BYTES):
                        _, evicted = self._blob_cache.popitem(last=False)
                        self._blob_cache_bytes -= len(evicted)
                    self._blob_cache[blob] = output
                    self._blob_cache_bytes += len(output)
                return output


def inspect_patch(budget, oid, max_bytes, *, base=None):
    """Return exclusion or stable ID, canonical diff and raw changed paths."""
    endpoints = [oid] if base is None else [base, oid]
    raw = budget.run(['diff-tree', '--root', '--no-commit-id', '-r', '--raw', '-z',
                      '--no-renames', '--no-abbrev', '--ignore-submodules=none', *endpoints, '--'],
                     inspection=True, cap=max_bytes)
    if not raw:
        return 'empty', None, None, None
    fields = raw.split(b'\0')
    blobs = set()
    reason = None
    for header in fields[:-1:2]:
        oldmode, newmode, old, new, status = header.split()
        oldmode = oldmode.lstrip(b':')
        modes = {oldmode, newmode} - {b'000000'}
        if not modes <= {b'100644', b'100755'}:
            reason = 'symlink, submodule, or unsupported file mode'
        elif len(modes) > 1:
            reason = 'executable mode change'
        blobs.update(blob.decode('ascii') for blob in (old, new) if set(blob) != {48})
    if reason:
        return reason, None, None, None
    for blob in sorted(blobs):
        content = budget.run(['cat-file', 'blob', blob], inspection=True, cap=max_bytes)
        if b'\0' in content:
            reason = 'binary (NUL in a changed blob)'
            break
    if reason:
        return reason, None, None, None
    hint = getattr(budget, '_patch_batch', {}).get((oid, base))
    if (hint is not None and time.monotonic() < budget.deadline
            and budget.bytes < budget.max_bytes
            and len(hint[0]) <= min(max_bytes, budget.max_bytes - budget.bytes)):
        # Speculation never charges inspection work. Replay the original diff's
        # logical bytes only here, after raw metadata and blobs have passed in
        # their original order. At a boundary retain the original streaming path.
        diff, patch_id = hint
        budget.bytes += len(diff)
        if time.monotonic() >= budget.deadline:
            raise Incomplete('runtime limit')
        return None, patch_id, diff, set(fields[1:-1:2])
    diff = budget.run(['diff-tree', '--root', '--no-commit-id', '-r', '-p', *DIFF, *endpoints, '--'],
                      inspection=True, cap=max_bytes)
    value = budget.run(['patch-id', '--stable'], data=diff).split()
    if len(value) != 2:
        raise Incomplete('patch-id produced no single ID')
    patch_id = value[0].decode('ascii')
    return None, patch_id, diff, set(fields[1:-1:2])


def patch_compare(repo: Path, left: str, right: str, max_count=30,
                  max_bytes=32 * 1024 * 1024, seconds=30.0):
    if not 1 <= max_count <= 10000 or not 1 <= max_bytes <= 256 * 1024 * 1024:
        raise GitError('patch limits require 1..10000 commits and 1..268435456 inspection bytes')
    if not math.isfinite(seconds) or not 0 < seconds <= 3600:
        raise GitError('patch timeout must be finite, greater than 0, and at most 3600 seconds')
    budget = Budget(repo, seconds, max_bytes)
    budget.run(['rev-parse', '--git-dir'])
    graft = os.fsdecode(budget.run(['rev-parse', '--git-path', 'info/grafts']).rstrip(b'\n'))
    graft_path = Path(graft) if Path(graft).is_absolute() else repo / graft
    if graft_path.exists() and graft_path.stat().st_size:
        raise GitError('Legacy grafts are unsupported')
    def resolve(ref):
        if not ref or ref.startswith('-'):
            raise GitError('revision must be a non-option commit name or ID')
        if len(os.fsencode(ref)) > 4096:
            raise GitError('revision exceeds 4096 bytes')
        raw = budget.run(['rev-parse', '--verify', '--end-of-options', ref + '^{commit}'], strict=True)
        oid = raw.decode('ascii').strip()
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid):
            raise GitError('revision must resolve to one full commit ID')
        return oid
    a, b = resolve(left), resolve(right)
    shallow = budget.run(['rev-parse', '--is-shallow-repository']).strip() == b'true'
    report = dict(schema_version=1, kind='patches', algorithm='git-patch-id--stable',
                  complete=False, scope='ancestry-exclusive nonmerge regular text changes',
                  notice=NOTICE, limits=dict(commits_per_side=max_count, inspection_bytes=max_bytes,
                                            seconds=seconds), warnings=[], matches=[],
                  left=dict(revision=left, oid=a), right=dict(revision=right, oid=b))
    history_error = None
    try:
        # Even identical tips must not hide missing shared ancestors.
        budget.run(['rev-list', '--count', a, b, '--'])
    except Incomplete as exc:
        history_error = str(exc)
    complete = not shallow and history_error is None
    if history_error:
        report['warnings'].append('history validation: ' + history_error)
    if shallow:
        report['warnings'].append('shallow history: membership and patch IDs withheld')
    for side, tip, other in [('left', a, b), ('right', b, a)]:
        entry = report[side]
        entry.update(enumeration_complete=False, sampled_count=0, unmatched=[], excluded=[], unclassified=[])
        try:
            rows = budget.run(['rev-list', '--topo-order', '--parents', f'--max-count={max_count + 1}',
                               tip, '^' + other, '--'], cap=8 * 1024 * 1024).splitlines()
            entry['enumeration_complete'] = len(rows) <= max_count and not shallow and history_error is None
            if not entry['enumeration_complete']:
                complete = False
                if not shallow and history_error is None:
                    report['warnings'].append(side + ': commit limit; additional commits omitted')
            entry['_rows'] = rows[:max_count]
            entry['sampled_count'] = len(entry['_rows'])
        except Incomplete as exc:
            complete = False
            report['warnings'].append(side + ': ' + str(exc))
            entry['_rows'] = []
    ids = {'left': {}, 'right': {}}
    for side in ('left', 'right'):
        entry = report[side]
        for row in entry.pop('_rows'):
            parts = row.decode('ascii').split()
            oid = parts[0]
            if shallow or history_error:
                entry['unclassified'].append(dict(oid=oid, reason='shallow history' if shallow else history_error))
                continue
            if len(parts) > 2:
                entry['excluded'].append(dict(oid=oid, reason='merge'))
                continue
            try:
                reason, patch_id, _, _ = inspect_patch(budget, oid, max_bytes)
                if reason:
                    entry['excluded'].append(dict(oid=oid, reason=reason))
                    continue
                ids[side].setdefault(patch_id, []).append(oid)
            except (Incomplete, subprocess.TimeoutExpired) as exc:
                complete = False
                entry['unclassified'].append(dict(oid=oid, reason=str(exc) if isinstance(exc, Incomplete) else 'runtime limit'))
    for patch_id in sorted(ids['left'].keys() & ids['right'].keys()):
        report['matches'].append(dict(patch_id=patch_id, left=sorted(ids['left'][patch_id]),
                                      right=sorted(ids['right'][patch_id])))
    for side, other in [('left', 'right'), ('right', 'left')]:
        for patch_id in sorted(ids[side].keys() - ids[other].keys()):
            for oid in sorted(ids[side][patch_id]):
                item = dict(oid=oid, patch_id=patch_id)
                if complete:
                    report[side]['unmatched'].append(item)
                else:
                    item['reason'] = 'incomplete search; no observed match'
                    report[side]['unclassified'].append(item)
    report['complete'] = complete
    report['inspection_bytes_read'] = budget.bytes
    return report


def format_patch_report(report, as_json=False, max_output=2 * 1024 * 1024):
    if not 1024 <= max_output <= 16 * 1024 * 1024:
        raise GitError('patch output limit must be 1024..16777216 bytes')
    if as_json:
        output = json.dumps(report, ensure_ascii=True, indent=2)
    else:
        lines = ['Patch comparison: ' + ('complete' if report['complete'] else 'INCOMPLETE'),
                 'Left:  ' + safe(report['left']['revision']) + ' ' + report['left']['oid'],
                 'Right: ' + safe(report['right']['revision']) + ' ' + report['right']['oid'],
                 report['notice'], f"Matching groups: {len(report['matches'])}"]
        for group in report['matches']:
            lines.extend(['  ' + group['patch_id'], '    left:  ' + ', '.join(group['left']),
                          '    right: ' + ', '.join(group['right'])])
        for side in ('left', 'right'):
            for category in ('unmatched', 'excluded', 'unclassified'):
                lines.append(f"{side} {category}: {len(report[side][category])}")
                lines.extend('  ' + item['oid'] + ' ' + item.get('reason', item.get('patch_id', ''))
                             for item in report[side][category])
        lines.extend('Warning: ' + safe(warning) for warning in report['warnings'])
        output = '\n'.join(lines)
    if len((output + '\n').encode('utf-8')) > max_output:
        raise GitError('output byte limit; no report emitted (increase --max-output-bytes)')
    return output
