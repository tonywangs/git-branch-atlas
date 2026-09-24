# Git Branch Atlas

A read-only terminal map of a local Git repository, with branch/upstream summaries,
linked-worktree locations, ancestry comparisons, and bounded patch-series review with stable patch-ID groups and ranked edited-patch candidates. Uses
Python 3.10+ and Git 2.43+; no third-party runtime dependencies. Tested here on
Python 3.12.3 and Git 2.43.0 on Linux. It does not contact remotes.

## Install and use

```console
python3 -m pip install .
git-branch-atlas --help

# Existing graph interface is preserved; "graph" is optional.
git-branch-atlas --repo "../my project" --all --max-count 15 --no-color
git-branch-atlas graph --base refs/heads/main

# Every local branch, its configured upstream, and all worktrees.
git-branch-atlas --repo "../my project" summary
git-branch-atlas summary --json

# First resolve these two names to full commit IDs, then compare those IDs.
git-branch-atlas compare refs/heads/main refs/heads/topic
git-branch-atlas compare main topic --max-count 20 --json
```

Without installing, replace `git-branch-atlas` with `python3 -m git_branch_atlas`
from this checkout. Options can go before or after the command and revisions.
`--repo` accepts a repository, an interior directory, a bare repository, or a linked
worktree. `--all` and `--base` apply only to graph. `--json` applies to summary,
compare, patches, and series. Errors go to stderr with exit status 2; complete reports exit 0.
Patch and series comparison return exit 1 for a valid but incomplete report.

## Reading reports

**Summary:** `+A/-B` compares each branch to **its own upstream**. `none` means no
upstream is configured; `missing` means the configured tracking ref is absent;
`unmapped` means configuration exists but Git cannot map it to a tracking ref.
No-upstream and unavailable-history counts are `null` in JSON, never assumed zero.
Full upstream configuration (remote and merge refs) is retained. If multiple merge
refs are configured, counts follow the single upstream selected by Git.

Worktrees show their paths and branch or detached commit, plus separate `locked`
and `prunable` flags and reasons. The main worktree is included; bare repositories
have a bare record. These flags describe Git's administrative metadata, not whether
a worktree is clean. Atlas does not inspect working files or refresh an index.

**Compare:** left/right unique counts are sizes of the reachable commit sets after
subtracting their intersection. All best common ancestors are listed, including
multiple merge bases in criss-cross history. No common ancestor is reported as
`unrelated`, with an empty merge-base list. Inputs must each resolve to one commit;
annotated tags and revision expressions such as `main~2` are accepted. Ambiguous
short names are rejected: use `refs/heads/NAME`, `refs/tags/NAME`, or a full ID.
Names starting with `-` are rejected. SHA-1 and SHA-256 object IDs are supported.

Unique commits remain unique after cherry-picking even if the patches or resulting
trees match. **These are ancestry counts, not patch-equivalence counts or a merge
simulation. Ancestry does not establish conflict-free merging.**

**Graph:** the original overview and Git graph remain available. Overview counts
are relative to `--base`, otherwise the attached HEAD's upstream, then `main`,
`master`, or the attached branch. `*` marks the attached branch; detached HEAD is
identified. In a bare repository it uses that repository's HEAD. The graph uses
Git's topology and decorations. Atlas applies color to its own headings/identity
only on a terminal; `--no-color` or `NO_COLOR` disables it.

## Multi-branch, multi-worktree example

Run in a temporary directory with Git author identity configured (the commands
below configure only this example):

```sh
mkdir atlas-example
cd atlas-example
git init -b main
git config user.name 'Atlas Demo'
git config user.email 'demo@example.invalid'
git commit --allow-empty -m Root
git branch topic
git worktree add '../linked café' topic
git -C '../linked café' commit --allow-empty -m Topic
git commit --allow-empty -m Main
git branch --set-upstream-to=main topic
git worktree lock --reason 'demo hold' '../linked café'
git-branch-atlas summary
git-branch-atlas compare main topic --json
git-branch-atlas --all --no-color
```

Summary should report two worktrees, a locked topic worktree, and `topic` at
`+1/-1` against `main`. Comparison should report one unique commit per side and
one merge base. [The installation check](scripts/verify_install.py) executes this
scenario using an installed console command outside the source checkout.

## Offline patch comparison

```console
git-branch-atlas patches main topic
git-branch-atlas patches main topic --json --max-count 500 --timeout 60
```

This compares stable patch IDs of nonmerge commits unique to each reference.
Matches are groups containing all observed commit IDs on each side, so duplicates
remain visible. Unmatched, excluded and unclassified commits are separate.
**Matching patches do not prove semantic equivalence, current branch contents,
safe cherry-picking or conflict-free merging.** Whitespace is normalized; reverted
and reapplied patches can still match. Shared ancestors are outside the search.

Try a real cherry-pick offline after installation:

