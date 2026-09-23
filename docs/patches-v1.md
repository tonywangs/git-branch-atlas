# Patch comparison, schema v1

`git-branch-atlas patches LEFT RIGHT [--json]` resolves both inputs once to full
commit IDs. It compares `reachable(LEFT) - reachable(RIGHT)` with the opposite
set. Shared ancestors never enter either patch set. Merge commits are excluded,
but their unique nonmerge ancestors are still considered. Unrelated roots work.
Inputs may be full refs, commit IDs or Git revision expressions; ambiguous names
are errors. No remote access or repository writes are needed.

## Interpretation

The implementation feeds deterministic diffs into **`git patch-id --stable`**.
This is a wrapper around existing Git machinery, not a new equivalence algorithm.
Git ignores whitespace and hunk line numbers and makes the stable sum independent
of file-diff ordering. Different whitespace can change program behavior, so a
match does **not** prove semantic equivalence. Hashes also have collision risk.
The output does not establish current branch contents, safe cherry-picking, or
conflict-free merging. A patch can match even after one branch reverted it.
Reverting and reapplying can put two commits on one side in the same group as one
commit on the other. Atlas retains the entire group; it never invents pairings.

An unmatched commit has a successfully computed patch ID absent from all
**supported** candidates in the opposite ancestry-exclusive set, after a complete
search. It does not mean the change is absent from the opposite tree, absent from
shared ancestry, or impossible to produce with a different patch decomposition.
Excluded changes are outside this definition. One commit mixing supported and
excluded file changes is excluded in its entirety.

## Deterministic supported scope

- Compare each single-parent commit against its parent. Compare a root against
  the empty tree via `diff-tree --root`. Empty commits are excluded.
- Regular text files with modes `100644` or `100755` are supported. A NUL byte
  **anywhere in either version of a changed blob** marks the entire commit binary
  and excluded. This explicit heuristic differs from Git's usual prefix scan;
  non-NUL data is treated as text regardless of filename or attributes.
- Executable-bit transitions, symlinks, submodules, and other nonregular modes are
  excluded. Addition/deletion of a regular executable file is supported.
- Rename/copy detection is disabled: renames are deletion plus addition. Paths
  participate in patch IDs; this does not detect moves of equivalent code.
- Myers diff, no indent heuristic, three context lines, zero inter-hunk context,
  full blob IDs, `a/` and `b/` prefixes, standard output indicators, quoted paths,
  no relative paths, and no color. Whitespace is preserved in the diff, then
  normalized by Git's stable patch-ID algorithm. Context differences can prevent
  matches even when the inserted/deleted lines look similar.
- External diff and textconv are disabled. Forced text output bypasses attribute
  binary suppression. Global/system Git configuration and attributes are ignored;
  relevant repository diff settings are overridden. Attribute function headings
  may appear in the generated diff, but patch-id ignores hunk headings. Git's
  object-format-specific IDs are preserved; do not compare IDs across repository
  object formats or different diff-setting profiles.
- Replacement refs are ignored and nonempty legacy grafts are rejected. All
  transport protocols, lazy downloads, hooks, and interactive prompts are disabled.

## Bounds and completeness

Defaults: 30 sampled commits **per side**, 32 MiB inspection bytes, 30 seconds
across all Git operations, and 2 MiB final output. Override with `--max-count`,
`--max-diff-bytes`, `--timeout`, and `--max-output-bytes`. Hard accepted maxima are
10,000 commits per side, 256 MiB inspection, 3,600 seconds, and 16 MiB output.
Output limit minimum is 1,024 bytes. Limits apply to patches only; graph/summary/
ancestry compare retain their existing behavior.

Commit traversal uses topology order and samples at most N+1 entries per side to
detect truncation. Shared ancestry validation may walk more than N commits and
is bounded by the same runtime deadline. Metadata stdout is capped at 1 MiB per
process (8 MiB for candidate enumeration); stderr at 64 KiB. Inspection bytes are
a **cumulative** budget for raw diffs, full changed-blob scans and generated patches,
including repeated objects. Reading one byte beyond the budget detects exhaustion;
`inspection_bytes_read` can therefore be limit+1. No more blob or diff commands
are launched after this budget is exhausted. Streams are read incrementally; each
process is killed and reaped on exhaustion or deadline. Bounded inputs use an
anonymous temporary file outside the inspected repository. The implementation
bounds its captured payload, not Git's internal memory allocation or filesystem
latency. Runtime enforcement is best effort under OS scheduling.

Limit exhaustion, missing required objects, or shallow history make `complete`
false. Unmatched arrays are then empty **on both sides**. Computed patches with
no observed match move to `unclassified` with a reason. Observed matching groups
can still be reported, but may be missing additional members. Shallow membership
is unreliable, so all sampled shallow commits are unclassified and no IDs are
computed. Unseen commits cannot be listed individually: `enumeration_complete`
false warns that the sampled partition is not the whole set. A failing enumeration
discards its partial stdout. Total unique counts are deliberately not estimated.

Exit status 0: complete report within the supported scope (possibly with excluded
commits). Status 1: valid incomplete report. Status 2: invalid input, failure before
identities can be established, or output too large; no report goes to stdout.
An output overflow produces a short stderr error, never a silently truncated JSON
document. Increase the output cap to retrieve the report. Application startup and
final bounded formatting sit outside the Git runtime deadline. Inspection is an
observation of immutable IDs, not an atomic snapshot; use a quiescent repository.

The bounded pipe implementation currently targets POSIX systems; it has been
validated on Linux. Windows pipe-selector compatibility is not claimed.

## JSON fields

`schema_version: 1`, `kind: "patches"`, `algorithm: "git-patch-id--stable"` identify
this format. `scope`, `notice`, `limits`, `warnings`, `complete`, and
`inspection_bytes_read` describe the interpretation and execution.

Each of `left` and `right` contains:

| Field | Meaning |
| --- | --- |
| `revision`, `oid` | Original requested string, resolved full commit ID |
| `enumeration_complete` | Entire unique commit set was enumerated reliably |
| `sampled_count` | Number of retained unique commits, including merges |
| `unmatched` | Objects with `oid` and `patch_id`; definitive only within supported scope |
| `excluded` | Objects with `oid` and `reason`; known outside supported scope |
| `unclassified` | Objects with `oid`, `reason`, and optional computed `patch_id` |

`matches` is an array of `{patch_id, left: [oid, ...], right: [oid, ...]}`.
Groups are ordered by patch ID; group members are sorted by full commit ID.
Every sampled commit occurs once, either as a matching-group member or in one of
its side's three arrays. Revisions are escaped in terminal output and JSON uses
ASCII escapes, including surrogate escapes for POSIX byte names. Field order is
not a compatibility promise. Consumers should ignore future additional fields.

## Related tools

[git patch-id](https://git-scm.com/docs/git-patch-id) defines the stable hash and
whitespace normalization. [git cherry](https://git-scm.com/docs/git-cherry)
classifies copied commits with `+`/`-`; the tests compare it on compatible text
fixtures. [git log --cherry-mark](https://git-scm.com/docs/git-log) combines
patch-equivalence markers and revision walks.
[git range-diff](https://git-scm.com/docs/git-range-diff) compares patch series and
can show how paired patches changed; Atlas does not implement its similarity
matching. [git diff-tree](https://git-scm.com/docs/git-diff-tree) supplies the
per-commit diffs and root handling. Atlas adds explicit groups, exclusions,
resource bounds and incomplete-search reporting to these existing capabilities.
