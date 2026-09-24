# Split and squash candidate review

Opt in with `git-branch-atlas series LEFT_BASE LEFT_TIP RIGHT_BASE RIGHT_TIP
--groups --json` or `--groups --html`. The four revisions retain normal
reachability-difference semantics: each side contains ancestors of its tip that
are not ancestors of its base. Bases need not be ancestors of tips. Use saved
refs from before and after a split, squash or rebase. The application only reads
Git objects; it does not split, squash, apply patches or change repository state.

For example, after saving `before-base`, `before-tip`, `after-base`, `after-tip`:

```sh
git-branch-atlas series before-base before-tip after-base after-tip --groups --json > review.json
git-branch-atlas series before-base before-tip after-base after-tip --groups --html > review.html
```

Check the exit status: 0 means complete within supported scope and configured
threshold, 1 is an incomplete but usable report, 2 is invalid input or output
overflow (no report emitted). Redirection can leave an empty file on error.
Default commands and JSON remain unchanged. `series --groups` emits **series
schema version 2**, with a **group_comparison schema version 1** extension. Both
single-to-group orientations are searched in one invocation. Swapping sides
is also supported; limits can make the searched subsets differ.

## Try a local sample

After installing the application, this creates an isolated example repository.
The Git mutation commands below prepare the sample; Atlas only reads it.

```sh
atlas_demo=$(mktemp -d)
git -C "$atlas_demo" init -b main
git -C "$atlas_demo" config user.name 'Atlas Demo'
git -C "$atlas_demo" config user.email 'demo@example.invalid'
git -C "$atlas_demo" commit --allow-empty -m Root
git -C "$atlas_demo" branch base
git -C "$atlas_demo" checkout -b pieces
printf 'alpha\n' > "$atlas_demo/feature"
git -C "$atlas_demo" add feature
git -C "$atlas_demo" commit -m 'First piece'
printf 'alpha\nbeta\n' > "$atlas_demo/feature"
git -C "$atlas_demo" add feature
git -C "$atlas_demo" commit -m 'Second piece'
git -C "$atlas_demo" checkout -b squashed base
git -C "$atlas_demo" merge --squash pieces
git -C "$atlas_demo" commit -m 'Combined feature'
git-branch-atlas --repo "$atlas_demo" series base pieces base squashed --groups --json
git-branch-atlas --repo "$atlas_demo" series base squashed base pieces --groups --html > "$atlas_demo/review.html"
```

Open the printed sample directory's `review.html`. Both orientations contain the
two ordered pieces as one group and the combined commit as a singleton, with
normalized patch agreement. Individual candidates remain visible as well. The
isolated installation verifier exercises this workflow using its installed CLI.

## What an aggregate means

A group contains two, three or four ordered commits. Every member must have at
most one parent; each next member must have exactly the previous member as its
sole parent. Every member must occur in the sampled side. Membership follows
parent links, never neighboring display rows. Other branches may interleave in
the display; they neither create nor break an actual parent chain. A merge may
be the *base endpoint* of a subsequent linear group, but may not be a member.
Groups are not required to be disjoint. Groups of five or more commits,
group-to-group comparisons, and noncontiguous collections are outside scope.

The change is a fresh `git diff-tree` from the first member's parent to the last
member, **not concatenated commit patches**. For a root-inclusive group the
base is the repository-format empty-tree hash (computed without writing an
object); `base_kind` is `empty_tree`. Otherwise the base is a resolved full commit
ID. Every candidate exposes both endpoints and all ordered member IDs. A revert
can cancel an intermediate edit; an entirely empty endpoint diff is excluded.
An empty member is permitted inside an otherwise nonempty aggregate.

Member inspections and endpoint inspection exclude binary changed blobs (NUL
anywhere), symlinks, submodules, unsupported file modes and executable mode
changes. Unsupported intermediate changes remain excluded even if later members
undo them. Missing objects, shallow history, feature/byte/time exhaustion cause
incompleteness, not evidence of absence. Grafts are rejected, replacements ignored,
and lazy network fetching, hooks, external diff programs and textconv are disabled.

