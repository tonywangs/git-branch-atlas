"""Bounded speculative patch transport; ordered inspection stays in patches.py."""
from __future__ import annotations

from itertools import islice
import re
import time

from .patches import DIFF, Incomplete

REQUESTS = 16
PAYLOAD_BYTES = 1024 * 1024
OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


def prefetch(budget, requests):
    """Return (tip, commit-base-or-None) -> (patch, stable ID), or no hints.

    Callers supply only full commit IDs, never tree IDs as fake parents. A root
    aggregate (empty-tree base) takes the ordinary path. One bounded batch lives
    at a time. Errors discard the whole batch and disable speculation for this
    comparison; the original ordered commands then determine report failures.
    """
    # A singleton cannot amortize transport. Leave validation, accounting and
    # failure handling to the existing ordered inspection path.
    if len(requests) == 1:
        return {}
    if (not requests or len(requests) > REQUESTS
            or getattr(budget, '_batch_disabled', False)
            or budget.max_bytes - budget.bytes < PAYLOAD_BYTES
            or time.monotonic() >= budget.deadline):
        return {}
    if any(not OID.fullmatch(tip) or (base is not None and not OID.fullmatch(base))
           for tip, base in requests):
        return {}
    # Git's --stdin fake parents persist inside the child. Never mix a natural
    # parent request with a fake-parent request for the same tip in that child.
    if any(base is None and any(t == tip and b is not None for t, b in requests)
           for tip, base in requests):
        return {}
    markers = [f'atlas-batch-{i:04d}\n'.encode() for i in range(len(requests)+1)]
    data = bytearray()
    for marker, (tip, base) in zip(markers, requests):
        data.extend(marker)
        data.extend((tip + (' ' + base if base is not None else '') + '\n').encode())
    data.extend(markers[-1])
    try:
        output = budget.run(['diff-tree', '--stdin', '--root', '--no-commit-id', '-r', '-p', *DIFF, '--'],
                            data=data, cap=PAYLOAD_BYTES, strict=True)
        # Markers occupy whole lines that cannot be emitted by a canonical patch:
        # path headers are quoted and hunk payload lines have explicit prefixes.
        parts = re.split(rb'^atlas-batch-[0-9]{4}\n', output, flags=re.MULTILINE)
        found = re.findall(rb'^atlas-batch-[0-9]{4}\n', output, flags=re.MULTILINE)
        if found != markers or parts[0] or parts[-1]:
            raise Incomplete('invalid batch diff framing')
        diffs = parts[1:-1]
        if any(d and (not d.startswith(b'diff --git ') or not d.endswith(b'\n')) for d in diffs):
            raise Incomplete('invalid batch patch')
        tagged = bytearray()
        tags = {}
        for i, (request, diff) in enumerate(zip(requests, diffs)):
            if not diff:
                continue
            tag = f'{i+1:0{len(request[0])}x}'.encode()
            tags[tag] = (request, diff)
            tagged.extend(b'commit ' + tag + b'\n' + diff)
        if not tags:
            return {}
        # The diff response includes framing; patch-ID input adds at most 16*72
        # bytes. Neither input can grow with the total number of windows.
        ids = budget.run(['patch-id', '--stable'], data=tagged, cap=REQUESTS*130, strict=True)
        result, seen = {}, set()
        for line in ids.splitlines():
            fields = line.split()
            if len(fields) != 2:
                raise Incomplete('invalid batch patch ID')
            pid, tag = fields
            if (tag not in tags or tag in seen or len(pid) != len(tag)
                    or not re.fullmatch(b'[0-9a-f]+', pid)):
                raise Incomplete('invalid batch patch ID')
            seen.add(tag)
            request, diff = tags[tag]
            result[request] = (diff, pid.decode('ascii'))
        if seen != tags.keys():
            raise Incomplete('missing batch patch ID')
        return result
    except Incomplete:
        budget._batch_disabled = True
        return {}


def batches(budget, items, endpoints):
    """Yield original items unchanged with at most one batch of patch hints."""
    iterator = iter(items)
    try:
        while chunk := list(islice(iterator, REQUESTS)):
            budget._patch_batch = {}
            requests = [request for item in chunk if (request := endpoints(item)) is not None]
            budget._patch_batch = prefetch(budget, requests)
            yield from chunk
    finally:
        budget._patch_batch = {}
