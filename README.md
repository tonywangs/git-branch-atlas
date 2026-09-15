# Git Branch Atlas

Git Branch Atlas is Tony Wang's small, read-only terminal map of a repository. It
combines a concise branch overview with Git's familiar, reliable commit graph.
It needs Python 3.10+ and the `git` executable, with no third-party runtime
packages.

## Install

From this checkout:

```console
python -m pip install .
git-branch-atlas --help
```

For development, it can also run without installation:

```console
python -m git_branch_atlas
```

## Use

```console
# Current branch history and an overview of every local branch
git-branch-atlas

# Inspect another repository (paths containing spaces are supported)
git-branch-atlas --repo "../my project"

# Include history reachable from every ref, limited to 15 commits
git-branch-atlas --all --max-count 15

# Compare local branches with a particular base
git-branch-atlas --base origin/main --no-color
```

The overview shows each local branch's latest hash and subject. `+A/-B` means
the branch is *A* commits ahead of and *B* commits behind the selected base. The
base defaults to the current branch's upstream, then `main` or `master`, then
the current branch. The `*` marks the attached HEAD; detached HEADs are stated
explicitly. Tags, branch names, HEAD, merges, subjects, and abbreviated hashes
appear in the graph decorations produced by Git itself.

Example (plain output abbreviated here):

```text
Branch overview  HEAD: feature  base: main
* feature  a13c9e1  +1/-0  Add atlas legend
  main     91be720  +0/-0  Initial import

Commit graph
* a13c9e1 Add atlas legend  (HEAD -> feature)
* 91be720 Initial import  (main)
```

Color is enabled only for a terminal, and is automatically disabled when
piping output. `--no-color` (or the conventional `NO_COLOR` environment
variable) disables it explicitly. Atlas disables paging, terminal prompts,
optional Git locks, and repository hooks while it performs read-only queries.

## Test

```console
python -m unittest discover -s tests -v
```