The canonical diff uses the existing explicit Myers settings, three context
lines and full indexes; renames are represented as deletion/addition. Stable
`git patch-id --stable` agreement ignores whitespace and hunk line numbers. It
is called **normalized patch agreement**, not an exact byte match. It does not
prove behavioral equivalence or a historical split/squash relationship. Context,
file names, modes and newline details can matter differently to patch IDs and
the heuristic; a score of 10000 does not imply patch-ID agreement.

The heuristic remains `signed-line-dice-path-jaccard-v1`: 80% multiset Dice of
signed changed lines after deleting ASCII whitespace, plus 20% changed-path
Jaccard, with an integer floor on the combined basis-point score. Zero signed
line overlap scores zero. Context, order and within-file line positions are
not heuristic features. Thus unrelated changes can score highly; a whitespace
change may matter behaviorally. Scores are not probabilities. A candidate is
retained when its aggregate and singleton patch IDs agree **or** its score meets
`--threshold`. Singleton eligibility includes commits already matched by the
individual engine; individual matches never suppress group candidates.

## Versioned JSON extension

All original individual fields (`left`, `right`, `matches`, `comparison_count`,
`algorithm`, `threshold`, `score_scale`, `limits`, `warnings`) retain their existing
meaning/results. Individual processing runs first. `individual_complete` saves
its completeness. Top-level `complete` combines individual and group completeness;
`notice` covers aggregate uncertainty and `inspection_bytes_read` counts the shared
inspection. Group warnings are in the extension. A group-only limit does not
change individual results. An individual pair-comparison limit need not prevent
a complete group search, if inspection/time budgets remain.

`group_comparison` contains:

- `schema_version: 1`, `algorithm`, `scope`, `notice`, `complete`, `warnings`,
  `limits`, and `search_order`.
- `sampled_scope.left/right`: sampled commit count, commit enumeration
  completeness and the number of structural windows in that sample.
- `structural_windows`, `windows_omitted`: exact counts *inside* the sampled
  ranges. Counts beyond incomplete commit enumeration are unknown, never zero
  by implication. Windows omitted by the group limit have no exported records.
- `excluded_members`: side, OID and reason for individually excluded commits,
  including empty commits (which are allowed as group members).
- `singles`: eligible singleton records with `side`, `oid`, `base_oid`,
  `base_kind`, `tip_oid`, `patch_id`, `candidate_count`. Counts include observed
  group candidates omitted by the candidate output cap.
- `groups`: records with `id` (`SIDE:FIRST..LAST`), `side`, ordered `members`,
  `base_oid`, `base_kind`, `tip_oid`, `status`, `candidate_count`. `status` is
  `eligible`, `excluded`, or `unclassified`; exclusions/unclassified records
  include `reason`. Endpoints can be null when inspection cannot resolve them.
  Supported records include `patch_id` and `changed_lines`. An unsupported member
  adds `excluded_members` OIDs. Active candidate groups also expose
  `overlapping_candidate_groups`, considering all observed candidates even when
  some candidates are omitted from export.
- `comparisons_possible`, `comparison_count`, `comparisons_unsearched`: eligible
  inspected group × opposite eligible singleton counts. These do **not** include
  uninspected windows or unavailable features. Exclusions are not comparisons.
- `candidate_count`, `candidates_omitted`, `candidates`: every observed candidate
  contributes to counts; the retained prefix is explicit. Candidates contain
  `single_side`, `single_oid`, `group_id`, `score`,
  `normalized_patch_agreement`, `ambiguous` and `evidence`. Evidence's source is
  always the singleton; counterpart is the aggregate, regardless of left/right.
- `inspection_bytes_read`: additional bytes consumed by group inspection.

No global assignment is solved. `ambiguous` indicates competing observed groups
for a singleton, competing observed singletons for a group, or shared members
among observed candidate groups. It does not measure confidence; false does not
mean unique history or exclude unseen alternatives. Existing individual results
remain available for separate review. Candidate order is deterministic search
order, not a ranking or a claim that earlier candidates are better. All competing
candidates within the searched scope are kept until the explicit export cap.