```sh
mkdir patch-example
cd patch-example
git init -b main
git config user.name 'Atlas Demo'
git config user.email 'demo@example.invalid'
git commit --allow-empty -m Root
git branch topic
printf 'useful change\n' > feature.txt
git add feature.txt
git commit -m Feature
git switch topic
git commit --allow-empty -m 'Different base'
git cherry-pick main
git-branch-atlas patches main topic --json
```

Expect one matching group with distinct full commit IDs and an excluded empty
commit on the right. This console workflow is exercised by the isolated installer.
Binary changes (NUL in either changed blob), executable-bit transitions, symlinks,
submodules, empty commits and merges are explicitly excluded. Renames are treated
as delete/add; regular text root commits are supported. No diff/textconv helpers
or lazy downloads run.

Defaults bound candidates to 30 per side, cumulative raw diff/blob/patch reads to
32 MiB, Git runtime to 30 seconds, and output to 2 MiB. Use `--max-count`,
`--max-diff-bytes`, `--timeout`, and `--max-output-bytes` to configure those limits.
Truncation, missing objects and shallow history never produce definitive unmatched
classifications: read `complete` and both `enumeration_complete` flags. An output
that exceeds its cap is rejected before anything is printed. See the full
[patch scope, deterministic settings, limits and JSON v1 contract](docs/patches-v1.md).
The pipe implementation is currently validated on Linux/POSIX.

## Before/after rebase review

Use four explicit endpoints to review two versions of a patch series:

```console
git-branch-atlas series old-base before main topic
git-branch-atlas series old-base before main topic --json --max-comparisons 10000
```

Exact patch-ID groups preserve duplicates. Edited-patch candidates have integer
scores, ranks, ambiguity flags and bounded normalized patch-difference evidence.
No pairing is forced. Messages do not affect these scores. **Similarity does not
establish semantic equivalence or safe cherry-picking.**

A complete offline example after installation (run in a new temporary directory):

```sh
mkdir series-example
cd series-example
git init -b main
git config user.name 'Atlas Demo'
git config user.email 'demo@example.invalid'
git commit --allow-empty -m Root
git branch old-base
git switch -c topic
printf 'exact change\n' > exact.txt
git add exact.txt
git commit -m Exact
printf 'alpha\nbeta\nold\n' > edited.txt
git add edited.txt
git commit -m Edited
git branch before
git switch main
printf 'new base\n' > base.txt
git add base.txt
git commit -m 'Advance base'
git rebase --onto main old-base topic
printf 'alpha\nbeta\nnew\n' > edited.txt
git add edited.txt
git commit --amend --no-edit
git-branch-atlas series old-base before main topic --json
```

Expect one exact group and one edited candidate scoring 7333/10000. The isolated
installation check executes this workflow with network transports disabled during
application use. See [series v1 semantics and limits](docs/series-v1.md) and
[validation evidence](docs/series-validation.md). Splits, squashes and unsupported
file types remain outside automatic correspondence. Incomplete searches never
claim definitive absence; read `complete`, warnings, and commit statuses.

## Completeness, safety, and limits

The following describes graph, summary and ancestry compare; patches and series have
separate bounded contracts linked above.

- An unborn repository has an explicit `unborn` HEAD and no local branches yet.
  Summary and graph work; comparison requires actual commits. With `--all`, graph
  can show other refs even if HEAD is unborn.
- Shallow repositories get summary metadata, a warning, `history.complete: false`,
  and unavailable counts. Compare and graph return an actionable error. Obtain
  full history separately if desired; Atlas never fetches it.
- Missing or corrupt reachable **commit** objects fail the report with no partial
  JSON. Parents are traversed even for identical tips. Commit-graph acceleration
  is disabled so a stale cache cannot hide missing commits. Trees/blobs are not
  checked: this is ancestry inspection, not `git fsck` or a working-tree audit.
  A partial clone with all required commits available can be inspected offline.
- Replacement refs are ignored to report original object ancestry. Legacy
  `info/grafts` is rejected because it can silently change parents.
- Git transport protocols are all disabled, including file and external helpers.
  `GIT_NO_LAZY_FETCH=1` is also set; the protocol denylist protects older supported
  Git versions. Hooks, paging, prompts, optional locks, signature display, and
  notes display are disabled. Caller `GIT_*` environment variables are discarded
  so `--repo` selects the inspected repository; normal Git configuration is read.
- No fetch, checkout, prune, status, index refresh, signature verifier, diff driver,
  or credential helper is invoked by the application. Worktree enumeration reads
  administrative metadata and checks location availability through Git.
- Terminal output escapes control and Unicode format/bidi characters from names,
  paths, messages, and errors. JSON uses escaped Unicode strings and preserves
  unusual paths, including POSIX non-UTF-8 bytes via surrogate escapes. Treat JSON
  values as untrusted when rendering them elsewhere. The graph preserves literal
  backslashes for topology; summary/comparison escape them in text fields.
- `--max-count N` (positive integer, default 30) limits graph commits or displayed
  unique commits **per comparison side**, not ancestry counting. JSON includes
  total counts, `truncated` per side, and the limit. Merge bases are never truncated.
  Summary lists all local branches and worktrees, with no pagination. No remote
  branches are listed except configured upstream relationships.
