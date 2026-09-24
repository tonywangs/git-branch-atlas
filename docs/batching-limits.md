# Diff batching at transport limits

This experiment evaluates the existing `series --groups` implementation on
large patches and packed Git objects. The original implementation is frozen in
[`experiments/batching_limits/baseline`](../experiments/batching_limits/baseline).
The unbatched comparator and all earlier measurements remain intact.
This is a bounded engineering experiment, with no novelty claim.

## Interfaces and prior work

Git already provides the needed plumbing:
[`diff-tree --stdin`](https://git-scm.com/docs/git-diff-tree) accepts commit streams
and substituted parents; [`patch-id --stable`](https://git-scm.com/docs/git-patch-id)
provides stable patch identifiers. [`pack-objects`](https://git-scm.com/docs/git-pack-objects)
documents Git's packed object representation. Atlas's existing batch framing,
ordered inspection, resource accounting and failure-disable behavior are described
in [group batching](group-batching.md). The experiment changes no public interface.

## Reproduce

With Python 3.10+, Git, GNU `/usr/bin/time`, Node and the pinned Playwright/Chromium
prerequisites from [HTML review](html-review.md):

```sh
python3 scripts/reproduce_batching_limits.py --output-dir /tmp/batching-limits-reproduction
```

This runs regressions, the candidate failure contract, 200 seeded stress histories,
the isolated installed CLI, offline Chromium and all paired measurements in
sequence. The installation verifier downloads pinned, hash-checked build wheels;
`verify_install.py --wheelhouse PATH` supports an already available offline wheel
cache. Browser tests themselves block non-file requests. The reproduction driver
does not install a browser or buy resources.

For timing alone:

```sh
python3 scripts/measure_batching_limits.py --output /tmp/batching-limits.json
```

The [protocol](../experiments/batching_limits/README.md) specifies workloads,
seeds, cache preparation, comparison rules and the candidate adoption rule before
paired timing. Temporary fixtures include refs, an index and an untracked file.
File hashes and mtimes must match before/after each storage phase. Only fixture
preparation packs/prunes objects; Atlas itself performs no writes.

## Workloads and measurement scope

Eight workloads are each measured with loose and packed objects. Small patches,
32 added files per commit, 231,300-byte text additions, mixed text/binary/symlink
changes, shared repeated endpoints with revert/reapplication histories, and a
zero group-comparison budget use six commits per side. The two boundary workloads
use one commit per side and calibrate the actual framed singleton diff to
1,048,575 and 1,048,577 bytes. Long single-line patches make the exact boundary
reproducible; they are deliberately synthetic.

Six blocks per workload/storage use all permutations of unbatched, frozen batched
and four-request candidate implementations. Each implementation occupies each
position twice, and each pair runs in each relative order three times. No timed observation is
discarded. Full serialized reports must match byte for byte within every block;
that includes searched scope, ordering, exclusions, candidates, evidence and
logical inspection bytes. Comparison deadlines are 600 seconds.

Each observation starts fresh Python and Git processes. Creation, calibration,
packing and repository snapshots warm filesystem caches. These are **not cold
filesystem-cache measurements**. No host-wide cache settings or cache eviction
are used. Timings include Python startup, instrumentation and serialization.
GNU time reports maximum individual-process RSS including waited-for children,
not simultaneous summed process-tree memory. Stage timings overlap. Raw rows
record exact commands (temporary paths require fixture regeneration), versions,
source hashes, seeds, output hashes, Git subprocess counts, standalone patch calls,
batch requests, duplicate requests, empty returns and disable transitions. Empty
returns include skipped/disabled batches, so they are not all subprocess failures.

## Exploratory profile and candidate

The [profile](../results/batching-limits-profile.json) covers all 16 combinations.
It ran concurrently with correctness checks; its times are diagnostic only.
The exact profiling harness is preserved as
[`profile_harness.py`](../experiments/batching_limits/profile_harness.py), matching
the recorded SHA-256. It is an archive for source auditing; the current measurement
script supports `--profile-only` to regenerate these workloads and stage metrics.
The just-below-cap workload returns hints. The above-cap workload disables batching
once, then executes two standalone patches. Multi-patch overflow also disables
once, then uses 36 standalone patch calls. Repeated endpoints produce four duplicate
requests within batches. The profile identifies an avoidable large singleton
batch before fallback; it does not establish a speedup.

The experimental candidate sets only `batching.REQUESTS = 4` in a fresh worker
using the frozen package. Payload cap, ordered inspection, fallback and cleanup
remain intact. It can fit four medium patches where six exceed 1 MiB. More
subprocesses and later group overflow can offset that saving. The predeclared
rule requires at least 10% median paired runtime reduction on both batch-overflow
storage variants and at most 5% regression on every other workload against the
frozen batched implementation. This is an engineering selection rule, not a
statistical significance claim.

## Failed attempts and validation limits

The first profile failed its zero-loose-object assertion: unreachable calibration
commits survived repacking. Its five completed rows are preserved in
[`batching-limits-profile-attempt.rows.jsonl`](../results/batching-limits-profile-attempt.rows.jsonl).
Fixture preparation now explicitly prunes unreachable calibration objects in the
temporary repository before the packed phase. No failed row is part of the paired
results. An initial installation-verifier invocation used an unsupported `--output`
option (exit 2); redirecting its normal JSON stdout succeeded.

Correctness claims assume stable valid repositories, nonbinding deadlines and
this tested Linux/Python/Git environment. Packed stress cases alternate by seed,
so storage and some unsupported-change cases are correlated; the paired timing
suite measures every workload in both storage modes. Small, compressible synthetic
histories and long-line cap probes do not predict real-world large-monorepo
performance. Neither short-deadline parity, transactional protection against
concurrent repository mutation, cold-cache speedups nor an aggregate memory bound
is established.

## Correctness and application checks

[Stress evidence](../results/batching-limits-parity.json) records 200 seeded
histories, 400 exact full/limited report comparisons against the preserved
unbatched implementation, and 965 independent `git diff` endpoint patch-ID checks.
There are 100 loose and 100 packed histories. Cases retain split/squash,
revert/reapplication, duplicate and overlapping windows, edited and empty commits,
merges, unusual filenames, unsupported changes and seven deterministic limits.
Seeded text additions vary up to 262,144 bytes plus the small original prefix.
Whole-repository hashes and mtimes, including index, refs, configuration and
untracked content, remain unchanged. These payloads extend the earlier small-patch
oracle without replacing its historical evidence.

All 60 existing regressions passed. Five existing failure-contract tests also
passed with the candidate's four-request bound: oversized stdout, malformed and
truncated framing/IDs, subprocess failure, stderr overflow, timeout, cancellation,
child reaping/descriptor cleanup, logical byte accounting, SHA-256 repositories,
and insufficient-budget fallback. [Check evidence](../results/batching-limits-checks.json)
records their observed outcomes; historical `results/tests.log` is unchanged.

[Installed-CLI verification](../results/batching-limits-installation.json) passed
in an isolated environment, including real split/squash use in both orientations
and terminal/HTML/JSON parity. [Offline Chromium](../results/batching-limits-browser.json)
passed all 14 fixtures with zero external requests, including keyboard navigation,
mobile overflow, evidence bounds, omissions and hostile content. Validation used
Python 3.12.3, Git 2.43.0 and Chromium 145.0.7632.6 on Linux.

## Paired results and decision

**Retain the production 16-request implementation.** The four-request candidate
failed the declared adoption rule. It reduced Git subprocesses on batch overflow
from 121 to 105 but did not improve runtime: median paired candidate/batched
ratios were 1.027 (loose) and 1.013 (packed). It also slowed small, repeated-endpoint
and exhausted-limit workloads. Fewer subprocesses alone did not predict benefit.
This reproducible negative result completes the experiment; the candidate is
retained only in the experimental worker and failure-contract verifier.

All **288 measurements** passed byte-for-byte report equality. The production
source still matches the frozen manifest exactly. The following are median wall
seconds and median **paired runtime ratios**; ratios above 1 mean slower. Ratios
are calculated per block before taking the median, so they need not equal ratios
of the displayed medians. Every observation, source hash, version, command,
fallback count and searched scope is in the [full results](../results/batching-limits.json)
and [raw rows](../results/batching-limits.rows.jsonl).

| Workload/storage | Unbatched s | Batched s | Four-request s | Batched / unbatched | Four / batched | Git processes U/B/4 | Max RSS KiB U/B/4 |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| small/loose | 0.926 | 0.549 | 0.591 | 0.590 | 1.107 | 125/61/73 | 20352/21632/21504 |
| small/packed | 0.844 | 0.509 | 0.605 | 0.573 | 1.217 | 125/61/73 | 20352/21504/21504 |
| many-files/loose | 2.283 | 1.961 | 1.951 | 0.862 | 0.987 | 311/247/259 | 21120/23276/22336 |
| many-files/packed | 1.879 | 1.533 | 1.657 | 0.835 | 1.081 | 311/247/259 | 21248/23376/22328 |
| below-cap/loose | 0.281 | 0.411 | 0.415 | 1.430 | 1.030 | 18/18/18 | 26120/28436/28436 |
| below-cap/packed | 0.291 | 0.410 | 0.417 | 1.389 | 1.024 | 18/18/18 | 26252/28564/28564 |
| above-cap/loose | 0.272 | 0.327 | 0.327 | 1.197 | 0.972 | 18/19/19 | 26256/27416/27400 |
| above-cap/packed | 0.290 | 0.327 | 0.329 | 1.138 | 1.015 | 18/19/19 | 26252/27412/27540 |
| batch-overflow/loose | 1.445 | 1.395 | 1.433 | 0.976 | 1.027 | 120/121/105 | 25996/26756/26800 |
| batch-overflow/packed | 1.367 | 1.390 | 1.410 | 1.009 | 1.013 | 120/121/105 | 25396/26516/26908 |
| mixed/loose | 0.277 | 0.295 | 0.311 | 1.062 | 1.051 | 32/28/32 | 20096/21504/21504 |
| mixed/packed | 0.361 | 0.344 | 0.365 | 1.006 | 0.987 | 32/28/32 | 19968/21632/21632 |
| repeated/loose | 0.631 | 0.480 | 0.561 | 0.731 | 1.195 | 96/56/68 | 20296/21504/21504 |
| repeated/packed | 0.633 | 0.445 | 0.554 | 0.709 | 1.248 | 96/56/68 | 20224/21504/21632 |
| limited/loose | 0.757 | 0.498 | 0.549 | 0.668 | 1.113 | 125/61/73 | 20096/21504/21504 |
| limited/packed | 0.746 | 0.452 | 0.538 | 0.619 | 1.137 | 125/61/73 | 20096/21632/21632 |

Existing batching is not universally faster. Immediately below the transport cap,
its paired runtime was 43.0% slower on loose objects and 38.9% slower on packed
objects; the singleton shape saves no subprocesses but incurs batch framing,
parsing and payload copies. Immediately above the cap it was 19.7% and 13.8%
slower, paying one failed speculative process. Mixed/loose changes were 6.2%
slower despite a lower subprocess count. Batch-overflow/packed and mixed/packed
showed approximately 1% paired slowdowns, which this small shared-host experiment
cannot distinguish from noise. These regressions qualify the earlier
small-patch speedup result rather than replacing it.

The candidate still disables speculation once on above-cap and batch-overflow
workloads. It postpones the multi-patch failure until aggregate processing, rather
than eliminating it. On other shapes it adds up to 12 Git processes. No candidate
result is promoted to production, and there is no new user setting. A possible
future experiment is bypassing singleton speculation; it is **not implemented or
measured here**, and no benefit is claimed.

`python3 scripts/check_batching_limits.py` independently audits raw/summary
consistency, all six orders, pair ratios, exact output hashes and searched scope,
object storage, calibration targets, source manifests and the adoption rule.
It passed for all 288 observations. Peak RSS values above are separate process
maxima, not an aggregate memory comparison or a memory-cap guarantee.
