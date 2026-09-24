# Split/squash latency experiment

`baseline/git_branch_atlas/` is the unchanged application source from catalog
Git tree `2ca8e13f793267ea5fc9b20ad3704d3598af93ec` (remote commit
`dd0df29b3c8d5722c3993f3879d111e2b8c251d1`). It is retained as a reproducible
comparison implementation, not imported by the installed application. Source
SHA-256 hashes accompany each profile and paired measurement.

Run `python3 scripts/measure_group_latency.py --profile-only` to profile the
frozen baseline, and `python3 scripts/measure_group_latency.py --repeat 6` to
compare it with the current application. All fixtures are generated with fixed
commit timestamps via Git fast-import in temporary repositories. Exact worker
commands, endpoint IDs, options, output hashes and per-stage measurements are
recorded in the resulting JSON. Temporary paths in those commands are historical;
the driver regenerates equivalent repositories on each run.

The 12-commit workload uses 6 commits per side. A complete 500-commit workload
uses 20 on the left and 480 on the right: every structural window and singleton
comparison fits the existing 100,000-comparison ceiling. The balanced 250/250
case retains all windows but deliberately exhausts 1,000 group comparisons.
There is no claim that the balanced case completes its candidate search.

Pairs alternate baseline/current and current/baseline order; six pairs balance
both orders. No warmup measurements are discarded. The driver asserts identical
serialized JSON hashes, complete window inspection, and the intended completion
status. Instrumentation is identical for both implementations. Per-stage times
include nested calls and should not all be added; individual/group totals partition
the computation, and serialization is separate. GNU time captures maximum RSS
of an individual process, not aggregate simultaneous memory.

`python3 scripts/verify_group_reuse.py` checks 200 deterministic seeded histories
against the frozen source, including complete reports and exhausted limits, then
independently verifies each eligible endpoint patch with `git diff` and
`git patch-id --stable`. The original exhaustive-window and independent-score
oracles remain in `tests/test_groups.py`.
