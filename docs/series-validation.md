# Series validation

The checks below generate disposable local repositories; no personal repository,
paid service, model inference, or network dataset is involved.

```
python3 -m unittest discover -s tests -v
python3 scripts/verify_install.py
python3 scripts/compare_range_diff.py
python3 scripts/benchmark_series.py --commits 250 --repeat 3
python3 scripts/check_publication.py
git diff --check
```

The installer alone downloads pinned, hash-verified build wheels unless supplied
`--wheelhouse PATH`. Its installed application scenarios run with Git transports
and proxy access disabled. They include the README's real before/after rebase review,
with one exact group and an edited candidate scoring 7333/10000.
[Installation evidence](../results/series-installation.json) records that check.

## Independent checks

[Tests](../tests/test_series.py) generate 100 histories with seeds 0–99. Each pair
has a changed base, shuffled commit order, unchanged patches, edited lines, repeated
lines, whitespace variants and unrelated changes. Range membership/order is checked
against direct `rev-list`, and every inspected patch ID against `diff-tree` piped
to `patch-id --stable`. A list-matching reference scorer uses exact rational
arithmetic independently of the production Counter-based scorer. An additional
300 seeded feature-pair cases check signs, repeated lines, whitespace and paths.
Seeds specify topology/content, not commit OIDs (test timestamps are not fixed).

Targeted tests cover rebase, message-only edits, unrelated histories, duplicate
revert/reapply groups, merge ordering, root changes, empty commits, NUL-containing
blobs, executable mode transitions, symlinks, submodules, renamed paths, no final
newline, and context-only patch differences yielding a **heuristic** score of 10000.
They also cover SHA-256, bare repositories, annotated tags, invalid/ambiguous input,
seven-way ties with five displayed candidates, threshold boundaries, normalized
evidence truncation, shallow/missing objects, disabled promisor transport, byte,
commit, feature, comparison, runtime and output limits. Tests compare repository
file hashes and modification times around review with dirty/staged files and hostile
diff/textconv configuration; no helper execution or repository changes are expected.
The original regressions also exercise an actual sleeping subprocess deadline.

[Full test output](../results/tests.log) is the actual test run, not an accuracy claim.
The older patch comparison's separate 100 seeded histories remain in the full suite.

## Comparison with Git range-diff

[Reproducible script](../scripts/compare_range_diff.py) fixes content, messages,
parents and timestamp; [recorded output](../results/series-range-diff.json) contains
both tools' actual observations on Git 2.43.0. Git's heuristic is **not ground truth**.

| Fixture | Atlas | Git range-diff observation |
| --- | --- | --- |
| Unchanged rebase | One exact group | One `=` pair |
| Message-only edit | One exact group, message ignored | One `!` pair with message difference |
| Edited added line | Candidate 7333 | One `!` pair with patch difference |
| Unrelated patch | No candidate | Separate deletion/addition |
| Identical lines, different path/message | Candidate 8000 | One `!` pair |
| Revert and reapply | Both additions retained in one group | Chooses the reapply for one pair; original and revert displayed as deletions |
| Reordered independent commits | Two exact groups | Two `=` pairs in new-series order |

The message-only and duplicate outcomes differ by design. The different-path
fixture is a useful negative result: the scorer gives a high score to content in
an independently chosen file. Similarity is therefore not evidence of provenance
or semantic identity. Neither these fixtures nor the generated histories estimate
real-world precision/recall, and no optimal threshold is claimed.

## Workload measurements

[Benchmark script](../scripts/benchmark_series.py) uses a deterministic fast-import
stream with 250 commits per side. Each adds an 80-line file. Half the patches are
exact; half edit one line, producing candidate scores of 9900. The complete case
performs 15,625 comparisons after removing 125 exact groups. Each case runs three
times and checks its expected completeness, groups, scores and absence policy.
[Recorded measurements](../results/series-benchmark.json) include elapsed time,
GNU time peak RSS, actual stdout/stderr bytes, comparison count and inspection bytes.
Commit-, byte-, and comparison-exhausted workloads are included.

Recorded three-run medians and maximum individual-process RSS:

| Case | Median seconds | Maximum RSS KiB | JSON bytes | Comparisons |
| --- | ---: | ---: | ---: | ---: |
| Complete | 11.548 | 20,196 | 488,168 | 15,625 |
| Commit limit | 5.382 | 18,176 | 246,177 | 3,969 |
| Byte limit | 0.247 | 17,152 | 119,986 | 0 |
| Comparison limit | 10.701 | 18,048 | 207,462 | 1 |

The comparison limit caps scoring work, not the preceding patch inspection, which
explains its runtime being close to the complete case. Output sizes were identical
across repetitions for each case.

These are local synthetic warm-cache runs, not controlled cold-cache experiments
or performance claims about real repositories. System load is not controlled.
GNU time reports maximum RSS for the CLI or an individual child, not the sum of
concurrent process memory. Large repositories may time out during ancestry
validation before patch inspection. The command's processing deadline is checked
cooperatively in Python, and bounded evidence/rendering work may finish afterward.
Linux/Python 3.12.3/Git 2.43.0 were used; macOS and Windows were not validated here.
