# Singleton speculative-batch bypass

This experiment evaluates returning no speculative hints for exactly one request.
The ordinary inspector still determines patch eligibility, IDs, evidence and
logical byte accounting. All multi-request behavior, including repeated pairs,
remains unchanged. The isolated candidate is in
[experiments/singleton_bypass/candidate](../experiments/singleton_bypass/candidate).

The [frozen protocol](../experiments/singleton_bypass/README.md) defines the
workloads and gates before measurement. Its SHA-256 is
`288cd355d7185f2fe7fd79242279c9d499ddeabcad960a1f7ce08d1aca97fd79`.
The candidate is the starting production package plus four lines in batching.py;
the [manifest](../experiments/singleton_bypass/baseline.json) pins every module.
Earlier experiments and source snapshots are preserved.

## Decision: retain production behavior

The candidate **fails the frozen performance rule**. Above-cap/packed has a
median paired runtime ratio of 0.9342 (6.6% faster), missing the required <=0.90.
Batch-overflow/packed has a ratio of 1.0592 (5.9% slower), exceeding <=1.05.
All 22 memory and subprocess gates pass. No production module was changed.
This is a reproducible negative adoption result, despite benefits on some inputs.

All 396 full JSON outputs matched within their paired blocks, including logical
inspection bytes, candidate ordering, evidence, completeness and searched scope.
The evidence audit additionally checks identical report hashes across repetitions,
all six run-order permutations, source/protocol hashes and the decision arithmetic.
Fixture file contents and mtimes, including index, refs and configuration, remained
unchanged during each loose/packed measurement phase.

The above-cap candidate bypassed two singleton requests per run, eliminated the
one speculative fallback transition, and reduced Git subprocesses from 19 to 18.
Unsupported singletons reduced subprocesses from 16 to 12. Other workloads had
unchanged counts. Below-cap cases improved with the same subprocess count: the
candidate avoids the framed transport/parser path, but this study does not isolate
how much of the timing difference comes from parsing. These are workload-specific
observations, not a universal speedup claim.

Batch-overflow has no singleton requests, so its observed regression is in a
control where the bypass never fires. Shared-host noise is a plausible explanation;
this experiment does not establish the slowdown's cause. The frozen gate still
fails; no post-hoc timing rerun or relaxed threshold is used for adoption.

### Complete timing results

Seconds are medians of six fresh-process wall times (including startup and JSON
serialization). Ratio is the median of six within-block candidate/production
ratios, not the ratio of the displayed medians. Lower is faster. Every ratio over
1 is an observed regression, including those within the 5% tolerance.

| Workload/storage | Unbatched s | Production s | Candidate s | Paired ratio | Paired range |
| --- | ---: | ---: | ---: | ---: | --- |
| small/loose | 0.7872 | 0.5425 | 0.5291 | 0.9823 | 0.883–1.039 |
| small/packed | 0.8470 | 0.5117 | 0.5396 | 0.9528 | 0.638–1.216 |
| many-files/loose | 2.1526 | 1.9357 | 1.8139 | 0.9614 | 0.862–1.034 |
| many-files/packed | 1.9075 | 1.5948 | 1.5873 | 0.9878 | 0.937–1.193 |
| below-cap/loose | 0.2867 | 0.4057 | 0.3121 | 0.7747 | 0.714–0.819 |
| below-cap/packed | 0.3053 | 0.4316 | 0.3580 | 0.8114 | 0.729–1.592 |
| above-cap/loose | 0.2775 | 0.3360 | 0.2976 | 0.8963 | 0.762–0.944 |
| above-cap/packed | 0.2858 | 0.3284 | 0.3068 | 0.9342 | 0.872–1.024 |
| batch-overflow/loose | 1.4003 | 1.4352 | 1.4059 | 1.0163 | 0.885–1.132 |
| batch-overflow/packed | 1.4425 | 1.4601 | 1.5464 | 1.0592 | 0.945–1.216 |
| mixed/loose | 0.3066 | 0.3112 | 0.3027 | 0.9770 | 0.818–1.121 |
| mixed/packed | 0.2911 | 0.2939 | 0.2919 | 1.0015 | 0.539–1.034 |
| repeated/loose | 0.6811 | 0.4400 | 0.4571 | 1.0119 | 0.860–1.508 |
| repeated/packed | 0.6392 | 0.4651 | 0.4778 | 1.0197 | 0.911–1.089 |
| limited/loose | 0.7353 | 0.4683 | 0.4640 | 0.9823 | 0.917–1.036 |
| limited/packed | 0.7536 | 0.4732 | 0.4606 | 0.9948 | 0.842–1.067 |
| singleton-small/loose | 0.2213 | 0.2508 | 0.2403 | 0.9651 | 0.829–1.106 |
| singleton-small/packed | 0.2196 | 0.2413 | 0.2357 | 0.9743 | 0.926–0.999 |
| singleton-unsupported/loose | 0.2010 | 0.2462 | 0.2053 | 0.8451 | 0.703–0.874 |
| singleton-unsupported/packed | 0.1898 | 0.2522 | 0.2114 | 0.8599 | 0.735–0.941 |
| singleton-tail/loose | 0.8188 | 0.5435 | 0.5924 | 1.0488 | 0.935–1.267 |
| singleton-tail/packed | 0.8269 | 0.5325 | 0.5684 | 1.0431 | 0.987–1.151 |

