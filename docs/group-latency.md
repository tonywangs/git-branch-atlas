# Measured split/squash blob reuse

This page preserves the blob-reuse experiment. The subsequent
[bounded batching experiment](group-batching.md) uses this implementation as its
baseline and reports the additional change separately.

On the reproducible complete 500-commit workload, bounded blob reuse reduced
median runtime from **54.76 to 35.28 seconds** while preserving identical reports.
The improvement was **1.55×; the 2× target was missed**.

The opt-in `series --groups` search now reuses successfully read Git blob
contents within one comparison. The cache is a least-recently-used map keyed by
full object ID. It retains at most **4 MiB of payload, 1,024 entries, and 256 KiB
per entry**. Keys and Python container overhead are additional but entry-bounded.
Larger objects take the original streaming path. Nothing is written to disk by
the cache, shared between repositories, or retained between comparisons. Workloads
with little reuse, oversized objects, or cache eviction can receive less benefit
and still pay lookup/bookkeeping overhead. The recorded synthetic fixtures fit
the cache; they do not establish gains on large-blob or cold-cache repositories.

## Why this optimization

The unchanged catalog implementation is preserved as source in
[`experiments/group_latency/baseline`](../experiments/group_latency/baseline).
Its source manifest corresponds to catalog tree
`2ca8e13f793267ea5fc9b20ad3704d3598af93ec`.
The [initial profile](../results/group-latency-profile.json) was collected before
selecting the optimization. For its complete 500-commit case, 10,934 Git processes
consumed 131.76 seconds including interpreter startup and measurement overhead.
Blob reads accounted for 52.06 seconds of instrumented work; raw diffs, patch
diffs and stable patch IDs remained other substantial costs. Scoring took less
than half a second. The 1,488 group windows reread 4,460 blobs after the singleton
phase had already inspected their contents. This motivated blob reuse rather
than changing candidate enumeration or scoring. Initial profiling overlapped
baseline regression validation and browser setup; its absolute wall times should
not be compared with later optimized runs. Speedups use only the later paired
measurements.

