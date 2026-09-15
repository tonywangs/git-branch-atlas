"""Command-line interface for Git Branch Atlas."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class GitError(RuntimeError):
    """A failure reported by Git."""


@dataclass(frozen=True)
class Branch:
    name: str
    short_hash: str
    subject: str
    timestamp: int


def run_git(repo: Path, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Git in read-only, non-interactive mode."""
    env = os.environ.copy()
    env.update(
        {
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.devnull,
            "GIT_CONFIG_KEY_1": "color.ui",
            "GIT_CONFIG_VALUE_1": "false",
        }
    )
    try:
        result = subprocess.run(
            ["git", "--no-pager", "-C", os.fspath(repo), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("Git executable was not found") from exc
    if check and result.returncode:
        message = result.stderr.strip() or "Git command failed"
        raise GitError(message.removeprefix("fatal: ").strip())
    return result


def repository_root(repo: Path) -> Path:
    result = run_git(repo, ["rev-parse", "--show-toplevel"])
    return Path(result.stdout.rstrip("\n"))


def current_head(repo: Path) -> tuple[str, bool]:
    symbolic = run_git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    if symbolic.returncode == 0:
        return symbolic.stdout.strip(), False
    oid = run_git(repo, ["rev-parse", "--short", "HEAD"], check=False)
    return (oid.stdout.strip() or "unborn"), True


def branches(repo: Path) -> list[Branch]:
    fmt = "%(refname:short)%00%(objectname:short)%00%(subject)%00%(committerdate:unix)"
    result = run_git(repo, ["for-each-ref", f"--format={fmt}", "refs/heads"])
    found: list[Branch] = []
    for line in result.stdout.splitlines():
        fields = line.split("\0")
        if len(fields) == 4:
            found.append(Branch(fields[0], fields[1], fields[2], int(fields[3])))
    return sorted(found, key=lambda item: (-item.timestamp, item.name))


def choose_base(repo: Path, requested: str | None, head: str, detached: bool) -> str | None:
    if requested:
        valid = run_git(repo, ["rev-parse", "--verify", "--quiet", f"{requested}^{{commit}}"], check=False)
        if valid.returncode:
            raise GitError(f"base revision not found: {requested}")
        return requested
    if not detached:
        upstream = run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], check=False)
        if upstream.returncode == 0:
            return upstream.stdout.strip()
    names = {branch.name for branch in branches(repo)}
    for candidate in ("main", "master"):
        if candidate in names:
            return candidate
    return None if detached else head


def ahead_behind(repo: Path, base: str, branch: str) -> tuple[int, int]:
    result = run_git(repo, ["rev-list", "--left-right", "--count", f"{base}...{branch}"])
    behind, ahead = (int(value) for value in result.stdout.split())
    return ahead, behind


def paint(text: str, code: str, color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if color else text


def render_overview(repo: Path, color: bool, requested_base: str | None) -> str:
    head, detached = current_head(repo)
    local = branches(repo)
    base = choose_base(repo, requested_base, head, detached) if local else None
    title = paint("Branch overview", "1;36", color)
    identity = f"detached at {head}" if detached else head
    lines = [f"{title}  HEAD: {paint(identity, '1;32', color)}"]
    if not local:
        lines.append("  (empty repository: no commits or local branches)")
        return "\n".join(lines)
    if base:
        lines[0] += f"  base: {paint(base, '1;33', color)}"
    name_width = max(len(item.name) for item in local)
    for item in local:
        marker = "*" if not detached and item.name == head else " "
        counts = ""
        if base:
            ahead, behind = ahead_behind(repo, base, item.name)
            counts = f"  +{ahead}/-{behind}"
        lines.append(f"{marker} {item.name:<{name_width}}  {item.short_hash}{counts}  {item.subject}")
    return "\n".join(lines)


def render_graph(repo: Path, color: bool, all_refs: bool, max_count: int) -> str:
    exists = run_git(repo, ["rev-parse", "--verify", "HEAD"], check=False)
    if exists.returncode:
        return "\nCommit graph\n  (no commits yet)"
    pretty = "format:%C(auto)%h%C(reset) %s %C(auto)%d%C(reset)"
    args = ["log", "--graph", "--decorate=short", f"--max-count={max_count}", f"--pretty={pretty}"]
    if all_refs:
        args.append("--all")
    args.append("--color=always" if color else "--color=never")
    graph = run_git(repo, args).stdout.rstrip()
    return f"\n{paint('Commit graph', '1;36', color)}\n{graph}"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="git-branch-atlas",
        description="Visualize Git branches and commit history at a glance.",
    )
    result.add_argument("--repo", default=".", metavar="PATH", help="repository or path inside it (default: current directory)")
    result.add_argument("--all", action="store_true", help="show commits from all refs, not only HEAD history")
    result.add_argument("--max-count", type=int, default=30, metavar="N", help="maximum graph commits (default: 30)")
    result.add_argument("--base", metavar="REV", help="revision used for branch ahead/behind counts")
    result.add_argument("--no-color", action="store_true", help="disable colored output")
    result.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.max_count < 1:
        parser().error("--max-count must be at least 1")
    color = not args.no_color and sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    try:
        root = repository_root(Path(args.repo).expanduser())
        output = render_overview(root, color, args.base) + render_graph(root, color, args.all, args.max_count)
        print(output)
    except (GitError, OSError) as exc:
        print(f"git-branch-atlas: error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
