# Bounded diff and patch-ID batching

The opt-in `series --groups` comparison batches canonical patch generation and
stable patch-ID calculation. It retains ordered raw metadata and blob inspection,
the existing blob cache, and all report schemas and search limits. Plain `series`
and `patches` commands keep their original execution paths.

## Baseline and choice

The immediately preceding blob-reuse implementation is frozen in
[`experiments/group_batching/baseline`](../experiments/group_batching/baseline),
with a SHA-256 source manifest for catalog tree
`dcaab594594a69d23970d219e592e8d51966bf7a`. The earlier pre-reuse experiment and
its raw measurements remain in `experiments/group_latency` and `results`.

The [profile](../results/group-batching-profile.json) was collected before
integrating batching. On the complete 20/480-commit workload, the baseline used
6,464 Git subprocesses and 77.80 seconds. Canonical patch generation consumed
25.17 instrumented seconds and patch IDs 20.47 seconds, together 3,976 processes.
Raw diffs consumed 23.09 seconds; blob calls, mostly cache hits, 4.57 seconds.
The small and comparison-limited workloads used 127 and 6,349 processes. Profiling
shared the host with regression checks; these absolute times are diagnostic,
not the performance comparison. The balanced paired experiment runs separately.

Batching these two stages avoids changing raw record parsing, binary/mode
exclusions, blob-cache admission, or the logical order of charged inspection.
This is an application of existing Git plumbing, not a new matching algorithm.
The related primary interfaces are
[`git diff-tree --stdin`](https://git-scm.com/docs/git-diff-tree#Documentation/git-diff-tree.txt---stdin)
and [`git patch-id --stable`](https://git-scm.com/docs/git-patch-id).
Git already supports multiple patch IDs from a stream of commit-prefixed patches.
The [Git 2.43 implementation](https://github.com/git/git/blob/v2.43.0/builtin/diff-tree.c)
also explains two details used here: non-object input lines are echoed, and
substituted commit parents persist for the life of that process.

## Transport and bounds

Each batch holds at most **16 requests** and accepts at most **1 MiB total diff
stdout**, including framing. A request contains a full 40- or 64-character commit
ID, optionally followed by a full commit ID to use as its parent. Pair order is
`tip base`; this differs from the ordinary two-endpoint command's `base tip`.
Root singletons use `--root`. Aggregate windows starting at a root have an
empty-tree base and retain the ordinary command, because a tree cannot be a fake
commit parent. Merges are excluded from singleton requests by the caller.

Numbered, non-object marker lines delimit requests. Canonical patch headers quote
special filenames and hunk lines have explicit prefixes, so the markers cannot
occur as whole lines inside a Git-produced patch. The parser requires the exact
marker sequence, including the final marker, and no leading or trailing output.
Empty patches have no ID and are left to ordinary empty-raw-diff exclusion.
Requests may repeat tips or complete endpoint pairs. Mixing natural parents and
substituted parents for the same tip in one child is rejected, avoiding Git's
persistent substituted-parent state.

Nonempty patches receive unique synthetic commit tags, with the repository's OID
width, before one `patch-id --stable` call. Returned tags must be present exactly
once and IDs must be full hexadecimal values of the same width. No synthetic tag
is exposed in JSON. Only a fully validated batch yields hints.

The diff request input is at most 2,369 bytes (16 pairs of SHA-256 commit IDs and
17 markers). Patch-ID input is at most 1 MiB plus 1,152 bytes of tags; stdout is
capped at 2,080 bytes. Each subprocess has the existing 64 KiB stderr cap and
64 KiB maximum read chunk. Both subprocesses finish before inspection resumes;
there are no persistent children, threads, or unbounded queues. Temporary input
files avoid pipe deadlocks. Exceptions, including cancellation, kill and wait for
the child and close its pipes, through the existing `Budget.run` lifecycle.

Retained patch payload is at most 1 MiB, with at most 16 mapping entries. Hints
are cleared before preparing the next batch and when the iterator closes.
Parser slices, the tagged input, bytearray-to-bytes conversion and a caller's
last patch can temporarily duplicate bounded payload. These are buffer bounds,
not a whole-process or Git RSS cap. The existing blob cache adds up to 4 MiB;
feature sets, result objects and Git's own allocations retain their prior bounds.

## Ordered semantics and fallback

Prefetching is speculative and charges no logical inspection bytes. At the
original inspection position Atlas still reads raw metadata, checks file modes,
and reads/checks all changed blobs in the original order. Only then may it use a
prefetched patch and ID. It charges that patch's full byte length, including for
repeated endpoints, exactly as the old patch command did. This preserves the
meaning of `inspection_bytes_read`; it is not a physical I/O counter.

If a cached patch would cross either the inspection limit or per-command output
cap, Atlas runs the original streaming commands instead. This preserves partial
byte accounting and failure precedence. New batches are skipped when fewer than
1 MiB of logical inspection budget remain. An overflow, malformed/truncated
response, diagnostic on stderr, or subprocess failure discards the entire batch
and disables batching for the rest of that comparison. The original ordered
commands then determine any reported exclusion or failure. A large-patch workload
can pay for one unsuccessful prefetch before falling back.
Singleton prefetch can also generate patches that subsequent ordered mode or
binary checks exclude; those hints are discarded without affecting eligibility.

Deadlines apply to speculation as well as inspection and are checked before
accepting a hint. A cancelled operation propagates cancellation. Faster successful
execution can inspect more work before a wall-clock deadline; unsuccessful
speculation can consume time that the baseline would have spent on inspection.
No equality at identical short wall-clock deadlines is promised. Deterministic
byte, enumeration, comparison, candidate, feature and export limits remain the
same, including near-limit streaming fallback. As before, OS pipe chunking can
affect partial byte counts for output-cap failures.

Like the existing blob cache, this assumes a stable, valid local repository during
a comparison. Refs are resolved once. Concurrent changes to objects, attributes
or configuration can make a prefetched patch stale or make it differ from a later
standalone command. There is no transactional snapshot guarantee. Trusted local
Git remains responsible for producing valid patches: framing detects transport
truncation, not arbitrary fabricated output from a malicious executable.

## Reproduction

Python 3.10+ and Git 2.43+ remain the documented requirements. These results test
Python 3.12.3 and Git 2.43.0 on Linux, including SHA-1 and SHA-256 repositories;
other platforms/versions are not newly certified. No runtime dependency was added.
The offline Chromium prerequisite setup is in [HTML review](html-review.md).

```sh
python3 scripts/measure_group_latency.py --baseline experiments/group_batching/baseline --profile-only --output /tmp/batching-profile.json
python3 -m unittest discover -s tests -v
python3 scripts/verify_group_reuse.py --baseline experiments/group_batching/baseline --seeds 200 --output /tmp/batching-parity.json
NODE_PATH=/tmp/atlas-browser/node_modules PLAYWRIGHT_BROWSERS_PATH=/tmp/atlas-browser-binaries python3 scripts/verify_html.py --output /tmp/batching-browser.json
python3 scripts/verify_install.py
python3 scripts/measure_group_latency.py --baseline experiments/group_batching/baseline --repeat 6 --output /tmp/batching-paired.json
python3 scripts/check_publication.py
git diff --check
```

Run paired measurements without other local validation jobs. Each workload runs
six pairs with alternating order and no discarded warmups. Raw rows include
runtime, maximum individual-process RSS from GNU time, Git subprocess counts,
stage times, output hashes, commands and searched scope; the summary includes
versions, source hashes and deterministic fixture IDs. The workloads are a
complete 6/6 search, a complete 20/480 search (54,600 group comparisons), and a
250/250 search retaining its exhausted 1,000-of-372,000 comparison limit. Both
large workloads inspect all 1,488 windows. Synthetic warm-cache measurements on a
shared host do not establish a general speedup or memory guarantee.

## Correctness results

All **60 regression tests passed** ([test output](../results/tests.log)), including
the existing independent 200-history group oracle and 100-history patch and series
oracles. Seven focused batching tests exercise root and merge commits, empty
patches, repeated endpoints and parent substitution, unusual filenames, SHA-256
repositories, request and payload bounds, exact/near inspection limits, oversized
patches, truncated framing and IDs, wrong/duplicate/missing IDs, subprocess errors,
stderr overflow, deadlines, cancellation, descriptor/child cleanup and iterator
hint cleanup. Focused tests also confirm successful hints avoid individual
patch-ID subprocesses while charging the original logical bytes.

The [seeded differential evidence](../results/group-batching-parity.json) contains
**200 histories, 400 identical full/limited report comparisons, and 965 independent
endpoint patch-ID checks**. Cases cover splits, squashes, duplicates, reverts,
overlapping candidates, edits, empty commits, merges, binary/symlink/mode
exclusions, seven unusual filename categories and seven deterministic limit
variants. Entire repository file snapshots, including index, refs, configuration
and untracked files, match before and after both comparisons.

[Offline Chromium review](../results/group-batching-browser.json) passed all
14 fixtures on Chromium 145.0.7632.6 with zero external requests. Visible statuses,
ordered members, endpoints, candidates, scores, ambiguity and evidence agree with
CLI JSON. [Isolated installation](../results/group-batching-installation.json)
passed the actual installed console-entry-point split/squash use case in both
orientations, including terminal output and HTML/JSON parity. Browser validation
is limited to Chromium on Linux, not a cross-platform accessibility audit.

## Paired performance results

Six balanced pairs per workload produced **36 measurements**, with no discarded
warmups or repetitions. Every pair has identical serialized JSON. All large runs
inspect all 1,488 windows; the complete case searches all 54,600 comparisons, and
the balanced case remains incomplete at 1,000 of 372,000 comparisons.

| Workload | Baseline median s | Batched median s | Ratio of medians | Median paired speedup | Git processes baseline → batched | Maximum RSS KiB baseline → batched |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 12 commits, complete | 0.980 | 0.563 | 1.74× | 1.71× | 127 → 63 | 20,224 → 20,352 |
| 500 commits (20/480), complete | 38.816 | 19.568 | 1.98× | 1.97× | 6,464 → 2,738 | 30,264 → 30,496 |
| 500 commits (250/250), comparison limit | 33.459 | 16.575 | 2.02× | 2.07× | 6,349 → 2,623 | 30,676 → 30,748 |

**The 1.5× target was met:** the complete workload improved from 38.82
to 19.57 seconds (1.98× ratio of medians). Its six paired speedups ranged
from 1.72× to 2.22×. None of the 18 measured pairs regressed in runtime.
This does not imply that large-patch, unsupported-change, cold-cache or low-budget
workloads improve; speculative fallback and short-deadline tradeoffs remain.

Peak RSS maxima are separate observations and do not isolate allocator or cache
overhead. GNU time does not sum simultaneous process memory. The shared host and
synthetic warm-cache workloads limit generalization.

[Full paired results](../results/group-batching-paired.json) preserve versions,
source hashes, fixture IDs, commands and every stage measurement.
[Raw incremental rows](../results/group-batching-paired.rows.jsonl) preserve the
completed observations independently. No repository writes, CLI/schema changes
or reduction in searched scope were needed for this improvement.
