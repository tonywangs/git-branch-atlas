# Explicit patch-series review (schema version 1)

```
git-branch-atlas --repo /path/to/repo series OLD_BASE OLD_TIP NEW_BASE NEW_TIP
git-branch-atlas --repo /path/to/repo series OLD_BASE OLD_TIP NEW_BASE NEW_TIP --json
```

The four arguments are **individual commit revisions**, not range expressions.
All endpoints resolve to full commit IDs before inspection. The command reads local
objects only, works with bare repositories, and does not change refs, index, worktree,
configuration, or object storage. Exit codes: 0 complete within the supported scope;
1 incomplete report; 2 invalid input, unresolved endpoint, repository error before
report construction, or output budget exceeded (no partial stdout).

## Membership and order

Each side is independently `reachable(tip) - reachable(base)`, exactly the set
selected by `git rev-list TIP ^BASE --`. Bases need not be ancestors, and unrelated
histories are allowed. Shared commits can appear on both sides. Roots included by
that expression are compared with the empty tree. This is not a first-parent walk.

Commits are ordered parents before children, choosing the lexicographically smallest
full OID among currently ready commits. Dates, messages, branch names and user locale
do not determine that order. At the commit limit, Git's newest `--topo-order` prefix
is sampled, then ordered using the same rule within the sample. Sampling follows the
installed Git's traversal/tie behavior; omitted commits are never implied absent.
`parents` lists actual parents, including those outside the range/sample.

Shallow history and failed ancestry validation make membership incomplete and withhold
patch IDs. Missing changed blobs leave commits unclassified. Missing endpoints cannot
produce a report with four resolved IDs and therefore yield exit 2. Replace refs are
ignored and legacy grafts rejected. These restrictions apply even to empty ranges.

## Exact matches and exclusions

Exact groups reuse the `patches` implementation: explicit Myers text diffs with three
context lines, no indent heuristic, no rename detection (renames become delete/add),
no external diff or textconv, followed by `git patch-id --stable`. See
[patches-v1](patches-v1.md). Whitespace and hunk line numbers are ignored by Git;
file diff ordering is stable-normalized. Paths and context can affect patch IDs.
Commit messages, author identity, timestamps and notes do not enter patch IDs.

Every group retains **all** observed OIDs in range order, including revert/reapply
duplicates. There is no one-to-one pairing. Exact groups take precedence: their
commits are removed from heuristic candidate pools. A group may be partial when
inspection is incomplete. Equality of IDs is not proof of semantic equivalence,
current contents, safe cherry-picking, or conflict-free merging.

Merges, empty commits, changes with any NUL in a changed old/new blob, executable
mode transitions, symlinks and submodules are explicitly excluded. Regular 100644
and 100755 additions/deletions are supported. NUL detection is a conservative byte
rule, not universal binary detection. Splits/squashes have no automatic mapping.
Exclusions do not by themselves make a report incomplete; completeness is relative
to this supported scope.

## Heuristic `signed-line-dice-path-jaccard-v1`

For each supported commit without an observed exact group:

1. Extract added/deleted lines from diff hunks, excluding headers, context, hunk
   offsets and the no-final-newline marker. Preserve each line's `+` or `-` sign.
2. Delete the six ASCII whitespace bytes (space, tab, LF, CR, VT, FF) from each
   changed line. Count the resulting signed byte strings as a multiset. Repeated
   lines count repeatedly. Line order and association with individual paths are
   ignored. Empty normalized lines still count.
3. Collect the set of changed paths as raw bytes from NUL-separated `diff-tree
   --raw -z`, without normalization. Case, whitespace and invalid UTF-8 in paths
   remain significant to the path term. Renames have both deleted and added paths.

Let `I` be multiset intersection size, `A,B` the total signed-line counts, and
`J = |pathsA ∩ pathsB| / |pathsA ∪ pathsB|`. The integer score is
`floor(8000 * 2I/(A+B) + 2000 * J)`, with **one final floor**. A zero intersection
always scores zero. Scores are basis points, not probabilities. The default inclusive
threshold is 5000; `--threshold 1..10000` changes it. Messages have no effect.

Pairs are examined in left range order then right range order, excluding exact
commits. Both directions retain at most five candidates, sorted by descending score
then full counterpart OID. Equal scores have equal competition ranks (1,1,3).
`candidate_count` counts all observed pairs above threshold, `candidates_omitted`
counts those beyond five, `top_tie_count` counts all observed best-score ties.
`ambiguous` means more than one observed candidate meets the threshold, regardless
of whether the best score is unique. Even an unambiguous candidate is only a heuristic.
No global assignment or mutual-best inference is performed.

