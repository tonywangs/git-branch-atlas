from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    return result.stdout.strip()


class AtlasIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="atlas tests ")
        self.repo = Path(self.temp.name) / "repo with spaces"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Test User")
        git(self.repo, "config", "user.email", "test@example.invalid")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def commit(self, subject: str, filename: str = "note.txt") -> str:
        path = self.repo / filename
        path.write_text(subject, encoding="utf-8")
        git(self.repo, "add", filename)
        git(self.repo, "commit", "-m", subject)
        return git(self.repo, "rev-parse", "--short", "HEAD")

    def atlas(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        return subprocess.run(
            [sys.executable, "-m", "git_branch_atlas", "--no-color", *args],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            check=False,
        )

    def test_graph_branches_tags_unicode_and_counts(self) -> None:
        first = self.commit("Initial café")
        git(self.repo, "tag", "v1.0")
        git(self.repo, "switch", "-c", "feature")
        second = self.commit("Feature 東京", "feature.txt")
        result = self.atlas("--repo", str(self.repo), "--all", "--base", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HEAD: feature", result.stdout)
        self.assertIn("feature", result.stdout)
        self.assertIn("+1/-0", result.stdout)
        self.assertIn(second, result.stdout)
        self.assertIn(first, result.stdout)
        self.assertIn("Feature 東京", result.stdout)
        self.assertIn("tag: v1.0", result.stdout)
        self.assertIn("HEAD -> feature", result.stdout)
        self.assertNotIn("\x1b[", result.stdout)

    def test_empty_repository(self) -> None:
        result = self.atlas("--repo", str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("empty repository", result.stdout)
        self.assertIn("no commits yet", result.stdout)

    def test_detached_head_and_max_count(self) -> None:
        old = self.commit("One")
        self.commit("Two", "two.txt")
        git(self.repo, "checkout", "--detach", old)
        result = self.atlas("--repo", str(self.repo), "--max-count", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("detached at", result.stdout)
        graph = result.stdout.split("Commit graph", 1)[1]
        self.assertIn("One", graph)
        self.assertNotIn("Two", graph)

    def test_merge_topology_is_rendered(self) -> None:
        self.commit("Root")
        git(self.repo, "switch", "-c", "topic")
        self.commit("Topic work", "topic.txt")
        git(self.repo, "switch", "main")
        self.commit("Main work", "main.txt")
        git(self.repo, "merge", "--no-ff", "topic", "-m", "Merge topic")
        result = self.atlas("--repo", str(self.repo), "--all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Merge topic", result.stdout)
        self.assertIn("|\\", result.stdout)

    def test_non_repository_and_invalid_options(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        result = self.atlas("--repo", str(outside))
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a git repository", result.stderr.lower())
        invalid = self.atlas("--max-count", "0")
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("must be at least 1", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
