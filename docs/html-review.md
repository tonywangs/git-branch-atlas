# Offline patch-series HTML review

Install with Python 3.10+ and local Git (`pip install .`), or use
`python3 -m git_branch_atlas` from the checkout. The installed application has no
Python dependencies. Building a wheel requires setuptools; prepare build tools
before going offline. POSIX is required by the existing Git inspection reader.

Save the old base and tip **before** rebasing. For example, in your own repository:

```sh
git branch review-old-base main
git branch review-before topic
# Advance main, then rebase topic as appropriate for your repository.
git rebase --onto main review-old-base topic
git-branch-atlas series review-old-base review-before main topic --html > review.html
```

Open `review.html` directly in a browser (`file://`). No server, account, fonts,
CDN or network access is used. Generation is read-only; the branch/rebase commands
above are actions you perform to prepare your own example. The HTML exit status
is 0 for a complete supported search, 1 for a usable incomplete report, or 2 for
invalid input/output overflow. Shell redirection can leave an empty file on error;
check the exit status before replacing a previous report. `--html` is available
only for `series` and is mutually exclusive with `--json`. All other commands and
the series-v1 JSON schema remain unchanged.

## Reading the report

The header shows the exact resolved endpoint IDs and enumeration completeness.
Commits stay in the order supplied by the comparison engine. Status, side, group
and text filters combine; search covers embedded commit fields including IDs,
reasons, paths and normalized evidence. Series-v1 does not collect commit messages,
author identities or full patches. Search therefore cannot find message subjects
or omitted evidence. Control and bidi formatting characters are displayed as
literal `\uXXXX` sequences, which can also be searched.

Select a commit to inspect its parents, patch ID, exclusions and search status.
An exact group retains every observed member on both sides. **Browse all group
members** resets other filters and shows that group's ordered commits. Duplicate
groups never imply unique pairing. Candidate ranks, integer scores, ambiguity,
top ties and omitted-candidate counts are copied from the report; the browser
never computes correspondence. Scores are not probabilities. Even an isolated
best candidate is a heuristic, not proof of semantic equivalence.

**Inspect bounded evidence** opens the normalized feature difference for a
candidate. Only one evidence panel is mounted at a time. Source always means the
selected commit, including on the right side. Each category shows sampled items,
counts, original byte lengths, truncation and omitted-key counts. This is neither
a full diff nor an applicable patch. **Go to counterpart** selects the other
commit and clears filters. Previous/next commit follows left series order, then
right series order, and also clears filters. Selecting filters does not clear the
inspection pane; its side and OID remain explicit.

Use Tab/Shift+Tab between controls, Enter/Space on buttons, and Enter/Space to
expand evidence. Native selects support arrow keys. Selection moves focus to the
inspection pane; page navigation moves focus to the commit-list heading. The
page includes a skip link, visible focus outlines, labeled controls, status
announcements and textual statuses independent of color. Warning, uncertainty
and omission notices remain outside the filtered list, including when no commits
match. They can be reached by scrolling to the header.

## Bounds and confidentiality

`--max-output-bytes` bounds the entire UTF-8 HTML file including the trailing
newline, with the existing default of 2 MiB and accepted range 1 KiB–16 MiB.
Markup, CSS, scripts and escaped JSON all count. The complete artifact is checked
before printing; overflow produces exit 2 and no partial stdout. No evidence is
silently dropped to fit. Embedded data is exactly the generated comparison JSON,
not a second comparison. JSON escapes `<`, `>` and `&` before embedding; DOM text
uses `textContent`. A hash-based content security policy permits only the bundled
script/style and blocks other resources. This is defense in depth, not encryption.

Commit pages contain at most **100 cards**, plus one selected inspection pane.
One expanded candidate contains at most **32 evidence item lines** (four categories
of eight). Closing/replacing a panel removes its contents from the DOM. These
bounds are below the milestone ceilings of 200 cards and 2,000 evidence lines.
The report still holds all bounded data in memory; this is DOM pagination, not
streaming parsing. Existing limits permit at most 1,000 commits per side and
five retained candidates per commit. An evidence item is at most 160 source bytes;
wrapping may produce several visual lines. Empty reports are supported. No
browser-side import or arbitrary-schema HTML-rendering API is supported.

