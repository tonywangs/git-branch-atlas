# Group comparison validation

All fixtures use temporary local Git repositories. No private data, hosted model,
paid compute or credentials are needed. The ordinary application has no runtime
Python dependencies. Build/browser tools must be prepared before going offline.

## Reproduction

From the checkout with Python 3.10+, local Git and GNU `time` for benchmarks:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/verify_install.py
npm install --prefix /tmp/atlas-browser --cache /tmp/atlas-npm-cache playwright@1.58.2
PLAYWRIGHT_BROWSERS_PATH=/tmp/atlas-browser-binaries /tmp/atlas-browser/node_modules/.bin/playwright install chromium
export NODE_PATH=/tmp/atlas-browser/node_modules
export PLAYWRIGHT_BROWSERS_PATH=/tmp/atlas-browser-binaries
python3 scripts/verify_html.py --output results/group-browser.json
python3 scripts/benchmark_groups.py --repeat 3 --output results/group-benchmark.json
python3 scripts/check_publication.py
git diff --check
```

Tool installation requires network access. `verify_install.py --wheelhouse PATH`
uses the documented pinned, hash-checked wheels without downloading; otherwise it
downloads the wheels into a temporary directory. The installed examples then run
with Git network protocols disabled and dead HTTP proxies. Chromium uses an
offline context and rejects every non-file request. The runtime report has no
remote assets. Browser tooling stays outside the project repository.

## Independent checks

`tests/test_groups.py` uses seeds **0 through 199**, Python's `random.Random(seed)`,
two short independently generated histories of two to six mutations, and an extra
fork/merge/post-merge case every fifth seed. Paths `a`, `b`, `c` are created,
modified or deleted with one-line alpha/beta/gamma/whitespace variants. Each
fixture stores its own file snapshots and parent lists. Thresholds are seeded
choices among 3000, 5000 and 10000. Commit timestamps are not a scoring input;
OIDs may differ between runs. The known inputs and graph shape are reproducible.

The reference exhausts every permutation of two, three and four sampled members
and checks sole-parent adjacency using fixture records. It does not invoke the
production window enumerator. Direct `git rev-list` provides an independent
membership check. For every structural window it checks the exported endpoints
against fixture parents, identifies empty aggregates from independent snapshots,
and compares the normalized ID against a separately invoked `git diff BASE TIP |
git patch-id --stable`. It does not reuse the production diff options constant
or patch-inspection helper. Changed signed lines/paths come directly from fixture
snapshots; a separate list-based rational scorer determines every expected
single/group pair. The resulting set, scores, normalized-agreement flags and
comparison counts must equal the entire exported candidate set. No production
ranking or mapping is used as an oracle.

Named checks supplement the seeded one-line corpus:

- Split and squash in both orientations, including a two-member and four-member
  overlapping candidate for the same singleton.
- Edited aggregates; duplicates and revert/reapply; empty aggregate exclusion;
  intervening changes that must not be skipped; multiple competing singletons.
- Different context yielding score 10000 but **no** normalized patch agreement
  (an intentional negative result); no-final-newline changes.
- Transient binary, symlink, submodule and executable-mode changes even when the
  final endpoints are regular text; merges inside windows excluded; merges before
  a supported window allowed.
- Root-inclusive windows using empty-tree hashes in both SHA-1 and SHA-256 repos;
  shallow history and missing intermediate blobs withheld.
- Separate enumeration, comparison, candidate export, byte, feature and runtime
  bounds; preservation of individual results; default schema 1 and opt-in schema
  2; no report on artifact-size overflow.
- Hostile diff/textconv configuration, staged/dirty/untracked files and object
  snapshots. Reports leave file contents and modification times unchanged and
  never invoke configured external helpers.

Existing CLI, graph, summary, comparison, patch, series and HTML regressions remain
in the full suite, including their separate 100-history patch and series oracles.
The test log records actual test outcomes; failed experiments are not evidence of
correctness until repaired and rerun.

## Browser and installed use case

The Chromium driver compares embedded data with independently invoked CLI JSON.
It exercises all individual statuses, candidate ranks and bounded evidence for
small fixtures. New split/squash fixtures inspect every group candidate, exact
ordered members, both endpoints, scores, agreement and ambiguity. It also browses
all inspected group records, including exclusions. It verifies keyboard focus,
member navigation, candidate/window omissions, filtering with persistent notices,
hostile markup inertness and 360-pixel layout. Evidence panels share the existing
32-line mounted-evidence limit; pages contain at most 100 commit cards and 20
group entries. Measurements include observed peak DOM nodes and mounted elements.

The isolated installation script constructs an actual two-commit branch, runs
`git merge --squash` onto its base, commits that result, then compares both
orientations using the installed console from an unrelated directory. It checks
normalized endpoint agreement, ordered group members, empty difference evidence,
terminal output and HTML/JSON equality. The source checkout is not used for
imports by the installed console. Existing worktree/rebase examples also run.

## Benchmark design

`scripts/benchmark_groups.py` generates 12 and 500 total commits using fast-import
with fixed timestamps `1700000000+i`. Each side adds six or 250 separate files,
each containing 80 lines; even individual patches are identical across sides,
odd patches have one edited line. All revisions and limits are in the script.
It measures three repetitions per scenario with wall time and GNU time maximum
RSS, JSON/HTML bytes, counts of searched/omitted work, Chromium load times and
DOM counts. The inspection timeout is 600 seconds so intentional logical limits,
not a short unpredictable clock cutoff, define the expected searched scope.

The small case searches all windows. The 500-commit scenarios deliberately stop:

- 60 inspected windows, 4000 group comparisons and two exported candidates,
  exercising enumeration, comparison and candidate-output omissions together.
- All 1488 structural windows inspected, 1000 group comparisons and 500 exported
  candidates, exercising incomplete comparisons without window omission.

Both large scenarios retain the full existing individual results. The driver
paginates all commits and every exported group/window. For large reports it
samples two individual and two group candidate evidence inspections; it does not
claim to inspect every large candidate's evidence. The small browser fixtures
provide full evidence parity checks, including byte/key omissions and truncation.

## Limits of the evidence

These are synthetic correctness and bounded-performance checks, not a corpus of
real historical split/squash labels or a precision/recall estimate. The algorithm
can miss groups longer than four, noncontiguous or unsupported changes and
candidates below threshold or outside budgets. It can propose false positives.
Overlaps and equal scores are valid outputs, not optimization failures. Empty
feature differences and perfect scores are weaker than normalized patch-ID
agreement; neither establishes behavioral equivalence or historical provenance.

Measurements are warm-cache, non-isolated observations, not performance guarantees.
GNU time reports the maximum RSS of the CLI or one child, not summed concurrent
memory. Browser load includes navigation and the DOM measurement observer; browser
peak memory is not measured. Chromium on Linux is validated; other browsers,
Windows/macOS file behavior, screen readers and a formal accessibility audit remain
untested. The previous milestone's measurements in other result files describe
its implementation at that time, not a claim of current group performance.

## Recorded results

On Linux x86-64 / Python 3.12.3 / Git 2.43.0 / Chromium 145.0.7632.6:

- The full suite passed **47 tests**, including the 200 seeded group histories
  and retained existing oracles/regressions. Focused CLI/bounds checks also passed
  after the final argument-validation and feature-limit test additions.
- All **14** offline browser fixtures passed, with zero external requests, CLI
  evidence parity, keyboard navigation and unchanged fixture repositories.
- The isolated installed CLI passed both split and squash orientations, alongside
  the retained graph/worktree/rebase checks.
- All **nine** repeated benchmark generations and browser playthroughs passed.
  Incomplete large searches are expected outcomes verified by the harness.

| Workload | Generation seconds, median (range) | Max RSS KiB | JSON / HTML bytes | Median browser load ms | Peak DOM nodes |
| --- | --- | ---: | ---: | ---: | ---: |
| 12 commits, complete | 4.881 (4.881–5.126) | 20,096 | 63,327 / 57,341 | 185 | 212 |
| 500, enumeration/output/comparison limited | 31.501 (31.114–33.687) | 24,492 | 707,978 / 445,655 | 255 | 604 |
| 500, all windows inspected, comparisons limited | 99.807 (89.827–130.810) | 31,196 | 1,642,949 / 1,155,140 | 259 | 604 |

The small case inspected 24 windows and all 144 group pairs, retaining 20
candidates. The first large case omitted 1428 of 1488 windows, searched 4000 of
15000 eligible inspected pairs, and retained two of 14 observed candidates. The
second large case inspected all 1488 windows, searched 1000 of 372000 pairs, and
retained six observed candidates. All omitted counts are explicit in the reports.
These synthetic adjacent additions are not ground-truth historical splits;
their heuristic candidates illustrate why positive scores require review.

Benchmark pages peaked at 100 commit cards, 20 group rows and four evidence
lines. The separate bounded group-evidence fixture mounted 16 lines and verified
key/byte omissions; the retained individual fixture mounted 18. All remained
within the global 32-line evidence bound. Observed peaks are not claims that every
possible maximum was exercised.

Generation overlapped other validation activity on this host. The large runtime
variation is retained, not averaged away or presented as an improvement over
previous milestones. Raw results and actual verification output are in
[group-benchmark.json](../results/group-benchmark.json),
[group-browser.json](../results/group-browser.json),
[group-installation.json](../results/group-installation.json) and
[tests.log](../results/tests.log).
