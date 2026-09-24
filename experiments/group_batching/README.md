# Diff/patch-ID batching experiment

`baseline/` freezes the cataloged blob-reuse implementation before batching.
`baseline.json` identifies its catalog commit/tree and hashes every Python module.
The older pre-reuse baseline is preserved separately in `../group_latency`.

The shared fixture, profiler, balanced-pair runner and seeded differential oracle
accept an explicit baseline directory. Reproduction commands, transport bounds,
primary references, tradeoffs and results are in
[the batching documentation](../../docs/group-batching.md).

The experiment keeps the original 12-commit, complete 500-commit (20/480), and
comparison-limited 500-commit (250/250) fixtures and limits. The performance target
is a further 1.5× ratio of baseline to candidate median elapsed time on the
complete 500-commit case, with identical output and searched scope. Correctness
requires identical full reports on 200 seeded histories and their limited
variants, independent endpoint checks and unchanged repository snapshots, as well
as regressions, offline browser and installed-CLI validation. All measurements,
including regressions and missed targets, are retained.
