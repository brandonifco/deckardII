"""Tests for tools/rules-conformance-gate.py.

Issue #41: rules-conformance review was a claim a PR body made about itself, and this
script is what turns it into a mechanical merge gate. It has two halves:

  * `evaluate()` (and its helpers) is pure decision logic -- no `gh`, no `git`, no
    network -- exercised directly here, the same way test_pr_policy.py exercises
    `pr_policy.check()`.
  * the CLI (`main()`) is thin plumbing that fetches PR facts through `gh` and a local
    `git diff`, then calls `evaluate()`. It is exercised end to end over a real,
    throwaway git repository with `gh` stubbed on PATH -- in the same style
    test_review_packet.py uses, and for the same reason: Issue #28 exists because a
    tooling test once believed its `gh` stub was airtight and it filed nine real Issues.
    The negative is proven here too (test_no_cli_test_in_this_module_can_reach_real_gh),
    not just asserted.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "rules-conformance-gate.py"
PR_POLICY = ROOT / "tools" / "pr-policy.py"
RULES_SURFACE_LIB = ROOT / "tools" / "lib" / "rules-surface.sh"

_spec = importlib.util.spec_from_file_location("rules_conformance_gate", SCRIPT)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
sys.modules["rules_conformance_gate"] = gate
_spec.loader.exec_module(gate)

IN_HOUSE = gate.IN_HOUSE_CONTEXT
CODEX, GEMINI, IN_HOUSE_INDEPENDENT = gate.INDEPENDENT_CONTEXTS

RULES_FILE = "M\tsrc/Deckard.Rules/DiceTest.cs\n"
DOCS_FILE = "M\tdocs/roadmap.md\n"

GOOD_PASS_DESC = "PASS bodySha256=" + "a" * 64 + " pages=44-47"
GOOD_FAIL_DESC = "FAIL bodySha256=" + "a" * 64 + " pages=44-47"


def status(context: str, state: str = "success", description: str = GOOD_PASS_DESC) -> dict:
    return {"context": context, "state": state, "description": description}


# --------------------------------------------------------------------- pure-logic tests


class RulesSurfaceTouchedTests(unittest.TestCase):
    """Delegated to tools/lib/rules-surface.sh -- the same file tools/review-packet.sh
    uses -- so these are really regression tests for the shared subprocess bridge, not a
    second copy of the rename-safety logic itself."""

    def test_plain_docs_change_is_not_a_rules_surface(self):
        self.assertFalse(gate.rules_surface_touched(DOCS_FILE))

    def test_plain_rules_change_is_a_rules_surface(self):
        self.assertTrue(gate.rules_surface_touched(RULES_FILE))

    def test_data_project_counts(self):
        self.assertTrue(gate.rules_surface_touched("M\tsrc/Deckard.Data/Skills.json\n"))

    def test_rules_tests_count(self):
        self.assertTrue(gate.rules_surface_touched("M\ttests/Deckard.Rules.Tests/T.cs\n"))

    def test_source_manifest_counts(self):
        self.assertTrue(gate.rules_surface_touched("M\t.github/source-manifest.json\n"))

    def test_core_only_change_does_not_count(self):
        self.assertFalse(gate.rules_surface_touched("M\tsrc/Deckard.Core/Pcg.cs\n"))

    def test_rename_into_rules_surface_counts(self):
        self.assertTrue(
            gate.rules_surface_touched("R100\ttools/foo.py\tsrc/Deckard.Rules/Foo.cs\n")
        )

    def test_rename_out_of_rules_surface_counts(self):
        self.assertTrue(
            gate.rules_surface_touched("R100\tsrc/Deckard.Rules/Foo.cs\ttools/foo.py\n")
        )


class RulesSurfaceLockFileExclusionTests(unittest.TestCase):
    """Issue #86: a directory match alone is not enough. NuGet's
    RestorePackagesWithLockFile (#43) writes packages.lock.json beside each project file
    it locks, landing it under src/Deckard.Rules/, src/Deckard.Data/ and their test
    projects -- a hash manifest of transitive package versions, not rules content, and
    one that can never carry a printed-page citation. This is the exact diff shape that
    blocked PR #82.
    """

    def test_rules_lock_file_alone_is_not_a_rules_surface(self):
        self.assertFalse(
            gate.rules_surface_touched("M\tsrc/Deckard.Rules/packages.lock.json\n")
        )

    def test_data_lock_file_alone_is_not_a_rules_surface(self):
        self.assertFalse(
            gate.rules_surface_touched("M\tsrc/Deckard.Data/packages.lock.json\n")
        )

    def test_rules_tests_lock_file_alone_is_not_a_rules_surface(self):
        self.assertFalse(
            gate.rules_surface_touched("M\ttests/Deckard.Rules.Tests/packages.lock.json\n")
        )

    def test_data_tests_lock_file_alone_is_not_a_rules_surface(self):
        self.assertFalse(
            gate.rules_surface_touched("M\ttests/Deckard.Data.Tests/packages.lock.json\n")
        )

    def test_dicepool_roll_still_counts(self):
        """A real rules file, in the same directory the lock file lives in, must still
        trip the gate -- the exclusion is by exact basename, not by directory."""
        self.assertTrue(
            gate.rules_surface_touched(
                "M\tsrc/Deckard.Rules/Resolution/DicePoolRoll.cs\n"
            )
        )

    def test_source_manifest_still_counts(self):
        """The case most likely to break: a wider exclusion (e.g. "*.json") would also
        swallow this file, which is deliberately part of the rules surface."""
        self.assertTrue(gate.rules_surface_touched("M\t.github/source-manifest.json\n"))

    def test_lock_file_does_not_hide_a_real_rules_file_in_the_same_diff(self):
        """The blocked-PR shape: a lock file change riding alongside a genuine rules
        change in the same `git diff --name-status` output must still trip the gate."""
        self.assertTrue(
            gate.rules_surface_touched(
                "M\tsrc/Deckard.Rules/packages.lock.json\n"
                "M\tsrc/Deckard.Rules/Resolution/DicePoolRoll.cs\n"
            )
        )

    def test_rename_of_a_cs_file_into_a_rules_directory_still_counts(self):
        """The rename/copy shape (`R100<TAB>old<TAB>new`) must survive the exclusion
        check -- excluding by basename must not accidentally blind the rename handling."""
        self.assertTrue(
            gate.rules_surface_touched(
                "R100\ttools/Foo.cs\tsrc/Deckard.Rules/Resolution/Foo.cs\n"
            )
        )

    def test_rename_of_a_lock_file_into_a_rules_directory_does_not_count(self):
        """A rename is just another way a path lands in the tree -- the destination
        basename is still what gets excluded, whether the file is new or moved."""
        self.assertFalse(
            gate.rules_surface_touched(
                "R100\ttools/packages.lock.json\tsrc/Deckard.Rules/packages.lock.json\n"
            )
        )


class RulesSurfaceTouchedFailureTests(unittest.TestCase):
    """Issue #89 follow-up: rules_surface_touched()'s own contract for the library's
    no-flag mode is exit 0 = "yes" and exit 1 = "no" -- both legitimate answers. Before
    this fix, `result.returncode == 0` collapsed EVERY other exit code (a bash syntax
    error, the interpreter or script missing, a hang) into False alongside the legitimate
    "1 = no", which is the identical fail-open shape tools/pr-policy.py's
    RulesSurfaceClassifierError fixes: this gate would report "no rules-surface file
    changed; gate passes trivially" for a PR that actually touched one, because its own
    classifier broke. A required merge gate must not read its own failure to answer as an
    answer of "no".
    """

    def setUp(self):
        self.original_lib = gate.RULES_SURFACE_LIB
        self.original_timeout = gate.TOUCHED_TIMEOUT_SECONDS
        self.tmpdir = tempfile.mkdtemp(prefix="deckard-gate-rules-surface-broken-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.addCleanup(setattr, gate, "RULES_SURFACE_LIB", self.original_lib)
        self.addCleanup(setattr, gate, "TOUCHED_TIMEOUT_SECONDS", self.original_timeout)

    def _install_script(self, contents: str) -> Path:
        path = Path(self.tmpdir) / "broken-rules-surface.sh"
        path.write_text(contents, encoding="utf-8")
        gate.RULES_SURFACE_LIB = path
        return path

    def test_an_exit_code_outside_0_and_1_raises_naming_the_script_and_stderr(self):
        self._install_script("#!/usr/bin/env bash\necho 'boom' >&2\nexit 2\n")
        with self.assertRaises(RuntimeError) as ctx:
            gate.rules_surface_touched(RULES_FILE)
        message = str(ctx.exception)
        self.assertIn(str(gate.RULES_SURFACE_LIB), message)
        self.assertIn("2", message)
        self.assertIn("boom", message)

    def test_broken_classifier_does_not_read_as_no_rules_surface_touched(self):
        """The exact scenario the defect names: a rules-file diff must not be silently
        treated as untouched when the classifier itself is broken."""
        self._install_script("#!/usr/bin/env bash\nexit 127\n")
        with self.assertRaises(RuntimeError):
            gate.rules_surface_touched(RULES_FILE)

    def test_missing_script_raises_naming_the_path(self):
        gate.RULES_SURFACE_LIB = Path(self.tmpdir) / "does-not-exist.sh"
        with self.assertRaises(RuntimeError) as ctx:
            gate.rules_surface_touched(RULES_FILE)
        self.assertIn(str(gate.RULES_SURFACE_LIB), str(ctx.exception))

    def test_timeout_raises_a_clear_error_instead_of_an_unhandled_traceback(self):
        self._install_script("#!/usr/bin/env bash\nsleep 5\n")
        gate.TOUCHED_TIMEOUT_SECONDS = 0.2
        with self.assertRaises(RuntimeError) as ctx:
            gate.rules_surface_touched(RULES_FILE)
        message = str(ctx.exception)
        self.assertIn(str(gate.RULES_SURFACE_LIB), message)
        self.assertIn("timed out", message)

    def test_legitimate_yes_and_no_are_unaffected_by_the_fix(self):
        """Exit 0 and exit 1 are the library's real, designed contract -- not failures --
        and must still resolve normally, not raise."""
        self._install_script("#!/usr/bin/env bash\nexit 0\n")
        self.assertTrue(gate.rules_surface_touched(RULES_FILE))
        self._install_script("#!/usr/bin/env bash\nexit 1\n")
        self.assertFalse(gate.rules_surface_touched(RULES_FILE))


class IndependentVerdictRequiredTests(unittest.TestCase):
    def test_ordinary_issue_does_not_require_an_independent_verdict(self):
        required, notes = gate.independent_verdict_required(["state:ready", "area:tooling"])
        self.assertFalse(required)
        self.assertEqual(notes, [])

    def test_risk_labelled_issue_requires_an_independent_verdict(self):
        required, notes = gate.independent_verdict_required(["risk:rules-conformance"])
        self.assertTrue(required)
        self.assertTrue(notes)

    def test_unresolved_labels_fail_safe_to_requiring_an_independent_verdict(self):
        required, notes = gate.independent_verdict_required(None)
        self.assertTrue(required)
        self.assertTrue(notes)


class EvaluateTests(unittest.TestCase):
    def test_non_rules_diff_passes_trivially_and_does_not_apply(self):
        result = gate.evaluate(DOCS_FILE, None, [])
        self.assertTrue(result.passed)
        self.assertFalse(result.applies)

    def test_rules_diff_with_no_verdict_is_blocked(self):
        result = gate.evaluate(RULES_FILE, ["state:ready"], [])
        self.assertFalse(result.passed)
        self.assertTrue(result.applies)
        self.assertIn(IN_HOUSE, " ".join(result.reasons))

    def test_rules_diff_with_a_valid_in_house_verdict_passes(self):
        result = gate.evaluate(RULES_FILE, ["state:ready"], [status(IN_HOUSE)])
        self.assertTrue(result.passed)

    def test_pending_state_does_not_pass(self):
        result = gate.evaluate(RULES_FILE, ["state:ready"], [status(IN_HOUSE, state="pending")])
        self.assertFalse(result.passed)
        self.assertIn("not success", " ".join(result.reasons))

    def test_explicit_fail_verdict_blocks_even_with_a_well_formed_description(self):
        result = gate.evaluate(
            RULES_FILE, ["state:ready"], [status(IN_HOUSE, state="failure", description=GOOD_FAIL_DESC)]
        )
        self.assertFalse(result.passed)

    def test_malformed_description_is_rejected(self):
        result = gate.evaluate(
            RULES_FILE, ["state:ready"], [status(IN_HOUSE, description="looks fine, trust me")]
        )
        self.assertFalse(result.passed)
        self.assertIn("does not name a packet", " ".join(result.reasons))

    def test_description_missing_pages_is_rejected(self):
        result = gate.evaluate(
            RULES_FILE, ["state:ready"],
            [status(IN_HOUSE, description="PASS bodySha256=" + "a" * 64)],
        )
        self.assertFalse(result.passed)

    def test_risk_label_with_only_in_house_verdict_is_blocked(self):
        """Acceptance criterion: the gate FAILS with the in-house verdict alone."""
        result = gate.evaluate(RULES_FILE, ["risk:rules-conformance"], [status(IN_HOUSE)])
        self.assertFalse(result.passed)
        self.assertIn("no independent verdict recorded", " ".join(result.reasons))

    def test_risk_label_passes_with_codex_as_the_independent_verdict(self):
        """Acceptance criterion: passes for each of the three independent contexts."""
        result = gate.evaluate(
            RULES_FILE, ["risk:rules-conformance"], [status(IN_HOUSE), status(CODEX)]
        )
        self.assertTrue(result.passed, result.reasons)

    def test_risk_label_passes_with_gemini_as_the_independent_verdict(self):
        """Acceptance criterion: passes for each of the three independent contexts."""
        result = gate.evaluate(
            RULES_FILE, ["risk:rules-conformance"], [status(IN_HOUSE), status(GEMINI)]
        )
        self.assertTrue(result.passed, result.reasons)

    def test_risk_label_passes_with_in_house_independent_as_the_fallback_verdict(self):
        """Acceptance criterion: passes for each of the three independent contexts."""
        result = gate.evaluate(
            RULES_FILE, ["risk:rules-conformance"],
            [status(IN_HOUSE), status(IN_HOUSE_INDEPENDENT)],
        )
        self.assertTrue(result.passed, result.reasons)

    def test_a_generic_independent_context_is_not_accepted(self):
        """The vendor-naming requirement (Issue #83): a collapsed, unnamed
        'deckard-verdict/independent' context must not satisfy the gate -- only the
        three explicitly named contexts in INDEPENDENT_CONTEXTS may."""
        result = gate.evaluate(
            RULES_FILE, ["risk:rules-conformance"],
            [status(IN_HOUSE), status("deckard-verdict/independent")],
        )
        self.assertFalse(result.passed)

    # -------------------- a recorded fail cannot be overridden by a later pass --------

    def test_codex_fail_is_not_overridden_by_a_gemini_pass(self):
        """Regression: an earlier version of evaluate() took the first PASSING context
        in the chain and ignored the rest, so a recorded Codex FAIL could be cleared by
        recording Gemini as a pass afterward. The chain advances on a vendor being
        UNREACHABLE (absent), never on disagreement -- a vendor that answered was
        available, so its fail is binding regardless of what another context says."""
        result = gate.evaluate(
            RULES_FILE, ["risk:rules-conformance"],
            [status(IN_HOUSE), status(CODEX, state="failure"), status(GEMINI)],
        )
        self.assertFalse(result.passed)
        reasons = " ".join(result.reasons)
        self.assertIn(f"'{CODEX}' verdict for this head commit is 'failure', not success", reasons)
        self.assertIn("does not override this recorded failure", reasons)

    def test_codex_absent_and_gemini_pass_still_passes(self):
        """Absence is not failure -- this is the whole point of the fallback chain.
        Codex never having been invoked (no status recorded at all) must not block a
        recorded Gemini pass."""
        result = gate.evaluate(
            RULES_FILE, ["risk:rules-conformance"], [status(IN_HOUSE), status(GEMINI)]
        )
        self.assertTrue(result.passed, result.reasons)

    def test_codex_pass_then_gemini_fail_still_fails(self):
        """Order must not matter: a recorded fail blocks the gate whether it was
        recorded before or after a passing verdict at a different context."""
        result = gate.evaluate(
            RULES_FILE, ["risk:rules-conformance"],
            [status(IN_HOUSE), status(CODEX), status(GEMINI, state="failure")],
        )
        self.assertFalse(result.passed)
        reasons = " ".join(result.reasons)
        self.assertIn(f"'{GEMINI}' verdict for this head commit is 'failure', not success", reasons)
        self.assertIn("does not override this recorded failure", reasons)

    def test_all_three_independent_contexts_absent_still_fails(self):
        """Unchanged from before this fix: no independent verdict recorded anywhere in
        the chain blocks the gate."""
        result = gate.evaluate(RULES_FILE, ["risk:rules-conformance"], [status(IN_HOUSE)])
        self.assertFalse(result.passed)
        self.assertIn("no independent verdict recorded", " ".join(result.reasons))

    def test_non_risk_label_does_not_require_the_independent_verdict(self):
        result = gate.evaluate(RULES_FILE, ["area:tooling"], [status(IN_HOUSE)])
        self.assertTrue(result.passed)

    def test_unresolved_labels_require_an_independent_verdict_too(self):
        result = gate.evaluate(RULES_FILE, None, [status(IN_HOUSE)])
        self.assertFalse(result.passed)
        self.assertIn("no independent verdict recorded", " ".join(result.reasons))

    def test_a_verdict_for_a_different_context_does_not_satisfy_the_requirement(self):
        result = gate.evaluate(RULES_FILE, ["state:ready"], [status("some-other-check")])
        self.assertFalse(result.passed)

    def test_uppercase_hex_in_the_hash_is_accepted(self):
        description = "PASS bodySha256=" + "A" * 64 + " pages=44-47"
        result = gate.evaluate(RULES_FILE, ["state:ready"], [status(IN_HOUSE, description=description)])
        self.assertTrue(result.passed)


# --------------------------------------------------------------------------- CLI tests


class GateCliTests(unittest.TestCase):
    """Exercises `main()` over a real throwaway git repository, with `gh` stubbed on
    PATH -- never the real one. Mirrors test_review_packet.py's fixture style."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="deckard-rules-gate-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self._init_repo()

    def _run(self, *args: str, cwd: Path | None = None, check: bool = True):
        return subprocess.run(
            list(args), cwd=str(cwd or self.repo), check=check, capture_output=True, text=True
        )

    def _commit(self, message: str) -> str:
        self._run("git", "add", "-A")
        self._run("git", "commit", "-q", "-m", message)
        return self._run("git", "rev-parse", "HEAD").stdout.strip()

    def _init_repo(self) -> None:
        (self.repo / "tools" / "lib").mkdir(parents=True)
        shutil.copy(SCRIPT, self.repo / "tools" / "rules-conformance-gate.py")
        shutil.copy(PR_POLICY, self.repo / "tools" / "pr-policy.py")
        shutil.copy(RULES_SURFACE_LIB, self.repo / "tools" / "lib" / "rules-surface.sh")
        self._run("git", "init", "-q", "-b", "main")
        self._run("git", "config", "user.email", "test@example.com")
        self._run("git", "config", "user.name", "Deckard Test")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "existing.md").write_text("hello\n", encoding="utf-8")
        (self.repo / "src" / "Deckard.Rules").mkdir(parents=True)
        (self.repo / "src" / "Deckard.Rules" / "Existing.cs").write_text("// x\n", encoding="utf-8")
        self.base_sha = self._commit("initial")

    def _branch_touching(self, name: str, relpath: str, content: str) -> str:
        self._run("git", "checkout", "-q", "-b", name)
        target = self.repo / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        head_sha = self._commit(f"change on {name}")
        self._run("git", "checkout", "-q", "main")
        return head_sha

    # --------------------------------------------------------------------- gh stub

    def _stub_gh(self, *, pr_number: str, closes_issue: str | None, issue_labels: list[str],
                 statuses: list[dict], repo: str = "example/repo") -> Path:
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        self.gh_log = self.tmp / "gh-calls.txt"

        body = f"## Linked Issue\n\nCloses #{closes_issue}\n" if closes_issue else "no issue linked\n"
        body_file = self.tmp / "pr-body.json"
        body_file.write_text(json.dumps({"body": body}), encoding="utf-8")

        labels_json = self.tmp / "issue-labels.json"
        labels_json.write_text(
            "{\"labels\":[" + ",".join(f'{{"name":"{l}"}}' for l in issue_labels) + "]}",
            encoding="utf-8",
        )

        statuses_json = self.tmp / "statuses.json"
        statuses_json.write_text(json.dumps({"statuses": statuses}), encoding="utf-8")

        shas_json = self.tmp / "shas.json"
        shas_json.write_text(
            json.dumps({"baseRefName": "main", "headRefOid": self.head_sha}),
            encoding="utf-8",
        )

        repo_json = self.tmp / "repo.json"
        repo_json.write_text(json.dumps({"nameWithOwner": repo}), encoding="utf-8")

        script = f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{self.gh_log}"
if [[ "$1 $2" == "pr view" && "$3" == "{pr_number}" ]]; then
  if [[ "$*" == *"--json body"* ]]; then cat "{body_file}"; exit 0; fi
  if [[ "$*" == *"baseRefName,headRefOid"* ]]; then cat "{shas_json}"; exit 0; fi
  echo "gh stub: unhandled pr view fields: $*" >&2; exit 1
fi
if [[ "$1 $2" == "issue view" ]]; then
  if [[ "$*" == *"--json labels"* ]]; then cat "{labels_json}"; exit 0; fi
  echo "gh stub: unhandled issue view fields: $*" >&2; exit 1
fi
if [[ "$1 $2" == "repo view" ]]; then
  cat "{repo_json}"; exit 0
fi
if [[ "$1" == "api" ]]; then
  cat "{statuses_json}"; exit 0
fi
echo "gh stub: unhandled invocation: $*" >&2
exit 1
"""
        stub = bindir / "gh"
        stub.write_text(script, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return bindir

    def run_cli(self, *args: str, pr_number: str = "9", closes_issue: str | None = "4",
                issue_labels: list[str] | None = None, statuses: list[dict] | None = None):
        bindir = self._stub_gh(
            pr_number=pr_number, closes_issue=closes_issue,
            issue_labels=issue_labels or [], statuses=statuses or [],
        )
        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        return subprocess.run(
            [sys.executable, str(self.repo / "tools" / "rules-conformance-gate.py"), *args],
            capture_output=True, text=True, env=env, cwd=str(self.repo), check=False,
        )

    def gh_calls(self) -> list[str]:
        return self.gh_log.read_text(encoding="utf-8").splitlines() if self.gh_log.exists() else []

    # ------------------------------------------------------------------------- tests

    def test_docs_only_pr_passes_and_never_calls_issue_view_or_status_api(self):
        self.head_sha = self._branch_touching("docs-pr", "docs/new.md", "content\n")
        result = self.run_cli("--pr", "9")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS", result.stdout)
        calls = " ".join(self.gh_calls())
        self.assertNotIn("issue view", calls)
        self.assertNotIn("api", calls)

    def test_rules_pr_with_no_verdict_is_blocked(self):
        self.head_sha = self._branch_touching(
            "rules-pr", "src/Deckard.Rules/New.cs", "// new\n"
        )
        result = self.run_cli("--pr", "9", issue_labels=["state:ready"], statuses=[])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FAIL", result.stdout)

    def test_rules_pr_with_a_recorded_verdict_passes(self):
        self.head_sha = self._branch_touching(
            "rules-pr-ok", "src/Deckard.Rules/New.cs", "// new\n"
        )
        result = self.run_cli(
            "--pr", "9", issue_labels=["state:ready"], statuses=[status(IN_HOUSE)]
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("PASS", result.stdout)

    def test_rules_pr_with_a_stale_verdict_for_a_different_commit_is_blocked(self):
        """A verdict recorded against an earlier head is invisible here because the
        combined-status lookup is keyed to THIS head SHA -- there is nothing to
        'invalidate' because a status for a different SHA was never fetched at all."""
        self.head_sha = self._branch_touching(
            "rules-pr-amended", "src/Deckard.Rules/New.cs", "// v2\n"
        )
        # Simulate "amended": statuses list is empty for the (new) head, as it would be
        # for a genuinely new commit the old verdict never covered.
        result = self.run_cli("--pr", "9", issue_labels=["state:ready"], statuses=[])
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_risk_labelled_issue_needs_the_independent_verdict_too(self):
        self.head_sha = self._branch_touching(
            "risk-pr", "src/Deckard.Rules/New.cs", "// new\n"
        )
        only_in_house = self.run_cli(
            "--pr", "9", issue_labels=["risk:rules-conformance"], statuses=[status(IN_HOUSE)]
        )
        self.assertEqual(only_in_house.returncode, 1, only_in_house.stdout)

        # Any ONE of the three vendor-named independent contexts satisfies the gate.
        for independent_context in (CODEX, GEMINI, IN_HOUSE_INDEPENDENT):
            with self.subTest(context=independent_context):
                result = self.run_cli(
                    "--pr", "9", issue_labels=["risk:rules-conformance"],
                    statuses=[status(IN_HOUSE), status(independent_context)],
                )
                self.assertEqual(result.returncode, 0, result.stdout)

    def test_offline_mode_needs_no_gh_at_all(self):
        changed = self.tmp / "changed.txt"
        changed.write_text(RULES_FILE, encoding="utf-8")
        statuses_file = self.tmp / "statuses-offline.json"
        statuses_file.write_text(json.dumps([status(IN_HOUSE)]), encoding="utf-8")

        # A PATH built by directory (e.g. just python3's own /usr/bin) would still carry
        # the real `gh` in this environment -- bash, gh and python3 all live in
        # /usr/bin here. So build a directory containing ONLY symlinks to `bash` (which
        # rules_surface_touched() needs), the coreutils tools/lib/rules-surface.sh's
        # default streaming mode shells out to (`cat`, `cut`, `tr`), and the interpreter,
        # with no `gh` anywhere on it: if any code path in this run tried to shell out to
        # `gh`, it would fail with FileNotFoundError rather than silently reaching the
        # real one. `cat`/`cut`/`tr` must be present precisely because
        # rules_surface_touched() now fails LOUDLY (Issue #89 follow-up) when the
        # classifier can't run at all -- omitting them used to be masked by the fail-open
        # bug that fix closed (any non-zero exit, including "cut: command not found",
        # silently read as "no rules surface touched," which this PASS's rules-file input
        # would not have exposed either way).
        path_without_gh = self.tmp / "path-without-gh"
        path_without_gh.mkdir()
        bash_path = shutil.which("bash")
        assert bash_path, "bash must be on PATH for this test to mean anything"
        os.symlink(bash_path, path_without_gh / "bash")
        for tool in ("cat", "cut", "tr"):
            tool_path = shutil.which(tool)
            assert tool_path, f"{tool} must be on PATH for this test to mean anything"
            os.symlink(tool_path, path_without_gh / tool)
        os.symlink(sys.executable, path_without_gh / Path(sys.executable).name)

        env = dict(os.environ)
        env["PATH"] = str(path_without_gh)
        result = subprocess.run(
            [sys.executable, str(self.repo / "tools" / "rules-conformance-gate.py"),
             "--changed-files", str(changed), "--labels", "state:ready",
             "--statuses-file", str(statuses_file)],
            capture_output=True, text=True, env=env, cwd=str(self.repo), check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_cli_test_in_this_module_can_reach_real_gh(self):
        """Prove the containment rather than asserting it in a docstring (Issue #28)."""
        self.head_sha = self._branch_touching(
            "airtight-pr", "src/Deckard.Rules/New.cs", "// new\n"
        )
        result = self.run_cli(
            "--pr", "9", issue_labels=["state:ready"], statuses=[status(IN_HOUSE)]
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        # The stub, not real gh, answered every call -- and its fake repo name never
        # once had to be consulted for this to pass, but the log proves only the stub
        # was reachable at all.
        calls = self.gh_calls()
        self.assertTrue(calls)
        self.assertNotIn("brandonifco", " ".join(calls))


if __name__ == "__main__":
    unittest.main()
