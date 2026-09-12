"""Tests for scripts/lib/dotnet-env.sh.

This is the SDK resolution logic validate.sh and doctor.sh both source -- the part with
branches worth pinning, per Issue #33. Every case below is a fake directory tree; no SDK
is ever downloaded. The worktree-fallback case still needs a real `git worktree add`,
because the behaviour under test is entirely about what
`git rev-parse --git-common-dir` reports.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "scripts" / "lib" / "dotnet-env.sh"


def source_and_report(script: Path, cwd: Path) -> subprocess.CompletedProcess:
    """Source dotnet-env.sh in a fresh shell and report the state it left behind.

    NUL-separated so a value containing ':' (PATH) or being empty is unambiguous.
    """
    probe = (
        f"source {str(script)!r}\n"
        'printf "%s\\0%s\\0%s\\0" '
        '"${FRAMEWORK_DOTNET_SOURCE-}" "${FRAMEWORK_DOTNET_HOME-}" "${PATH-}"\n'
    )
    return subprocess.run(
        ["bash", "-c", probe], cwd=cwd, capture_output=True, text=True, check=False,
    )


def parse(result: subprocess.CompletedProcess) -> tuple[str, str, str]:
    fields = result.stdout.split("\0")
    return fields[0], fields[1], fields[2]


class RepoLocalDotnetTests(unittest.TestCase):
    """Case 1: a .dotnet next to this checkout's own scripts/ is chosen."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="framework-dotnet-env-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "scripts" / "lib").mkdir(parents=True)
        shutil.copy(LIB, self.tmp / "scripts" / "lib" / "dotnet-env.sh")
        (self.tmp / ".dotnet").mkdir()
        (self.tmp / ".dotnet" / "dotnet").write_text("#!/bin/sh\necho fake\n")

    def test_repo_local_dotnet_is_chosen(self):
        script = self.tmp / "scripts" / "lib" / "dotnet-env.sh"
        result = source_and_report(script, self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        source, home, path = parse(result)
        expected = str(self.tmp / ".dotnet")
        self.assertEqual(source, "repo-local")
        self.assertEqual(home, expected)
        self.assertTrue(path.startswith(expected + ":"), path)

    def test_it_is_chosen_even_though_it_is_not_a_git_repository(self):
        """The repo-local check is a plain directory test -- git is never invoked."""
        self.assertFalse((self.tmp / ".git").exists())
        script = self.tmp / "scripts" / "lib" / "dotnet-env.sh"
        result = source_and_report(script, self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(parse(result)[0], "repo-local")


class NoDotnetAnywhereTests(unittest.TestCase):
    """Case 2: no .dotnet at all. This is the CI path -- PATH's dotnet must be used,
    untouched, exactly as it is today.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="framework-dotnet-env-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "scripts" / "lib").mkdir(parents=True)
        shutil.copy(LIB, self.tmp / "scripts" / "lib" / "dotnet-env.sh")
        # A real checkout, same shape as actions/checkout in CI, just with no .dotnet/.
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.tmp, check=True)

    def test_nothing_changes_without_a_dotnet_directory(self):
        """CI (actions/setup-dotnet, no repo-local .dotnet/) must take exactly this path."""
        script = self.tmp / "scripts" / "lib" / "dotnet-env.sh"
        result = source_and_report(script, self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        source, home, path = parse(result)
        self.assertEqual(source, "path")
        self.assertEqual(home, "")
        # The inherited PATH is passed through byte-for-byte -- nothing prepended.
        self.assertEqual(path, os.environ.get("PATH", ""))


class WorktreeFallbackTests(unittest.TestCase):
    """Case 3: invoked from a worktree with no .dotnet of its own; the primary
    checkout's .dotnet is chosen. This is the case every tools/dispatch-agent.sh
    worktree hits, since one install has to serve all of them.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="framework-dotnet-env-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.primary = self.tmp / "primary"
        self.primary.mkdir()
        run = lambda *a: subprocess.run(
            a, cwd=self.primary, check=True, capture_output=True
        )
        run("git", "init", "-q", "-b", "main")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "T")
        (self.primary / "scripts" / "lib").mkdir(parents=True)
        shutil.copy(LIB, self.primary / "scripts" / "lib" / "dotnet-env.sh")
        run("git", "add", "scripts/lib/dotnet-env.sh")
        run("git", "commit", "-qm", "seed")

        # A real linked worktree, checked out from the same commit -- it gets its own
        # copy of scripts/lib/dotnet-env.sh at the same relative path, exactly like
        # tools/dispatch-agent.sh produces.
        self.worktree = self.tmp / "wt"
        run("git", "worktree", "add", "-q", "-b", "issue-1-x", str(self.worktree))

        (self.primary / ".dotnet").mkdir()
        (self.primary / ".dotnet" / "dotnet").write_text("#!/bin/sh\necho fake\n")

    def test_primary_checkouts_dotnet_is_chosen_from_the_worktree(self):
        self.assertFalse((self.worktree / ".dotnet").exists(), "fixture sanity check")
        script = self.worktree / "scripts" / "lib" / "dotnet-env.sh"
        result = source_and_report(script, self.worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        source, home, path = parse(result)
        expected = str(self.primary / ".dotnet")
        self.assertEqual(source, "primary-checkout")
        self.assertEqual(home, expected)
        self.assertTrue(path.startswith(expected + ":"), path)

    def test_the_primary_checkout_itself_still_resolves_its_own_dotnet(self):
        """Sanity check on the derivation, not just the worktree: run from the primary
        checkout directly, where --git-common-dir and --git-dir are the same path.
        """
        script = self.primary / "scripts" / "lib" / "dotnet-env.sh"
        result = source_and_report(script, self.primary)
        self.assertEqual(result.returncode, 0, result.stderr)
        source, home, _path = parse(result)
        self.assertEqual(source, "repo-local")
        self.assertEqual(home, str(self.primary / ".dotnet"))


if __name__ == "__main__":
    unittest.main()