Across workloads, candidate/production maximum-RSS ratios range from 0.9213
to 1.0171, below the 1.10 cap. Exact per-run KiB, stage times, subprocess
and fallback counts, request sizes, commands and seeds are preserved in
[raw rows](../results/singleton-bypass.rows.jsonl) and the
[measurement summary](../results/singleton-bypass.json). The environment was
Python 3.12.3, git version 2.43.0, `Linux-6.8.0-124-generic-x86_64-with-glibc2.39`.

## Existing work and request paths

Git documents both [diff-tree stdin batching](https://git-scm.com/docs/git-diff-tree)
and [stable patch IDs](https://git-scm.com/docs/git-patch-id). Atlas already uses
these facilities. This is a transport-policy experiment, not a new comparison
algorithm. Stable IDs normalize whitespace and file-diff order; equality here
means identical Atlas reports, not proof of semantic program equivalence.

In `series.py`, each side's commits are chunked separately. A one-commit side,
a final 16n+1 tail, or filtering merge commits can produce a singleton. In
`group_compare.py`, window limits and exclusion of unsupported or uninspected
members can leave one eligible endpoint even in a larger chunk. The candidate
checks the actual request count, without deduplicating repeated endpoints.
An empty-tree aggregate already uses ordinary inspection.

Bypassing a singleton also avoids setting `_batch_disabled` if that speculative
request would have failed. Later multi-request chunks can therefore still
speculate in the candidate. The tests check that state difference deliberately;
this suite does not exhaust all possible singleton-failure/multi-request sequences.
Ordered report equality is verified only for the documented workloads and histories.

## Three-way correctness

All 200 seeded stress histories passed full-report equality against both frozen
production and the preserved unbatched baseline. Each history also passed one
limited-search comparison (200 limited reports), covering group, comparison,
candidate, byte and commit bounds. Full equality includes candidate ordering,
searched scope, logical byte accounting, exclusions, evidence and completeness.
Independent `git diff` endpoint patches produced the expected stable IDs in
965 checks. Repository file contents and mtimes, including index, refs and
configuration, stayed unchanged throughout. Loose and packed objects alternate.

The [per-history evidence](../results/singleton-bypass-parity.json) records seeds,
full/limited output hashes, bounds, endpoint hashes and all three source packages.
The [validation summary](../results/singleton-bypass-validation.json) records
executed commands, test counts, the failed gates and every runtime regression.

## Application and failure checks

Both production and the isolated candidate passed clean-venv installation,
console commands, real split/squash examples, and HTML/JSON parity. Each package
passed all 14 offline Chromium 145.0.7632.6 fixtures: statuses, ordered members,
evidence, filters, keyboard navigation, narrow layouts, inert hostile metadata
and zero external network requests. Fixture repositories remained unchanged.
See the [application source hashes and commands](../results/singleton-bypass-apps-checks.json),
[production installation](../results/singleton-bypass-production-installation.json),
[candidate installation](../results/singleton-bypass-singleton-installation.json),
[production browser](../results/singleton-bypass-production-browser.json) and
[candidate browser](../results/singleton-bypass-singleton-browser.json).

The complete regression suite passed all 66 tests. Six focused candidate tests cover direct singleton success, roots and explicit
bases, byte-limit boundaries, exhausted deadlines, no-process bypass, oversized
patches, 16+1 tails, filtered chunks, repeated endpoint pairs, generator closure,
truncated patch-ID output, subprocess failure, stderr/output caps, timeout and
cancellation. Every recorded child is reaped with closed pipes; file-descriptor
counts return to their starting value where /proc is available. They also run
three unchanged multi-request regressions against the candidate, including
SHA-256 repositories and malformed framing/IDs. The original regressions remain.

## Reproduction

With Python 3, Git, GNU time, Node and the pinned browser prerequisites below:

```sh
python3 scripts/reproduce_singleton_bypass.py --output-dir /tmp/singleton-reproduction
```

This runs the full regression suite, 200 three-way seeded stress histories,
isolated installed CLI and offline Chromium checks for both source packages,
396 sequential worker measurements, an independent evidence audit and publication
checks. Temporary Git fixtures are generated from fixed seeds and deleted.
Output is written to the chosen directory, preserving published evidence.

Browser preparation (the same pinned versions as the existing HTML verifier):

```sh
npm install --prefix /tmp/atlas-browser --cache /tmp/atlas-npm-cache playwright@1.58.2
PLAYWRIGHT_BROWSERS_PATH=/tmp/atlas-browser-binaries /tmp/atlas-browser/node_modules/.bin/playwright install chromium
```

The application verifier downloads the three pinned, hash-verified Python build
wheels specified in `verify_install.py`; the installed application and browser
fixtures run offline. No private data or paid inference is needed. The reproduction
script does not install a browser automatically. To audit saved timing evidence
without rerunning measurements:

```sh
python3 scripts/check_singleton_bypass.py
```

## Interpretation limits

Measurements use fresh processes on a shared Linux host. Fixture creation,
calibration, packing and repository snapshots warm filesystem caches. There is
no cold-cache claim or host cache eviction. All six permutations of implementation
order are retained, with no dropped observations or tuned replacement candidate.
Paired ratios are descriptive practical decision rules, not confidence intervals
or significance tests. Short runtimes are sensitive to scheduling noise.

Peak RSS is GNU time's maximum individual process, including waited-for children;
it is not simultaneous aggregate memory. Instrumented stage timings overlap and
must not be summed. Synthetic long-line boundary patches and small histories
limit generalization. The 600-second deadlines are nonbinding; deadline races,
concurrent repository mutation, other operating systems and other Git versions
are outside the equivalence claim. An ordinary subprocess with malformed output
retains the existing inspector's validation policy; this candidate adds no parser.
