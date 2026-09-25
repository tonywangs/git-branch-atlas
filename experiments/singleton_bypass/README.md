# Singleton bypass: frozen protocol

Protocol fixed before candidate measurements on 2026-09-25. Starting catalog
repository git-branch-atlas (ID 1370963014), catalog commit
cd749ab66477baa93060dcee0b4cdb9a58fcb031, tree
469afb18741790e6382af7f713e76ca025eafe2a. Local tree matched exactly;
this checkout has no remote. Identity is based on the supplied catalog and tree,
not an independent GitHub lookup. No novelty is claimed.

The existing implementation uses Git's documented diff-tree --stdin transport
and stable patch IDs: https://git-scm.com/docs/git-diff-tree and
https://git-scm.com/docs/git-patch-id (reviewed 2026-09-25). This experiment
extends the preserved batching-limit study; none of its evidence is replaced.

series.py batches each side's nonmerge commits in chunks of 16. A side with one
commit, a 16n+1 tail, or filtering merges can leave one request. group_compare.py
batches structural windows after max_groups truncation, then filters unsupported,
uninspected and empty-tree-base windows; even a full chunk can leave one eligible
endpoint. Repeated endpoints count as requests, not distinct endpoint pairs.

One candidate only: prefetch returns {} immediately when len(requests) == 1.
No deduplication, chunk-size, cap, budget, cache, or interface changes. Ordinary
ordered inspection owns patch validation, stable IDs, evidence and accounting.
Keep the candidate separate until all gates pass. Frozen current production is
experiments/batching_limits/baseline; unbatched is group_batching/baseline.

Workloads are the eight preserved batching-limit fixtures (seed 23000+index),
plus singleton-small, singleton-unsupported and singleton-tail (23008..23010).
The first two have one commit per side; unsupported includes binary and symlink
changes. Tail has 17 commits per side and max_groups=1, exercising 16+1 chunks
and a singleton window. Other options match the prior harness: max_count=32,
256 MiB logical byte allowance, 600-second deadline, 100000 comparisons,
1600 groups (except tail), 2000 candidates, and zero group comparisons for
limited. Boundary framed responses are exactly 1 MiB minus/plus one byte.
Small, many-files, mixed, repeated, batch-overflow and limited are controls.
Every case uses loose and packed objects: 22 workloads, 396 worker measurements.

Six repetitions use each permutation of three implementations exactly once per
workload. Fresh Python/Git processes; no concurrent local validation during
measurements, discarded warmups, retries, outlier removal or tuned second
candidate. Preserve all rows, commands, hashes, seeds, fixture object IDs,
inner/outer runtime, peak RSS, subprocess counts, request-size distribution,
singleton bypasses, hints, fallback transitions and standalone patch calls.
Fixture creation/calibration/snapshots and packing warm filesystem caches;
no eviction or cold-cache claim. GNU time peak RSS is maximum individual process
including waited-for children, not summed process-tree memory.

Adopt only if all full JSON outputs are byte-identical within every paired block,
200 seeded stress histories match both comparators under full and limited
searches with independent endpoint checks, repository snapshots remain unchanged,
and regression, candidate failure, installed CLI and offline Chromium checks pass.
For every workload candidate/production median paired wall runtime must be <=1.05;
for above-cap/loose AND above-cap/packed it must be <=0.90 instead. Each workload's
candidate maximum RSS / production maximum RSS must be <=1.10 and candidate Git
subprocess count must never exceed production in the paired block. Above-cap
singleton speculation must be bypassed with zero speculative fallback transitions.
The historical unbatched runtime comparison is descriptive, not an adoption gate.
These are practical gates, not significance tests. Report all failing gates and
all observed median runtime regressions, including those within tolerance.

One bounded suite, six paired repetitions only; max 20 permitted for explicit
future reproduction. Synthetic inputs, shared host noise, Linux/Git version,
short deadlines and concurrent repository mutation limit the conclusions.
Reproduction command and final decision are in docs/singleton-bypass.md.