This is conventional content-addressed reuse, not a new correspondence algorithm.
The related primary interfaces are Git's
[`cat-file`](https://git-scm.com/docs/git-cat-file),
[`diff-tree`](https://git-scm.com/docs/git-diff-tree), and
[`patch-id`](https://git-scm.com/docs/git-patch-id).
Git provides batch object retrieval, but Atlas continues to use the existing
single-object command for misses. A per-comparison cache avoids introducing a
persistent subprocess protocol, prefetched objects, or a different failure order.
Endpoint patches and stable IDs continue to come from Git.

## Preserved behavior and bounds

There are no new CLI options or JSON fields. Default series and patch comparison
commands do not enable the cache. Group window order, candidate order, scores,
ambiguity, unsupported-member exclusions and all deterministic search limits are
unchanged. Cached binary blobs still trigger the same NUL exclusion.

`inspection_bytes_read` retains the baseline **logical inspection-byte accounting**:
every reused blob is charged its full size again. The byte limit therefore does
not authorize extra windows after this optimization. If an object would cross a
byte limit or a subprocess output cap, Atlas deliberately uses the original
streaming read, preserving failure precedence and partial-byte accounting. Exact
budget boundaries and repeated empty blobs retain the original behavior.

Deadlines are checked before cache hits. Faster code can naturally finish more
work before a wall-clock deadline; identical outputs are asserted with generous
deadlines, while deterministic byte/enumeration/comparison/candidate caps are
compared exactly. OS pipe chunking can affect the reported partial byte count on
an output-cap failure even between two uncached runs; the fallback retains that
existing behavior. No elapsed-time equivalence is claimed.

Failed reads and reads with stderr are not cached. Temporary stdin files and
subprocess stdout/stderr pipes continue to be closed by context managers; killed
children are waited for. The existing subprocess stdout cap remains at most the
requested inspection limit (256 MiB maximum through the public API), stderr is
capped at 64 KiB, and reads use at most 64 KiB chunks. Temporary bytearray-to-bytes
conversion can briefly duplicate stdout; the cache adds at most 4 MiB of retained
payload. This is not a new whole-process RSS cap. All existing feature, evidence,
candidate and rendered-output limits still apply.

As with any content-addressed cache, reuse assumes the repository's object store
remains valid during a comparison. Concurrent deletion or corruption of objects
that were already successfully read may not be noticed on a cache hit. There is
no claim to provide a transactionally consistent snapshot during external
repository mutation. Refs, configuration, attributes and failures are never
cached. Replacement objects and lazy network fetching remain disabled.

## Reproduction and validation

From the project checkout:

```sh
python3 scripts/measure_group_latency.py --profile-only --output /tmp/group-profile.json
python3 scripts/verify_group_reuse.py --seeds 200 --output /tmp/group-parity.json
python3 -m unittest discover -s tests -v
python3 scripts/measure_group_latency.py --repeat 6 --output /tmp/group-paired.json
NODE_PATH=/tmp/atlas-browser/node_modules PLAYWRIGHT_BROWSERS_PATH=/tmp/atlas-browser-binaries python3 scripts/verify_html.py
python3 scripts/verify_install.py
python3 scripts/check_publication.py
git diff --check
```

Browser prerequisites and the pinned Playwright installation are documented in
[HTML review](html-review.md). Installation verification supports an offline
`--wheelhouse` and otherwise downloads only pinned, hash-checked build wheels.
The application itself has no new dependencies and uses no new Git flags. The
existing documented Git compatibility requirement still applies; measured and
tested versions are recorded with results, including SHA-256 repository regression
coverage. Other Git versions are not newly certified by these measurements.

The new differential harness runs the same repository through the frozen and
current implementations. It compares entire reports, including evidence,
ambiguity and byte counts, on 200 seeded histories plus a resource-limited variant
of each. Cases cover both split/squash orientations, duplicate/revert/overlapping
candidates, edited patches, merges, empty commits, binary/symlink/mode exclusions,
and whitespace, quotes, newlines, Unicode and invalid-UTF-8 filenames. Independent
`git diff` endpoint patches are checked against the reported stable IDs. Fixture
contents, index, refs and configuration are hashed with modification times before
and after both implementations. The preexisting 200-history exhaustive window and
independent score oracle remains in the regression suite.

Focused cache regressions cover entry/payload/admission limits, logical byte
charging, exact boundaries, eviction, missing objects, truncated failed stdout,
stdout/stderr overflow, oversized patches, deadline checks and child/pipe cleanup.
A malicious Git executable returning truncated content with a successful exit is
not an authenticated object source; that is outside the existing Git trust model.

The benchmark includes a complete 12-commit case (6/6), a complete 500-commit case
(20/480), and a balanced 500-commit case (250/250) that exhausts 1,000 comparisons.
All 1,488 windows are inspected in both large cases. A complete balanced 250/250
search requires 372,000 comparisons, exceeding the unchanged public 100,000 cap;
it is not represented as a complete search. The complete 20/480 case evaluates
all 54,600 group comparisons. Fixture definitions and exact commands are in the
[experiment description](../experiments/group_latency/README.md).

Six pairs alternate execution order equally, with no discarded warmups. Each pair
asserts identical serialized output SHA-256 hashes and the same inspected scope.
Measurements include all raw timings, stage times, actual Git subprocess counts,
peak RSS, source hashes and versions. Adjacent `.rows.jsonl` output preserves
completed measurements even if a later assertion fails. Run the benchmark without
other local tests for a cleaner comparison. The host is not machine-isolated,
workloads are synthetic and warm-cache, and GNU time reports the maximum RSS of
one process rather than the sum of simultaneously resident processes.

## Recorded correctness results

All **53 regression tests passed** on Python 3.12.3 / Git 2.43.0 on Linux;
[raw test output](../results/tests.log) includes the existing 200-history group
oracle and the existing 100-history patch and 100-history series oracles.

The [differential results](../results/group-reuse-parity.json) contain 200 seeds,
400 identical complete/limited report comparisons, and 965 independent endpoint
patch-ID checks. All seven filename categories and all six additional history
variants were exercised. All before/after repository snapshots matched.
The [offline Chromium results](../results/group-reuse-browser.json) record 14
fixtures on Chromium 145.0.7632.6 with zero external requests, and
[isolated installation results](../results/group-reuse-installation.json) confirm
actual split/squash console commands and embedded HTML/JSON agreement.

## Paired performance results

Six pairs per workload, alternating order equally, produced **36 measurements**.
Every pair produced identical serialized JSON; all large runs inspected all 1,488
windows. The complete case searched all 54,600 group comparisons; the balanced
case retained the same 1,000-of-372,000 comparison limit and incomplete status.

| Workload | Baseline median s | Reuse median s | Ratio of medians | Median paired speedup | Git processes baseline → reuse | Maximum RSS KiB baseline → reuse |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 12 commits, complete | 1.092 | 0.819 | 1.33× | 1.36× | 198 → 127 | 20,224 → 20,224 |
| 500 commits (20/480), complete | 54.764 | 35.279 | 1.55× | 1.57× | 10,934 → 6,464 | 29,788 → 30,304 |
| 500 commits (250/250), comparison limit | 52.600 | 33.342 | 1.58× | 1.57× | 10,934 → 6,349 | 30,100 → 30,516 |

**The 2× median speedup target was missed.** The complete 500-commit search
improved from 54.764 to 35.279 seconds (1.55× ratio of medians; 1.57× median
paired speedup). Its six paired ratios ranged from 1.48× to 1.69×. All measured
pairs improved runtime, but no general speedup guarantee follows from these
cache-fitting synthetic fixtures. The cache is retained because it improves this
measured workload without changing reports or deterministic resource accounting.

Peak measured RSS for the complete case increased by 516 KiB (29,788 to 30,304
KiB); these maxima come from different runs and are not a precise attribution of
allocator overhead. Blob subprocesses fell from 4,960 to 490 in the complete
case. Remaining raw-diff, patch-diff and patch-ID processes explain why blob reuse
alone leaves substantial latency. No batching of those operations is claimed.

The [full paired results](../results/group-latency-paired.json) and
[incremental raw rows](../results/group-latency-paired.rows.jsonl) preserve every
measurement, including slower repetitions. Summary `median_speedup` is the ratio
of baseline and optimized medians; the table also reports the median of the six
`paired_speedups`. Limits are explicitly raised using the existing API options
recorded in the results (including a 600-second deadline); this is not a claim
that default settings complete a 500-commit search.
