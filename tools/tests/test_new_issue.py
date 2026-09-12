"""Tests for tools/new-issue.sh.

The `start-issue` skill told agents this script "refuses to file a mechanics Issue with
no source locator". It did not: its only validation was that the body was not entirely
comments. An agent trusting a gate that is not there is worse off than one told the
truth, so the gate now exists and these tests hold it to the claim.

No test here contacts GitHub. `gh` is stubbed with a recording script on PATH, so the
cases that pass the gate can assert what *would* have been sent rather than sending it.

An earlier version of this file claimed the same thing and was wrong: only the REJECTED
cases stopped before `gh`. The cases that passed the gate ran `gh issue create` for real
and filed nine Issues titled "probe" in the live repository. The docstring asserted the
property; nothing tested it. That is the third time in this repository a test has agreed
with its author instead of with the code, so the stub now also proves the negative --
see test_no_test_in_this_module_can_reach_real_gh.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "new-issue.sh"

WITH_LOCATOR = """## Purpose
Implement glitch detection.

## Source
SR6 Core / Glitches / printed p. 44 / PDF p. 45

## Exact scope
Determine glitches and critical glitches.
"""

WITHOUT_LOCATOR = """## Purpose
Implement glitch detection.

## Source
N/A

## Exact scope
Determine glitches and critical glitches.
"""

PROSE_BUT_NO_PAGE = """## Purpose
Implement glitch detection.

## Source
The chapter about tests, somewhere near the front of the book.

## Exact scope
Determine glitches and critical glitches.
"""


class NewIssueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="deckard-issue-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def editor_writing(self, body: str) -> str:
        """A fake $EDITOR that overwrites the template with `body`."""
        source = self.tmp / "body.md"
        source.write_text(body, encoding="utf-8")
        editor = self.tmp / "fake-editor.sh"
        editor.write_text(f'#!/usr/bin/env bash\ncat "{source}" > "$1"\n', encoding="utf-8")
        editor.chmod(editor.stat().st_mode | stat.S_IEXEC)
        return str(editor)

    def stub_gh(self) -> Path:
        """A fake `gh` that records its arguments instead of calling GitHub."""
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        self.gh_log = self.tmp / "gh-invocations.txt"
        stub = bindir / "gh"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "$*" >> "{self.gh_log}"\n'
            "echo https://github.com/example/repo/issues/1\n",
            encoding="utf-8",
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return bindir

    def gh_calls(self) -> list[str]:
        return self.gh_log.read_text(encoding="utf-8").splitlines() if self.gh_log.exists() else []

    def run_script(self, body: str, *labels: str):
        env = dict(os.environ)
        env["EDITOR"] = self.editor_writing(body)
        # The stub goes FIRST on PATH so the real gh is unreachable, and GH_TOKEN is
        # cleared so nothing could authenticate even if it were.
        env["PATH"] = f"{self.stub_gh()}{os.pathsep}{env.get('PATH', '')}"
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        args = ["--title", "probe"]
        for label in labels:
            args += ["--label", label]
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True, text=True, env=env, cwd=str(ROOT), check=False,
        )

    def test_mechanics_issue_without_a_page_is_refused(self):
        result = self.run_script(WITHOUT_LOCATOR, "area:rules")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("names\n       no page", result.stderr.replace("\r", ""))

    def test_prose_source_without_a_page_number_is_refused(self):
        result = self.run_script(PROSE_BUT_NO_PAGE, "area:rules")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no page", result.stderr)

    def test_data_issues_are_also_mechanics_issues(self):
        result = self.run_script(WITHOUT_LOCATOR, "area:data")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no page", result.stderr)

    def test_mechanics_issue_with_a_locator_is_filed(self):
        result = self.run_script(WITH_LOCATOR, "area:rules")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.gh_calls()
        self.assertEqual(len(calls), 1)
        self.assertIn("issue create", calls[0])
        self.assertIn("--title probe", calls[0])
        self.assertIn("--label area:rules", calls[0])

    def test_multiple_labels_are_all_passed_through(self):
        result = self.run_script(WITH_LOCATOR, "area:rules", "state:ready", "phase:1-kernel")
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.gh_calls()[0]
        for label in ("area:rules", "state:ready", "phase:1-kernel"):
            self.assertIn(f"--label {label}", call)

    def test_rejected_issues_never_reach_gh(self):
        self.run_script(WITHOUT_LOCATOR, "area:rules")
        self.assertEqual(self.gh_calls(), [], "a refused Issue must not be filed")

    def test_non_mechanics_issue_needs_no_locator(self):
        result = self.run_script(WITHOUT_LOCATOR, "area:tooling")
        self.assertNotIn("no page", result.stderr)
        self.assertEqual(len(self.gh_calls()), 1)

    def test_unlabelled_issue_needs_no_locator(self):
        result = self.run_script(WITHOUT_LOCATOR)
        self.assertNotIn("no page", result.stderr)
        self.assertEqual(len(self.gh_calls()), 1)

    def test_empty_body_is_refused(self):
        result = self.run_script("<!-- still the template -->\n", "area:tooling")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("still empty", result.stderr)

    def test_no_test_in_this_module_can_reach_real_gh(self):
        """Prove the containment rather than asserting it in a docstring.

        This is the test the previous version of this file needed and did not have: it
        claimed no test contacted GitHub, and nine real Issues say otherwise.
        """
        result = self.run_script(WITH_LOCATOR, "area:rules")
        self.assertEqual(result.returncode, 0)
        # The stub, not the real gh, produced this URL.
        self.assertIn("example/repo", result.stdout)
        self.assertNotIn("github.com/brandonifco", result.stdout)

    def test_title_is_required(self):
        result = subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, cwd=str(ROOT), check=False
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--title is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
