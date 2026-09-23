# JSON report schema, version 1

Use `summary --json` or `compare LEFT RIGHT --json`. Successful stdout is one JSON
object, with no terminal escapes or prose outside it. Errors exit 2, leave stdout
empty, and explain the failure on stderr. Consumers should check the exit status
and `schema_version` before reading a report. New keys may be added to v1;
incompatible field meanings or types require a new schema version.

Both commands include:

| Key | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | `1` |
| `command` | string | `summary` or `compare` |
| `history` | object | `complete` and `shallow` booleans |
| `warnings` | string array | Completeness advisories, empty when none |
| `semantics` | string | Human explanation of ancestry vs patch/merge semantics |

`history.complete` concerns reachable commit ancestry needed by this report, not
all objects in the repository. It is false for shallow summaries. Missing commit
objects cause an error, not a successful incomplete comparison. Replacement refs
are ignored and graft files rejected, as described in the README.

## Summary

| Key | Type | Meaning |
| --- | --- | --- |
| `bare` | boolean | Whether the inspected repository context is bare |
| `head` | object | `state` (`attached`, `detached`, `unborn`), full `ref` or null, full `oid` or null |
| `branches` | object array | All local branches, ordered by full ref name |
| `worktrees` | object array | Git's worktree enumeration order; do not rely on ordering |

Each branch has `name`, full `ref`, full commit `oid`, `current` boolean,
`upstream`, integer-or-null `ahead` and `behind`, and `worktrees` (array of path
strings). A branch's `current` means HEAD in the inspected repository context,
not that it is checked out in any worktree.

`upstream` has `state` (`none`, `missing`, `unmapped`, `present`), full tracking
`ref` or null, commit `oid` or null, configured `remote` or null (`.` means the
local repository), and `merge_refs` (array of configured full refs).
`ahead` counts commits reachable from the local tip but not its upstream;
`behind` is the reverse. Null counts mean unavailable, not zero.

Each worktree has:

- `path`: absolute path string, including missing/prunable locations.
- `oid`, `branch`: Git's administrative HEAD and full branch ref, or null.
  An unborn worktree may have an all-zero administrative `oid`; `head.oid` is null.
- `bare`, `detached`, `locked`, `prunable`: independent booleans.
- `lock_reason`, `prune_reason`: null if the corresponding flag is absent,
  possibly empty strings if present without a reason.

## Compare

Each of `left` and `right` has:

| Key | Type | Meaning |
| --- | --- | --- |
| `input` | string | User-supplied revision expression |
| `oid` | string | Fully resolved commit object ID |
| `unique_count` | integer | Total commits reachable only from this side |
| `commits` | object array | At most `limit_per_side` entries, each with full `oid` and `subject` |
| `truncated` | boolean | Whether some unique commits were omitted from the array |

Unique lists use Git's topological order; equal-time ties are not a portable sort
contract. They contain every unique commit when `truncated` is false.
`merge_bases` is a sorted array of **all** full best-common-ancestor IDs.
`unrelated` is true exactly when this array is empty. `limit_per_side` is a positive
integer. Identical tips yield zero unique counts and their own ID as the single
merge base. Ancestor/descendant comparisons yield zero on the ancestor side.
SHA-256 repositories use 64-character IDs; do not assume IDs are always 40 characters.

The `patches` command has a separate [schema-v1 contract](patches-v1.md), identified
by `kind: "patches"`, with explicit incomplete and excluded classifications.
