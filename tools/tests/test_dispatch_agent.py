"""Tests for tools/dispatch-agent.sh.

Only the guards that do not require GitHub are covered here: argument validation, the
primary-checkout requirement, and the rule that worktrees live outside the repository.
Everything past those needs a real Issue and a real remote.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "dispatch-agent.sh"


class DispatchAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        # A fixture repository with its own copy of the script, so REPO_ROOT resolves to
        # the fixture rather than to Deckard itself.
        self.tmp = Path(tempfile.mkdtemp(prefix="deckard-dispatch-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        (self.repo / "tools").mkdir(parents=True)
        shutil.copy(SCRIPT, self.repo / "tools" / "dispatch-agent.sh")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, check=True)

    def run_script(self, *args: str, worktree_root: str | None = None):
        import os

        env = dict(os.environ)
        if worktree_root is not None:
            env["DECKARD_WORKTREE_ROOT"] = worktree_root
        return subprocess.run(
            ["bash", str(self.repo / "tools" / "dispatch-agent.sh"), *args],
            cwd=self.repo, capture_output=True, text=True, env=env, check=False,
        )

    def test_worktree_root_inside_the_repository_is_refused(self):
        """A worktree inside the repo gets committed, scanned, or deleted by a clean step."""
        result = self.run_script("42", worktree_root=str(self.repo / "worktrees"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("resolves inside the repository", result.stderr)

    def test_nested_worktree_root_is_refused(self):
        result = self.run_script("42", worktree_root=str(self.repo / "a" / "b" / "c"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("resolves inside the repository", result.stderr)

    def test_worktree_root_outside_the_repository_passes_the_check(self):
        """It then fails on the Issue lookup, which is past the guard under test."""
        result = self.run_script("999999", worktree_root=str(self.tmp / "outside"))
        self.assertNotIn("resolves inside the repository", result.stderr)

    def test_non_numeric_issue_is_refused(self):
        result = self.run_script("abc")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be numeric", result.stderr)

    def test_cleanup_requires_a_numeric_issue(self):
        result = self.run_script("--cleanup", "not-a-number")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be numeric", result.stderr)

    def test_no_arguments_prints_usage(self):
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dispatch-agent.sh", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
