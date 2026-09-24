"""Read-only Git process boundary and terminal escaping."""
from __future__ import annotations

import os
import subprocess
import unicodedata
from pathlib import Path
from typing import Sequence


class GitError(RuntimeError):
    """A failure reported by Git."""


def run_git(repo: Path, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Git in read-only, non-interactive mode."""
    # Ignore Git routing/config injection from the caller; --repo selects the target.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "LC_ALL": "C",
            "GIT_CONFIG_COUNT": "5",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.devnull,
            "GIT_CONFIG_KEY_1": "color.ui",
            "GIT_CONFIG_VALUE_1": "false",
            "GIT_CONFIG_KEY_2": "core.commitGraph",
            "GIT_CONFIG_VALUE_2": "false",
            "GIT_CONFIG_KEY_3": "log.showSignature",
            "GIT_CONFIG_VALUE_3": "false",
            "GIT_CONFIG_KEY_4": "i18n.logOutputEncoding",
            "GIT_CONFIG_VALUE_4": "utf-8",
        }
    )
    try:
        result = subprocess.run(
            ["git", "--no-pager", "-C", os.fspath(repo), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError("Git query exceeded 60 seconds; narrow the repository or comparison") from exc
    except FileNotFoundError as exc:
        raise GitError("Git executable was not found") from exc
    result = subprocess.CompletedProcess(result.args, result.returncode,
                                         result.stdout.decode("utf-8", "surrogateescape"),
                                         result.stderr.decode("utf-8", "surrogateescape"))
    if check and (result.returncode or (args[0] == "for-each-ref" and result.stderr.strip())):
        message = result.stderr.strip() or "Git command failed"
        raise GitError(message.removeprefix("fatal: ").strip())
    return result


def safe(text: str, *, backslashes: bool = True) -> str:
    """Escape controls, bidi/format characters, and backslashes in terminal fields."""
    result = []
    for char in text:
        if char == "\\" and backslashes:
            result.append("\\\\")
        elif unicodedata.category(char).startswith("C") or char in ("\u2028", "\u2029"):
            code = ord(char)
            result.append(f"\\x{code:02x}" if code < 256 else f"\\u{code:04x}")
        else:
            result.append(char)
    return "".join(result)


def repository_root(repo: Path) -> Path:
    # Keep the supplied worktree context, including bare and linked worktrees.
    run_git(repo, ["rev-parse", "--git-dir"])
    return repo.absolute()