Every displayed candidate contains `normalized-feature-difference-v1` evidence:
multiset counts present only in the source or counterpart, plus differing path sets.
Each of the four categories contains at most eight lexicographically ordered byte
keys, each truncated to 160 original bytes; counts, original byte length, truncation
and omitted-key counts are explicit. Text uses UTF-8 with backslash escapes for
invalid bytes and JSON/terminal control escaping. This display is not a lossless
patch encoding, does not include context, and must not be applied as a patch.
`source_only` always describes the commit containing the candidate list, and
`counterpart_only` describes that candidate, on either side of the report.

## JSON and completeness

The top-level discriminator is `kind: "series", schema_version: 1`. Existing
`graph`, `summary`, `compare`, and `patches` formats remain unchanged.

Top-level fields include the algorithm identifier, score scale, threshold, limits,
comparison count, inspection bytes, warnings, completeness, exact `matches`, and
`left`/`right`. Each side contains `{base: {revision, oid}, tip: {revision, oid},
enumeration_complete, commits}`. Each ordered commit has `oid`, `parents`, and status:

| Status | Meaning |
| --- | --- |
| `patch_id_match` | Member of an observed exact group; `patch_id` supplied |
| `heuristic_candidates` | At least one observed score meets threshold |
| `no_candidate` | Complete supported search; no score meets threshold |
| `excluded` | Unsupported change, with explicit `reason` |
| `unclassified` | Inspection/features unavailable, with `reason` |
| `incomplete_search` | Inspected but no observed candidate; search incomplete |

Heuristic-search records have `search_complete`, candidate counts, ambiguity and
`candidates` (`oid`, `score`, `rank`, `evidence`). If any range, inspection or scoring
is incomplete, all candidate searches are conservatively marked incomplete and all
rankings are provisional. `no_candidate` is then prohibited. Observed exact groups
remain valid observations, but might omit additional members. No candidate does
not mean the change is absent from the other tip's current tree.

## Bounds and offline behavior

| Budget | Default | Accepted range |
| --- | ---: | ---: |
| `--max-count` per side | 30 | 1–1000 |
| `--max-diff-bytes` total raw diff, blob and patch bytes | 32 MiB | 1–256 MiB |
| `--max-comparisons` candidate pairs | 10000 | 0–100000 |
| `--timeout` shared elapsed processing budget, seconds | 30 | >0–3600, finite |
| `--max-output-bytes` including trailing newline | 2 MiB | 1 KiB–16 MiB |

Additional fixed bounds: 20,000 changed lines per commit for scoring, five retained
candidates per commit, four evidence categories × eight items × 160 bytes per item,
8 MiB enumeration stdout per side, 1 MiB other subprocess stdout, 64 KiB stderr.
Feature exhaustion preserves computed exact IDs but cannot support definitive
heuristic absence. Candidate-count exhaustion and timeouts produce incomplete
reports. The inspection-byte reader can consume one excess sentinel byte to detect
exhaustion. Output overflow emits an error instead of truncated/invalid JSON.

The shared deadline kills and reaps a timed-out Git subprocess; Python feature
extraction checks it every 1024 lines, and scoring before each bounded pair. Bounded
evidence construction and rendering can finish after this deadline; it is not a
hard OS limit on whole-process elapsed time or memory. Enumeration walks can exceed
the sampled commit count internally, but remain under the subprocess deadline.

Git runs with external diff/textconv disabled, caller `GIT_*` overrides removed,
optional locks disabled, replacement objects disabled, lazy fetch disabled and all
transport protocols disallowed. POSIX streaming/selectors are required. No network
is needed to use the installed command. Normal repository diff attributes may still
influence textual hunk selection; fixtures test hostile external driver settings.

## Related tools and known weaknesses

[Git range-diff](https://git-scm.com/docs/git-range-diff) includes author/message/diff
changes in its heuristic correspondence and produces human-oriented, non-stable
porcelain output. This command instead exposes independent scored candidates and
versioned JSON. [Git patch-id](https://git-scm.com/docs/git-patch-id) supplies exact
normalized groups; [rev-list](https://git-scm.com/docs/git-rev-list) supplies membership.
This is a small review aid built on those established tools, not a novelty claim.

Common boilerplate can cause false candidates. Reordering lines, moving content
between files, and ignoring whitespace can obscure meaningful changes. A complete
rewrite may have no overlap and no candidate. Paths weigh only 20%, so identical
content in unrelated files can score 8000. Added and removed lines differ by sign;
a revert is not automatically matched to the original. Thresholds are a documented
engineering choice, not calibrated probabilities or a semantic benchmark.

See [series validation](series-validation.md) for reproducible checks, observed
range-diff differences and measured workloads.

## Interactive HTML

`series ... --html` renders the same report as a self-contained offline HTML file.
The JSON schema and comparison algorithm are unchanged. See
[HTML review](html-review.md) for controls, byte/DOM limits, confidentiality and
reproducible browser checks. `--html` and `--json` are mutually exclusive.