- Reports are observations, not an atomic repository snapshot. Comparison pins its
  two resolved IDs; concurrent ref/worktree/configuration changes can still affect
  metadata. Run against a quiescent repository for reproducible reports.
- Each Git subprocess has a 60-second timeout. Total time and output grow with
  history and branch count. A very large repository can exceed those limits;
  changing the display limit does not reduce full ancestry validation work.

See [JSON v1](docs/json-v1.md) for the machine-readable contract.

## Verification and measurements

```console
python3 -m unittest discover -s tests -v
python3 scripts/verify_install.py
python3 scripts/benchmark.py --commits 5000 --branches 100 --repeat 3
python3 scripts/benchmark_patches.py --commits 250 --repeat 3
python3 scripts/benchmark_series.py --commits 250 --repeat 3
python3 scripts/compare_range_diff.py
python3 scripts/check_publication.py
```

The tests retain the original graph regressions and use an independent Python
parent-set traversal to check counts, unique commit sets, and all best common
ancestors. Fixtures cover linear, diverged, merged, cherry-picked, unrelated,
identical, and criss-cross histories, plus generated DAGs with seed `20260923`.
Integration cases cover bare/unborn/detached repositories, linked worktrees,
missing/unmapped upstreams, shallow and missing objects, ambiguous refs, malformed
arguments, terminal controls, unusual paths, replacement refs, and SHA-256.
Repository preservation checks compare file bytes and modification times, including
refs, config, index, worktree metadata, and local working files. A fake promisor
transport helper verifies that missing objects cannot invoke a download helper.

The isolated installer bootstraps pinned, SHA-256-verified pip/setuptools/wheel
wheels from PyPI into a temporary environment; it needs network access **only for
build tools**. `--wheelhouse PATH` uses the exact named wheels offline. Nothing is
installed globally. This works even when the host Python lacks pip/ensurepip.
The application and benchmark themselves are offline. Windows/macOS were not
validated in this milestone; the earlier five-test pilot ran on macOS.

[Recorded benchmark](results/benchmark.json), [installation evidence](results/installation.json),
and [test output](results/tests.log) contain actual runs, not estimates. The
benchmark generates exactly 5,000 commits and 100 local branches using a fixed
fast-import stream, checks expected counts, and times full CLI processes three
times. It uses empty trees, a 4,901-commit linear trunk and 99 one-commit topic
branches. It measures sequential warm-cache execution; it is not a cold-cache or
real-world repository performance claim. Rerunning prints fresh measurements
without replacing the recorded result unless `--output PATH` is given.

Patch tests additionally cover **100 histories with seeds 0..99**, independently
specified many-to-many groups, direct stable patch-ID plumbing and `git cherry`.
Targeted cases exercise real cherry-pick/rebase, revert/reapply, whitespace, roots,
renames, empty commits, merges, binary/special files, unusual byte paths, SHA-256,
hostile configuration, missing objects, shallow history and resource exhaustion.
[Patch measurements](results/patch-benchmark.json) record actual CLI elapsed time,
GNU time peak RSS, output bytes and limit-exhaustion outcomes. These use a fixed
fast-import workload and warm caches; peak RSS is the maximum for the CLI or an
individual child, not their combined concurrent memory footprint.

## Existing foundations

Atlas packages existing Git capabilities into consistent read-only reports; it
makes no claim to a new ancestry algorithm. Its definitions follow Git's
[revision set operations](https://git-scm.com/docs/git-rev-list) and
[all best merge bases](https://git-scm.com/docs/git-merge-base). Metadata comes from
[for-each-ref](https://git-scm.com/docs/git-for-each-ref) and the documented
[NUL-delimited worktree porcelain](https://git-scm.com/docs/git-worktree).
The offline process boundary uses Git's documented
[environment controls](https://git-scm.com/docs/git).

### Offline HTML series review

```sh
git-branch-atlas --repo /path/to/repo series OLD_BASE OLD_TIP NEW_BASE NEW_TIP --html > review.html
```

Open the self-contained file directly in your browser. Filter ordered commits,
navigate duplicate patch-ID groups, and expand ranked candidate evidence. Incomplete
searches and ambiguity remain explicit. The file embeds repository content; filters
are not redaction. See [HTML review setup, controls, bounds and validation](docs/html-review.md).

## Review possible splits and squashes

Add `--groups` to a series comparison to compare a single commit with every
eligible contiguous group of two to four commits in the searched ranges:

```sh
git-branch-atlas series old-base old-tip new-base new-tip --groups --json
git-branch-atlas series old-base old-tip new-base new-tip --groups --html > review.html
```

This opts into series schema 2; the default schema and individual results stay
unchanged. Group changes come from endpoint trees, with explicit exclusions,
resource limits and omission counts. The offline viewer shows ordered members,
endpoints, bounded evidence and competing/overlapping candidates. Normalized patch
agreement is not behavioral equivalence or proof of a historical split/squash.
See [schema, limits and examples](docs/groups-v1.md) and
[reproducible validation and measurements](docs/groups-validation.md).
