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
    result.add_argument("command", nargs="?", default="graph", choices=("graph", "summary", "compare"))
    result.add_argument("revisions", nargs="*", metavar="REV")
    result.add_argument("--json", action="store_true", help="versioned JSON for summary or compare")
    result.add_argument("--repo", default=".", metavar="PATH", help="repository or path inside it (default: current directory)")
    result.add_argument("--all", action="store_true", help="show commits from all refs, not only HEAD history")
    result.add_argument("--max-count", type=int, default=30, metavar="N", help="maximum graph commits or unique commits per comparison side (default: 30)")
    result.add_argument("--base", metavar="REV", help="revision used for branch ahead/behind counts")
    result.add_argument("--no-color", action="store_true", help="disable colored output")
    result.add_argument("--version", action="version", version="%(prog)s 0.2.0")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_intermixed_args(argv)
    if args.max_count < 1:
        parser().error("--max-count must be at least 1")
    if args.command == "compare" and len(args.revisions) != 2:
        parser().error("compare requires exactly two revisions")
    if args.command != "compare" and args.revisions:
        parser().error("revisions are only accepted by compare")
    if args.command == "graph" and args.json:
        parser().error("--json requires summary or compare")
    if args.command != "graph" and (args.base or args.all):
        parser().error("--base and --all apply only to graph")
    color = not args.no_color and sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    try:
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
