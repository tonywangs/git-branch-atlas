# Large-patch and packed-object protocol

The baseline directory freezes catalog tree
`8670959e6b403d5050b7226990deab91fda12fc4` (remote commit
`526d44bb5f012244da4a007891bf501d303a476f`). SHA-256 hashes are checked on every
run. The unbatched comparator remains the historical `group_batching/baseline`;
all previous experiments are preserved. The local starting tree matches the
catalog tree exactly; no remote identity or visibility operation is required.

Before paired measurements, use one exploratory batched pass to identify
speculation costs. Eight seeded workloads cover six commits per side with small
patches, 32 changed files per commit, mixed binary/symlink/text changes,
reverts/reapplications with identical left/right endpoints, zero group-comparison allowance, and 900 x 257-byte
payloads causing aggregate batch overflow. Two additional boundary shapes
replace the six-commit shape with one commit per side: a framed singleton patch
exactly one byte below/above the 1 MiB cap. They are part of the eight workloads,
not extra measurements. Calibration asserts actual Git output lengths.

Each fixture is measured first with loose objects and then after `git repack -ad`
and `git prune-packed`, then `git prune --expire=now` solely inside the temporary
fixture to remove unreachable calibration objects. Assert pack counts and zero remaining loose objects.
Object IDs, seed, packing commands, counts and comparison options are recorded.
Repository file hashes and mtimes (including index, refs, configuration, objects,
and an untracked file) must remain unchanged throughout each measurement phase.

Compare unbatched, frozen batched, and an experimental four-request candidate.
The candidate changes only REQUESTS from 16 to 4 in the worker's imported frozen
module. It keeps the 1 MiB bound and failure-disable behavior. Hypothesis: smaller
batches avoid some overflow at the cost of more subprocesses on small patches.
Do not adopt unless all outputs match, the batch-overflow workloads improve by at least
10% in median paired runtime, and no other workload regresses by over 5%.
This is a workload-specific decision rule, not a statistical significance test.

Run all six permutations of the three implementations: six balanced observations
per implementation per workload/storage, 288 fresh-process measurements total.
No discarded warmups, retries, outlier removal, or concurrent local test jobs.
Require byte-identical full JSON within each block, including all searched scope,
logical byte accounting, ordering, evidence and completeness. Preserve every raw
row, wall runtime including interpreter start/serialization, inner stage timings,
Git subprocess counts, fallback-disable transitions, empty batch returns,
standalone patch calls, output hash and GNU time peak RSS. RSS is the maximum
individual process including waited-for children, not aggregate simultaneous RSS.
Instrumented stage times overlap and must not be added together.

Fixture construction, calibration, packing and file snapshots warm filesystem
caches. Every worker is fresh; no cold-cache claim is made. No cache eviction or
host-wide cache setting changes. Shared-host noise, small synthetic histories,
long-line boundary patches, and Linux/Git-version coverage limit applicability.
The deadline is 600 seconds; short deadlines and concurrent repository mutation
are outside the equivalence claim.

Reproduction (Python, Git and GNU time):

```sh
python3 scripts/reproduce_batching_limits.py --output-dir /tmp/batching-limits-reproduction
```

The script generates fixtures in temporary storage and deletes them on exit;
seeds and object IDs permit regeneration. The candidate is an experiment only.
For full milestone checks and results see `docs/batching-limits.md`.

The full command requires the pinned offline Chromium prerequisites documented in
`docs/html-review.md`. It installs no browser automatically. Timing-only reruns:
`python3 scripts/measure_batching_limits.py --output /tmp/batching-limits.json`.
