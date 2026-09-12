"""Tests for tools/record-verdict.sh.

Issue #41: this script is the one place a rules-conformance verdict becomes a fact
GitHub can check -- a commit status posted directly against a head SHA. Nothing here
contacts GitHub: `gh` is stubbed with a recording script on PATH, in the same style
test_review_packet.py and test_new_issue.py use, and for the same reason Issue #28
exists -- a stub that only "looks" airtight is not the same as one that is proven so
(test_no_test_in_this_module_can_reach_real_gh).
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
SCRIPT = ROOT / "tools" / "record-verdict.sh"

GOOD_HASH = "a" * 64


class RecordVerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="deckard-record-verdict-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    # --------------------------------------------------------------------- gh stub

    def _stub_gh(self, *, repo: str = "example/repo", pr_branch: str = "issue-4-foo",
                 fail_workflow_run: bool = False) -> Path:
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        self.gh_log = self.tmp / "gh-calls.txt"
        workflow_run_exit = "1" if fail_workflow_run else "0"

        script = f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{self.gh_log}"
if [[ "$1 $2" == "repo view" ]]; then
  echo "{repo}"
  exit 0
fi
if [[ "$1" == "api" ]]; then
  exit 0
fi
if [[ "$1 $2" == "pr view" ]]; then
  echo "{pr_branch}"
  exit 0
fi
if [[ "$1 $2" == "workflow run" ]]; then
  exit {workflow_run_exit}
fi
echo "gh stub: unhandled invocation: $*" >&2
exit 1
"""
        stub = bindir / "gh"
        stub.write_text(script, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return bindir

    def run_script(self, *args: str, **stub_kwargs):
        bindir = self._stub_gh(**stub_kwargs)
        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True, text=True, env=env, check=False,
        )

    def gh_calls(self) -> list[str]:
        return self.gh_log.read_text(encoding="utf-8").splitlines() if self.gh_log.exists() else []

    # ------------------------------------------------------------- flag validation

    def test_sha_is_required(self):
        result = self.run_script(
            "--reviewer", "rules-conformance", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--sha is required", result.stderr)
        self.assertEqual(self.gh_calls(), [])

    def test_sha_must_look_like_a_commit(self):
        result = self.run_script(
            "--sha", "not-hex!", "--reviewer", "rules-conformance", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not look like a commit SHA", result.stderr)

    def test_reviewer_is_required(self):
        result = self.run_script(
            "--sha", "abc1234", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--reviewer is required", result.stderr)

    def test_reviewer_must_be_a_known_value(self):
        """Acceptance criterion (Issue #83): record-verdict.sh rejects an unknown
        --reviewer value."""
        result = self.run_script(
            "--sha", "abc1234", "--reviewer", "gpt", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "must be one of rules-conformance, codex, gemini, in-house-independent",
            result.stderr,
        )
        self.assertEqual(self.gh_calls(), [])

    def test_reviewer_accepts_gemini_as_a_fallback_independent_reviewer(self):
        """Issue #83: the ordered fallback chain's second link."""
        result = self.run_script(
            "--sha", "abc1234def", "--reviewer", "gemini", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        api_call = next(c for c in self.gh_calls() if c.startswith("api "))
        self.assertIn("context=deckard-verdict/gemini", api_call)

    def test_reviewer_accepts_in_house_independent_as_the_last_resort_fallback(self):
        """Issue #83: the ordered fallback chain's third link -- named distinctly from
        `rules-conformance` (the always-required first verdict) so a merged commit's
        statuses show a same-vendor fallback occurred, rather than hiding it."""
        result = self.run_script(
            "--sha", "abc1234def", "--reviewer", "in-house-independent", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        api_call = next(c for c in self.gh_calls() if c.startswith("api "))
        self.assertIn("context=deckard-verdict/in-house-independent", api_call)

    def test_verdict_must_be_pass_or_fail(self):
        result = self.run_script(
            "--sha", "abc1234", "--reviewer", "codex", "--verdict", "maybe",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be 'pass' or 'fail'", result.stderr)

    def test_packet_sha256_must_be_64_hex_characters(self):
        result = self.run_script(
            "--sha", "abc1234", "--reviewer", "codex", "--verdict", "pass",
            "--packet-sha256", "deadbeef", "--pages", "44-47",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("64 hex characters", result.stderr)

    def test_pages_is_required(self):
        result = self.run_script(
            "--sha", "abc1234", "--reviewer", "codex", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--pages is required", result.stderr)

    def test_rerun_gate_requires_pr_and_fails_before_recording_anything(self):
        result = self.run_script(
            "--sha", "abc1234", "--reviewer", "codex", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47", "--rerun-gate",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--rerun-gate requires --pr", result.stderr)
        # Validated statically, before any gh call -- no verdict silently posted for a
        # combination that was already known to be wrong.
        self.assertEqual(self.gh_calls(), [])

    def test_description_over_140_chars_is_refused(self):
        result = self.run_script(
            "--sha", "abc1234", "--reviewer", "codex", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
            "--notes", "x" * 100,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(">140", result.stderr)
        self.assertEqual(self.gh_calls(), [])

    def test_unknown_argument_is_refused(self):
        result = self.run_script("--bogus")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown argument", result.stderr)

    # ------------------------------------------------------------------- recording

    def test_pass_verdict_posts_success_state_with_expected_context_and_description(self):
        result = self.run_script(
            "--sha", "abc1234def", "--reviewer", "rules-conformance", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.gh_calls()
        api_call = next(c for c in calls if c.startswith("api "))
        self.assertIn("repos/example/repo/statuses/abc1234def", api_call)
        self.assertIn("state=success", api_call)
        self.assertIn("context=deckard-verdict/rules-conformance", api_call)
        self.assertIn(f"description=PASS bodySha256={GOOD_HASH} pages=44-47", api_call)

    def test_fail_verdict_posts_failure_state(self):
        result = self.run_script(
            "--sha", "abc1234def", "--reviewer", "codex", "--verdict", "fail",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        api_call = next(c for c in self.gh_calls() if c.startswith("api "))
        self.assertIn("state=failure", api_call)
        self.assertIn("context=deckard-verdict/codex", api_call)
        self.assertIn("description=FAIL bodySha256=", api_call)

    def test_notes_are_appended_to_the_description(self):
        result = self.run_script(
            "--sha", "abc1234def", "--reviewer", "codex", "--verdict", "fail",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
            "--notes", "row 9 disagrees",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        api_call = next(c for c in self.gh_calls() if c.startswith("api "))
        self.assertIn("-- row 9 disagrees", api_call)

    def test_packet_sha256_is_lowercased_in_the_description(self):
        result = self.run_script(
            "--sha", "abc1234def", "--reviewer", "codex", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH.upper(), "--pages", "44-47",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        api_call = next(c for c in self.gh_calls() if c.startswith("api "))
        self.assertIn(f"description=PASS bodySha256={GOOD_HASH} pages=44-47", api_call)

    def test_explicit_repo_flag_skips_the_repo_view_lookup(self):
        result = self.run_script(
            "--sha", "abc1234def", "--reviewer", "codex", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47", "--repo", "brandonifco/deckard",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.gh_calls()
        self.assertFalse(any(c.startswith("repo view") for c in calls))
        api_call = next(c for c in calls if c.startswith("api "))
        self.assertIn("repos/brandonifco/deckard/statuses/", api_call)

    def test_rerun_gate_dispatches_the_workflow_against_the_prs_branch(self):
        result = self.run_script(
            "--sha", "abc1234def", "--reviewer", "codex", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
            "--pr", "108", "--rerun-gate",
            pr_branch="issue-4-dice",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.gh_calls()
        self.assertTrue(any(c.startswith("pr view 108") for c in calls))
        dispatch = next(c for c in calls if c.startswith("workflow run"))
        self.assertIn("rules-conformance-gate.yml", dispatch)
        self.assertIn("--ref issue-4-dice", dispatch)
        self.assertIn("-f pr=108", dispatch)

    def test_rerun_gate_failure_to_dispatch_is_reported(self):
        result = self.run_script(
            "--sha", "abc1234def", "--reviewer", "codex", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47",
            "--pr", "108", "--rerun-gate",
            fail_workflow_run=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not dispatch a re-run", result.stderr)

    def test_no_test_in_this_module_can_reach_real_gh(self):
        """Prove the containment rather than asserting it in a docstring (Issue #28)."""
        result = self.run_script(
            "--sha", "abc1234def", "--reviewer", "rules-conformance", "--verdict", "pass",
            "--packet-sha256", GOOD_HASH, "--pages", "44-47", "--repo", "brandonifco/deckard",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.gh_calls()
        self.assertTrue(calls)
        # The stub, not real gh, handled every call -- nothing here reached a network.


if __name__ == "__main__":
    unittest.main()