Evidence is `normalized-feature-difference-v1`, with `source_only`,
`counterpart_only`, `paths_source_only`, `paths_counterpart_only`. Each is a
multiset sample of up to eight sorted distinct keys, each truncated to 160 source
bytes and decoded using UTF-8 with backslash replacement. Items contain `text`,
`count`, original `bytes` and `truncated`; `omitted_items` counts distinct keys
not sampled. Empty evidence does not establish patch-ID or behavioral agreement.
It is not an applicable diff, and cannot reconstruct full changes.

## Explicit limits

| Option | Default | Accepted range | Scope |
| --- | ---: | ---: | --- |
| `--max-count` | 30 | 1–1000 | Commits per side, newest topological sample |
| `--max-groups` | 600 | 0–6000 | Inspected structural windows across both sides |
| `--max-group-comparisons` | 20000 | 0–100000 | Additional single/group pairs |
| `--max-group-candidates` | 500 | 0–2000 | Exported group candidates |
| `--max-comparisons` | 10000 | 0–100000 | Existing individual pair search |
| `--max-diff-bytes` | 32 MiB | 1–256 MiB | Shared streamed raw diff, blobs and patch bytes |
| `--timeout` | 30 s | >0–3600 s | Shared Git inspection and scoring deadline |
| `--max-output-bytes` | 2 MiB | 1 KiB–16 MiB | Entire JSON/text/HTML artifact plus newline |

The algorithm first counts at most three structural windows per sampled commit
(less at roots and boundaries). It inspects their prefix, ordered by left groups
then right groups, parent-before-child last-member order then increasing length.
Each eligible group compares with opposite singletons in existing series order.
A low cap can favor the left group's orientation; omissions are reported. An
export cap retains the first candidates but continues counting within comparison
and runtime bounds. It sets incomplete when candidates are actually omitted.
The hard feature cap is 20,000 changed lines per singleton or endpoint patch.
Repeated endpoint blobs/diffs are charged again to the shared byte budget.

Exhausted bytes may read one sentinel byte past the configured cap to detect
exhaustion, as in existing patch inspection. Repository discovery, history
validation and individual inspection precede groups. An overall deadline failure
during initial revision resolution can still produce exit 2. JSON/HTML formatting
and bounded bookkeeping are outside the Git processing deadline. Full artifact
size overflow emits no partial report; increase the output limit or reduce scope.
This is intentionally bounded review, not exhaustive repository-wide inference.

## Offline viewer

Existing individual review controls, exact patch-ID duplicate groups, 100-card
pagination and keyboard navigation remain. The separate **Split and squash
candidates** section shows its scope, limits, omissions and uncertainty outside
filters. **Observed candidates** lists 20 entries per page; **All inspected
groups** includes excluded and unclassified windows. Search covers embedded group
records and evidence. Select a row to inspect ordered members, both resolved
endpoint pairs, scores, normalized agreement and ambiguity. Member buttons jump
to individual inspection. Tab/Enter operate all controls. Only one evidence panel
is mounted across both individual and group views (at most 32 evidence lines).
The HTML embeds the same JSON and uses the existing CSP and text-only rendering.

## Existing work and limitations

This is a bounded review extension, not a claim of algorithmic novelty.
[Git range-diff](https://git-scm.com/docs/git-range-diff) compares patch series
using heuristic commit pairing and documents its human-oriented output.
[Git patch-id](https://git-scm.com/docs/git-patch-id) supplies normalized patch
identifiers, including its stable whitespace/order behavior. These primary
manuals were reviewed for this implementation. Atlas composes endpoint diffs
and retains overlapping candidates independently instead of forcing a pairing.
Neither Git's pairing nor this tool proves historical provenance or behavior.

No private repositories, hosted inference, GPUs or paid data are required.
Validation uses synthetic local Git histories. See [group validation](groups-validation.md)
for independent oracles, negative cases, measured bounds and remaining limitations.

The group search uses a bounded per-comparison blob cache. It preserves logical
inspection-byte accounting and the existing search limits. See
[measured blob reuse](group-latency.md) for its bounds, frozen-baseline comparison,
reproduction commands and performance tradeoffs.