The file embeds repository endpoint labels and IDs, paths and patch-derived
text, including data hidden by filters or collapsed evidence. Treat the entire
file as confidential repository content. Filtering is not redaction. Review it
before sharing. No telemetry is included.

## Reproducing validation

The application needs no browser automation packages. The development playthrough
uses Node and Playwright 1.58.2 with its Chromium build; install these outside the
checkout, then run:

```sh
npm install --prefix /tmp/atlas-browser --cache /tmp/atlas-npm-cache playwright@1.58.2
PLAYWRIGHT_BROWSERS_PATH=/tmp/atlas-browser-binaries /tmp/atlas-browser/node_modules/.bin/playwright install chromium
export NODE_PATH=/tmp/atlas-browser/node_modules
export PLAYWRIGHT_BROWSERS_PATH=/tmp/atlas-browser-binaries
python3 -m unittest discover -s tests -v
python3 scripts/verify_html.py
python3 scripts/benchmark_html.py --repeat 3
python3 scripts/verify_install.py
python3 scripts/check_publication.py
```

Browser/tool installation requires network access; the playthrough itself blocks
networking and opens temporary files. Installation verification bootstraps pinned,
hash-checked build wheels before restricting the example to offline Git protocols
and dead HTTP proxies; `--wheelhouse PATH` uses prepared wheels without downloading.
The isolated installed console verifies the before/after rebase HTML embeds exactly
the same JSON as its installed JSON command. Browser parity uses real Git fixtures
for reordered/rebased patches, duplicates, competing edits, excluded binary changes,
empty ranges and exhausted comparison/byte/commit limits. Additional hostile
metadata is injected solely to test serializer defense. Tests snapshot fixture
repository files and modification times before and after report generation.

Results are recorded in [html-browser.json](../results/html-browser.json),
[html-installation.json](../results/html-installation.json),
[html-benchmark.json](../results/html-benchmark.json) and
[tests.log](../results/tests.log). The benchmark records every repeated runtime,
GNU time peak RSS, artifact size, Chromium load time and observed DOM counts for
12 and 500 total commits. Fixtures and deterministic timestamps are in the script.
Measurements are warm-cache synthetic observations, not performance guarantees;
GNU time RSS is the maximum individual process, not summed concurrent memory.
Browser load includes automation/instrumentation overhead. The browser driver
checks all small-fixture statuses/groups/candidates/evidence and paginates all
large-fixture commits, sampling two large-fixture candidate inspections.

Validated browser: Chromium on Linux. Firefox, Safari, Windows/macOS file handling,
screen readers, forced colors and a formal accessibility audit remain untested.
The existing heuristic/exclusion and processing-deadline limitations described in
[series-v1](series-v1.md) still apply. No split/squash inference is added.

## Recorded measurements

On Linux/Python 3.12.3/Git 2.43.0 with Chromium 145.0.7632.6, three
repetitions of each deterministic workload produced:

| Total commits | Generation seconds (median; range) | Maximum RSS KiB | HTML bytes | Browser load ms (median) | Observed peak DOM nodes / cards |
| --- | --- | ---: | ---: | ---: | --- |
| 12 | 0.456; 0.455–0.576 | 19,840 | 20,355 | 354 | 134 / 12 |
| 500 | 23.740; 10.757–28.844 | 22,916 | 273,269 | 249 | 520 / 100 |

The large timing variation is retained in the raw results. These runs were not
isolated from other validation activity and do not establish a speedup or a
performance guarantee. Browser measurements include a DOM-count observer.
The workloads exercised two evidence lines at once; the separate bounded-evidence
fixture exercised 18 lines and verified omitted candidates, omitted evidence keys
and byte truncation. The implementation bound is 32 evidence item lines, not a
claim that the fixtures observed every possible maximum. All 40 Python tests,
the seven offline browser fixtures, the six benchmark browser loads and isolated
installation checks passed. Browser checks compare every small-fixture candidate
and its evidence; large workloads sample candidate inspection as noted above.

## Existing work

This is a local presentation layer for the existing comparison engine, not a
novel correspondence algorithm. [Git range-diff](https://git-scm.com/docs/git-range-diff)
provides a human-oriented review of two patch series using heuristic paired
correspondence. Atlas retains independent candidates and duplicate patch-ID groups.
The browser defense follows the documented
[CSP hash-source mechanism](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy)
and uses native HTML controls. These primary references were reviewed during
implementation; they are documentation links, not report runtime dependencies.
