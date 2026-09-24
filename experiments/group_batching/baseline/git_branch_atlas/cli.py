"""Command-line interface for Git Branch Atlas."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .git import GitError, run_git, safe, repository_root
from .patches import patch_compare, format_patch_report
from .series import series_compare, format_series_report
from .html_report import format_series_html
from .reports import compare, history_state, render, resolve, summary, validate_history


@dataclass(frozen=True)
class Branch:
    name: str
    short_hash: str
    subject: str
    timestamp: int


class AtlasParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        super().error(safe(message))


def current_head(repo: Path) -> tuple[str, bool]:
    symbolic = run_git(repo, ["symbolic-ref", "--quiet", "HEAD"], check=False)
    if symbolic.returncode == 0:
        return symbolic.stdout.strip().removeprefix("refs/heads/"), False
    oid = run_git(repo, ["rev-parse", "--short", "HEAD"], check=False)
    return (oid.stdout.strip() or "unborn"), True


def branches(repo: Path) -> list[Branch]:
    fmt = "%(refname)%00%(objectname:short)%00%(committerdate:unix)%00%(subject)"
    result = run_git(repo, ["for-each-ref", f"--format={fmt}", "refs/heads"])
    found: list[Branch] = []
    for line in result.stdout.split("\n"):
        fields = line.split("\0", 3)
        if len(fields) == 4:
            found.append(Branch(fields[0].removeprefix("refs/heads/"), fields[1], fields[3], int(fields[2])))
    return sorted(found, key=lambda item: (-item.timestamp, item.name))


def choose_base(repo: Path, requested: str | None, head: str, detached: bool) -> str | None:
    if requested:
        return resolve(repo, requested)
    if not detached:
        upstream = run_git(repo, ["rev-parse", "--symbolic-full-name", "@{upstream}"], check=False)
        if upstream.returncode == 0:
            return upstream.stdout.strip()
    names = {branch.name for branch in branches(repo)}
    for candidate in ("main", "master"):
        if candidate in names:
            return "refs/heads/" + candidate
    return None if detached else "refs/heads/" + head


def ahead_behind(repo: Path, base: str, branch: str) -> tuple[int, int]:
    a = resolve(repo, base)
    b = resolve(repo, branch)
    result = run_git(repo, ["rev-list", "--left-right", "--count", f"{a}...{b}", "--"])
    behind, ahead = (int(value) for value in result.stdout.split())
    return ahead, behind


def paint(text: str, code: str, color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if color else text


def render_overview(repo: Path, color: bool, requested_base: str | None) -> str:
    head, detached = current_head(repo)
    local = branches(repo)
    base = choose_base(repo, requested_base, head, detached) if local or requested_base else None
    title = paint("Branch overview", "1;36", color)
    identity = f"detached at {head}" if detached else head
    lines = [f"{title}  HEAD: {paint(safe(identity), '1;32', color)}"]
    if not local:
        lines.append("  (empty repository: no commits or local branches)")
        return "\n".join(lines)
    if base:
        lines[0] += f"  base: {paint(safe(base), '1;33', color)}"
    name_width = max(len(safe(item.name)) for item in local)
    for item in local:
        marker = "*" if not detached and item.name == head else " "
        counts = ""
        if base:
            ahead, behind = ahead_behind(repo, base, "refs/heads/" + item.name)
            counts = f"  +{ahead}/-{behind}"
        lines.append(f"{marker} {safe(item.name):<{name_width}}  {item.short_hash}{counts}  {safe(item.subject)}")
    return "\n".join(lines)


def render_graph(repo: Path, color: bool, all_refs: bool, max_count: int) -> str:
    exists = run_git(repo, ["rev-parse", "--verify", "HEAD"], check=False)
    if exists.returncode and not (all_refs and run_git(repo, ["for-each-ref", "--count=1"]).stdout):
        return "\nCommit graph\n  (no commits yet)"
    pretty = "format:%h %s %d"
    args = ["log", "--no-notes", "--graph", "--decorate=short", f"--max-count={max_count}", f"--pretty={pretty}"]
    if all_refs:
        args.append("--all")
    args.append("--color=never")
    graph = "\n".join(safe(line, backslashes=False) for line in run_git(repo, args).stdout.rstrip().split("\n"))
    return f"\n{paint('Commit graph', '1;36', color)}\n{graph}"


def parser() -> argparse.ArgumentParser:
    result = AtlasParser(
        prog="git-branch-atlas",
        description="Visualize Git branches and commit history at a glance.",
    )
    result.add_argument("command", nargs="?", default="graph", choices=("graph", "summary", "compare", "patches", "series"))
    result.add_argument("revisions", nargs="*", metavar="REV")
    result.add_argument("--json", action="store_true", help="versioned JSON for summary, compare, patches or series")
    result.add_argument("--html", action="store_true", help="series: self-contained interactive HTML on stdout")
    result.add_argument("--repo", default=".", metavar="PATH", help="repository or path inside it (default: current directory)")
    result.add_argument("--all", action="store_true", help="show commits from all refs, not only HEAD history")
    result.add_argument("--max-count", type=int, default=30, metavar="N", help="maximum graph commits or commits per comparison side (default: 30)")
    result.add_argument("--max-diff-bytes", type=int, default=32 * 1024 * 1024,
                        help="patches/series: total streamed raw diff, blob and patch bytes (default: 32 MiB)")
    result.add_argument("--timeout", type=float, default=30,
                        help="patches/series: elapsed inspection budget in seconds (default: 30)")
    result.add_argument("--max-output-bytes", type=int, default=2 * 1024 * 1024,
                        help="patches/series: maximum report bytes (default: 2 MiB)")
    result.add_argument("--max-comparisons", type=int, default=10000, help="series: candidate pair limit (default: 10000)")
    result.add_argument("--threshold", type=int, default=5000, help="series: minimum score in basis points (default: 5000)")
    result.add_argument("--groups", action="store_true", help="series: opt into schema 2 with singleton versus 2..4 commit endpoint candidates")
    result.add_argument("--max-groups", type=int, help="--groups: total windows inspected (default: 600; maximum 6000)")
    result.add_argument("--max-group-comparisons", type=int, help="--groups: additional comparison limit (default: 20000)")
    result.add_argument("--max-group-candidates", type=int, help="--groups: exported candidate limit (default: 500; maximum 2000)")
    result.add_argument("--base", metavar="REV", help="revision used for branch ahead/behind counts")
    result.add_argument("--no-color", action="store_true", help="disable colored output")
    result.add_argument("--version", action="version", version="%(prog)s 0.2.0")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_intermixed_args(argv)
    if not args.groups and any(value is not None for value in (args.max_groups, args.max_group_comparisons, args.max_group_candidates)):
        parser().error("group limits require --groups")
    if args.groups and args.command != "series":
        parser().error("--groups requires series")
    if args.html and (args.command != "series" or args.json):
        parser().error("--html requires series and cannot be combined with --json")
    if args.max_count < 1:
        parser().error("--max-count must be at least 1")
    if args.command in ("compare", "patches") and len(args.revisions) != 2:
        parser().error("compare/patches requires exactly two revisions")
    if args.command == "series" and len(args.revisions) != 4:
        parser().error("series requires LEFT_BASE LEFT_TIP RIGHT_BASE RIGHT_TIP")
    if args.command not in ("compare", "patches", "series") and args.revisions:
        parser().error("revisions are only accepted by compare, patches or series")
    if args.command == "graph" and args.json:
        parser().error("--json requires summary, compare, patches or series")
    if args.command != "graph" and (args.base or args.all):
        parser().error("--base and --all apply only to graph")
    color = not args.no_color and sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    try:
        if args.command == "series":
            report = series_compare(Path(args.repo).expanduser().absolute(), *args.revisions,
                                    args.max_count, args.max_diff_bytes, args.timeout,
                                    args.max_comparisons, args.threshold, include_groups=args.groups,
                                    max_groups=600 if args.max_groups is None else args.max_groups,
                                    max_group_comparisons=20000 if args.max_group_comparisons is None else args.max_group_comparisons,
                                    max_group_candidates=500 if args.max_group_candidates is None else args.max_group_candidates)
            print(format_series_html(report, args.max_output_bytes) if args.html else
                  format_series_report(report, args.json, args.max_output_bytes))
            return 0 if report["complete"] else 1
        if args.command == "patches":
            report = patch_compare(Path(args.repo).expanduser().absolute(), *args.revisions,
                                   args.max_count, args.max_diff_bytes, args.timeout)
            print(format_patch_report(report, args.json, args.max_output_bytes))
            return 0 if report['complete'] else 1
        root = repository_root(Path(args.repo).expanduser())
        if args.command == "summary":
            report = summary(root)
            output = json.dumps(report, ensure_ascii=True, indent=2) if args.json else render(report)
        elif args.command == "compare":
            report = compare(root, *args.revisions, args.max_count)
            output = json.dumps(report, ensure_ascii=True, indent=2) if args.json else render(report)
        else:
            shallow, _ = history_state(root)
            if shallow:
                raise GitError("Shallow history: use summary for metadata; obtain full history separately for graph counts")
            refs = run_git(root, ["for-each-ref", "--format=%(objectname)", "refs/heads/"]).stdout.split()
            head = run_git(root, ["rev-parse", "--verify", "HEAD^{commit}"], check=False)
            if head.returncode == 0:
                refs.append(head.stdout.strip())
            validate_history(root, ["--all"] if args.all else refs)
            output = render_overview(root, color, args.base) + render_graph(root, color, args.all, args.max_count)
        print(output)
    except (GitError, OSError) as exc:
        print(f"git-branch-atlas: error: {safe(str(exc))}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
