"""Tests for tools/review-packet.sh.

Issue #46: the review-packet standard (docs/agent-team.md, "Briefing agents";
.claude/skills/rules-review/SKILL.md) was reachable only from the rules-review path, and
nothing actually built the packet -- two repo-steward dispatches were briefed with prose
and no diff, and a read-only reviewer has no Bash, so it cannot build one for itself.
This generator is the one place the packet gets assembled.

No test here contacts GitHub. `gh` is stubbed with a recording script on PATH, in the
same style test_new_issue.py uses -- and for the same reason: an earlier version of that
file claimed its stub was airtight and was wrong, filing nine real Issues. The negative
is proven here too (test_no_test_in_this_module_can_reach_real_gh), not just asserted.

Each test gets its own throwaway git repository with its own copy of the script, so
REPO_ROOT resolves inside the fixture rather than into this actual repository.
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
SCRIPT = ROOT / "tools" / "review-packet.sh"
RULES_SURFACE_LIB = ROOT / "tools" / "lib" / "rules-surface.sh"

DEFAULT_BODY = """## Purpose

Do the thing.

## Source

N/A

## Exact scope

The thing.

## Acceptance criteria

- The thing is done.
- Nothing else broke.

## Required tests/evidence

Tests pass.
"""

NO_ACCEPTANCE_BODY = """## Purpose

Do the thing.

## Source

N/A

## Acceptance criteria

## Required tests/evidence

Tests pass.
"""

RULES_BODY = """## Purpose

Implement a mechanic.

## Source

SR6 Core / Tests / printed p. 44 / PDF p. 45

## Acceptance criteria

- The mechanic is implemented.

## Required tests/evidence

Tests pass.
"""


class ReviewPacketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="deckard-review-packet-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self._init_repo()

    # ------------------------------------------------------------------ fixture repo

    def _run(self, *args: str, cwd: Path | None = None, check: bool = True) -> None:
        subprocess.run(list(args), cwd=str(cwd or self.repo), check=check,
                        capture_output=True, text=True)

    def _commit(self, message: str) -> None:
        self._run("git", "add", "-A")
        self._run("git", "commit", "-q", "-m", message)

    def _init_repo(self) -> None:
        (self.repo / "tools" / "lib").mkdir(parents=True)
        shutil.copy(SCRIPT, self.repo / "tools" / "review-packet.sh")
        shutil.copy(RULES_SURFACE_LIB, self.repo / "tools" / "lib" / "rules-surface.sh")
        self._run("git", "init", "-q", "-b", "main")
        self._run("git", "config", "user.email", "test@example.com")
        self._run("git", "config", "user.name", "Deckard Test")

        decisions = self.repo / "docs" / "decisions"
        decisions.mkdir(parents=True)
        (decisions / "0001-foo.md").write_text(
            "# 0001 -- Foo decision\n\nBody text.\n", encoding="utf-8"
        )
        (decisions / "0002-bar.md").write_text(
            "# 0002 -- Bar decision\n\nBody text.\n", encoding="utf-8"
        )
        (decisions / "README.md").write_text("index, not an ADR\n", encoding="utf-8")

        src = self.repo / "src"
        src.mkdir()
        (src / "existing.txt").write_text("".join(f"line{n}\n" for n in range(1, 11)),
                                           encoding="utf-8")
        self._commit("initial")

    def _branch_with_change(self, name: str, relpath: str, content: str) -> None:
        """Create `name` off the current HEAD with one file changed/added."""
        self._run("git", "checkout", "-q", "-b", name)
        target = self.repo / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._commit(f"change on {name}")
        self._run("git", "checkout", "-q", "main")

    def _branch_with_rename(self, name: str, old_relpath: str, new_relpath: str) -> None:
        """Create `name` off the current HEAD, renaming `old_relpath` (already committed
        on HEAD) to `new_relpath` via `git mv`. Content is untouched, so git reports this
        as a pure rename (R100) rather than a delete+add -- the exact shape that exposed
        the detection gap: `git diff --name-status` puts a rename on ONE line, as
        "R100<TAB>old<TAB>new"."""
        self._run("git", "checkout", "-q", "-b", name)
        new_path = self.repo / new_relpath
        new_path.parent.mkdir(parents=True, exist_ok=True)
        self._run("git", "mv", old_relpath, new_relpath)
        self._commit(f"rename on {name}")
        self._run("git", "checkout", "-q", "main")

    # --------------------------------------------------------------------- gh stub

    def _stub_gh(self, issue: str, title: str, body: str, pr: dict | None = None) -> Path:
        """`pr`, when given, is {lookup, number, title, url, body}: `gh pr view
        <lookup>` succeeds and returns those fields; any other lookup fails, matching
        real `gh`'s "no pull requests found" behavior. `pr=None` (the default) makes
        every `gh pr view` call fail -- the normal case in this project, where packets
        are routinely generated before a PR exists."""
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        body_file = self.tmp / "issue-body.md"
        body_file.write_text(body, encoding="utf-8")
        self.gh_log = self.tmp / "gh-calls.txt"
        stub = bindir / "gh"

        pr_block = ""
        if pr:
            pr_body_file = self.tmp / "pr-body.md"
            pr_body_file.write_text(pr["body"], encoding="utf-8")
            pr_block = (
                'if [[ "$1" == "pr" && "$2" == "view" ]]; then\n'
                f'  if [[ "$3" != "{pr["lookup"]}" ]]; then\n'
                '    echo "no pull requests found for branch" >&2\n'
                "    exit 1\n"
                "  fi\n"
                '  if [[ "$*" == *"--json number"* ]]; then\n'
                f'    printf "%s" "{pr["number"]}"\n'
                "    exit 0\n"
                "  fi\n"
                '  if [[ "$*" == *"--json title"* ]]; then\n'
                f'    printf "%s" "{pr["title"]}"\n'
                "    exit 0\n"
                "  fi\n"
                '  if [[ "$*" == *"--json url"* ]]; then\n'
                f'    printf "%s" "{pr["url"]}"\n'
                "    exit 0\n"
                "  fi\n"
                '  if [[ "$*" == *"--json body"* ]]; then\n'
                f'    cat "{pr_body_file}"\n'
                "    exit 0\n"
                "  fi\n"
                "fi\n"
            )

        stub.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "$*" >> "{self.gh_log}"\n'
            'if [[ "$1" == "issue" && "$2" == "view" ]]; then\n'
            f'  if [[ "$3" != "{issue}" ]]; then\n'
            '    echo "gh: issue not found" >&2\n'
            "    exit 1\n"
            "  fi\n"
            '  if [[ "$*" == *"--json title"* ]]; then\n'
            f'    printf "%s" "{title}"\n'
            "    exit 0\n"
            "  fi\n"
            '  if [[ "$*" == *"--json body"* ]]; then\n'
            f'    cat "{body_file}"\n'
            "    exit 0\n"
            "  fi\n"
            "fi\n"
            + pr_block
            + 'if [[ "$1" == "pr" && "$2" == "view" ]]; then\n'
              '  echo "no pull requests found for branch" >&2\n'
              "  exit 1\n"
              "fi\n"
              'echo "gh stub: unhandled invocation: $*" >&2\n'
              "exit 1\n",
            encoding="utf-8",
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return bindir

    def run_script(self, *args: str, issue: str = "1", title: str = "Do the thing",
                    body: str = DEFAULT_BODY, pr: dict | None = None):
        env = dict(os.environ)
        # The stub goes FIRST on PATH so the real gh is unreachable, and both token
        # variables are cleared so nothing could authenticate even if it were.
        env["PATH"] = f"{self._stub_gh(issue, title, body, pr)}{os.pathsep}{env.get('PATH', '')}"
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        return subprocess.run(
            ["bash", str(self.repo / "tools" / "review-packet.sh"), *args],
            capture_output=True, text=True, env=env, cwd=str(self.repo), check=False,
        )

    def gh_calls(self) -> list[str]:
        return self.gh_log.read_text(encoding="utf-8").splitlines() if self.gh_log.exists() else []

    # ------------------------------------------------------------------------- tests

    def test_issue_is_required(self):
        result = self.run_script("--branch", "main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--issue is required", result.stderr)

    def test_issue_must_be_numeric(self):
        result = self.run_script("--issue", "abc", "--branch", "main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--issue must be numeric", result.stderr)

    def test_branch_is_required(self):
        result = self.run_script("--issue", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--branch is required", result.stderr)

    def test_context_must_be_a_non_negative_integer(self):
        result = self.run_script("--issue", "1", "--branch", "main", "--context", "-1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--context must be", result.stderr)

    def test_unknown_argument_is_refused(self):
        result = self.run_script("--issue", "1", "--branch", "main", "--bogus")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown argument", result.stderr)

    def test_help_prints_usage_without_calling_gh(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("review-packet.sh", result.stdout)
        # The header comment's closing sentence. A prior version's usage() cut the sed
        # range one line short and truncated mid-sentence; asserting only that the
        # script name appears would not have caught that.
        self.assertIn("never be committed.", result.stdout)
        self.assertEqual(self.gh_calls(), [])

    def test_pr_flag_must_be_numeric(self):
        result = self.run_script("--issue", "1", "--branch", "main", "--pr", "xyz")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--pr must be numeric", result.stderr)

    def test_nonexistent_issue_is_refused(self):
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        result = self.run_script("--issue", "2", "--branch", "feature", "--base", "main",
                                  issue="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)

    def test_issue_without_acceptance_criteria_is_refused(self):
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  body=NO_ACCEPTANCE_BODY)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Acceptance criteria", result.stderr)

    def test_no_diff_between_base_and_branch_is_refused(self):
        result = self.run_script("--issue", "1", "--branch", "main", "--base", "main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing to review", result.stderr)

    def test_unknown_branch_is_refused(self):
        result = self.run_script("--issue", "1", "--branch", "no-such-ref-xyz", "--base", "main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr)

    def test_output_inside_repo_and_ungitignored_is_refused(self):
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        out = self.repo / "packet.md"
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  "--output", str(out))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must never be committed", result.stderr)
        self.assertFalse(out.exists())

    def test_output_refusal_does_not_create_the_parent_directory(self):
        """`realpath -m` does not require the parent to exist, so nothing forces the
        refusal to run before an unconditional `mkdir -p` -- a prior version created
        the directory and THEN refused, leaving a stray one behind. The previous test
        cannot catch this: its output path's parent is the repo root, which already
        exists either way."""
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        out = self.repo / "packet-drafts" / "x.md"
        self.assertFalse(out.parent.exists())
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  "--output", str(out))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must never be committed", result.stderr)
        self.assertFalse(out.parent.exists(),
                          "the refusal must not create packet-drafts/ before checking")

    def test_output_inside_repo_but_gitignored_is_allowed(self):
        (self.repo / ".gitignore").write_text("/ignored-packets/\n", encoding="utf-8")
        self._commit("add gitignore")
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        out = self.repo / "ignored-packets" / "packet.md"
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  "--output", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(out.is_file())

    def test_output_outside_repo_is_allowed_and_creates_parent_dirs(self):
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        out = self.tmp / "nested" / "dir" / "packet.md"
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  "--output", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(out.is_file())

    def test_packet_contains_issue_title_and_acceptance_criteria(self):
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  title="Frobnicate the widget")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Frobnicate the widget", result.stdout)
        self.assertIn("The thing is done.", result.stdout)

    def test_packet_contains_changed_files_and_diff(self):
        self._branch_with_change("feature", "src/existing.txt",
                                  "".join(f"line{n}\n" for n in range(1, 11)).replace(
                                      "line5", "CHANGED"))
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("src/existing.txt", result.stdout)
        self.assertIn("-line5", result.stdout)
        self.assertIn("+CHANGED", result.stdout)

    def test_packet_lists_adrs_but_not_the_readme(self):
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0001-foo.md -- 0001 -- Foo decision", result.stdout)
        self.assertIn("0002-bar.md -- 0002 -- Bar decision", result.stdout)
        self.assertNotIn("README.md --", result.stdout)

    def test_source_locator_is_carried_verbatim(self):
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  body=RULES_BODY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SR6 Core / Tests / printed p. 44 / PDF p. 45", result.stdout)

    def test_non_rules_change_does_not_ask_for_rules_conformance(self):
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NO -- repo-steward's structural review is the whole review",
                       result.stdout)
        self.assertNotIn("rules-conformance review", result.stdout)

    def test_rules_change_asks_for_rules_conformance_and_independent_verdict(self):
        self._branch_with_change("feature", "src/Deckard.Rules/Glitches.cs",
                                  "// a rule\n")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  body=RULES_BODY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("YES -- rules-conformance and the independent verdict", result.stdout)
        self.assertIn("rules-conformance review", result.stdout)
        self.assertIn("Independent verdict review", result.stdout)

    def test_modification_within_rules_path_is_detected(self):
        """Regression table row 1 (a plain `M` inside src/Deckard.Rules/) was already
        detected before the rename fix below -- kept as the baseline the other two rows
        are judged against."""
        (self.repo / "src" / "Deckard.Rules").mkdir(parents=True, exist_ok=True)
        (self.repo / "src" / "Deckard.Rules" / "Foo.cs").write_text("// v1\n", encoding="utf-8")
        self._commit("add Foo.cs under Rules")
        self._branch_with_change("feature", "src/Deckard.Rules/Foo.cs", "// v2\n")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  body=RULES_BODY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("YES -- rules-conformance and the independent verdict", result.stdout)

    def test_rename_into_rules_path_is_detected(self):
        """Regression table row 2 -- the gap this fix closes. A rename from
        src/Deckard.Core/ into src/Deckard.Rules/ puts BOTH paths on one
        "R100<TAB>old<TAB>new" line; only the old (non-rules) path anchored the line
        before the fix, so this was silently reported as no rules review needed."""
        (self.repo / "src" / "Deckard.Core").mkdir(parents=True, exist_ok=True)
        (self.repo / "src" / "Deckard.Core" / "Foo.cs").write_text("// v1\n", encoding="utf-8")
        self._commit("add Foo.cs under Core")
        self._branch_with_rename("feature", "src/Deckard.Core/Foo.cs", "src/Deckard.Rules/Foo.cs")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  body=RULES_BODY)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Confirms git actually reported a rename (not a delete+add) -- otherwise this
        # test would not exercise the one-line-per-change shape the bug depended on.
        self.assertIn("R100\tsrc/Deckard.Core/Foo.cs\tsrc/Deckard.Rules/Foo.cs", result.stdout)
        self.assertIn("YES -- rules-conformance and the independent verdict", result.stdout)

    def test_rename_out_of_rules_path_is_detected(self):
        """Regression table row 3: a rename from src/Deckard.Rules/ to
        src/Deckard.Core/ was already detected before the fix, since the OLD path
        anchored the line. Kept as a regression so a future change to the detection
        logic cannot silently flip it back."""
        (self.repo / "src" / "Deckard.Rules").mkdir(parents=True, exist_ok=True)
        (self.repo / "src" / "Deckard.Rules" / "Foo.cs").write_text("// v1\n", encoding="utf-8")
        self._commit("add Foo.cs under Rules")
        self._branch_with_rename("feature", "src/Deckard.Rules/Foo.cs", "src/Deckard.Core/Foo.cs")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  body=RULES_BODY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("R100\tsrc/Deckard.Rules/Foo.cs\tsrc/Deckard.Core/Foo.cs", result.stdout)
        self.assertIn("YES -- rules-conformance and the independent verdict", result.stdout)

    def test_source_manifest_change_also_counts_as_rules_touching(self):
        self._branch_with_change("feature", ".github/source-manifest.json", "{}\n")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  body=RULES_BODY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("YES -- rules-conformance and the independent verdict", result.stdout)

    def test_context_flag_changes_the_diff_width(self):
        content = "".join(f"line{n}\n" for n in range(1, 11)).replace("line5", "CHANGED")
        self._branch_with_change("feature", "src/existing.txt", content)
        narrow = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  "--context", "0")
        wide = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                "--context", "5")
        self.assertEqual(narrow.returncode, 0, narrow.stderr)
        self.assertEqual(wide.returncode, 0, wide.stderr)
        self.assertIn("diff context: -U0", narrow.stdout)
        self.assertIn("diff context: -U5", wide.stdout)
        self.assertLess(len(narrow.stdout), len(wide.stdout))

    def test_rejected_packets_never_reach_gh(self):
        self.run_script("--issue", "1", "--branch", "main", "--base", "main")
        # The no-diff refusal happens after both gh calls (title, body) succeed --
        # unlike new-issue.sh's pre-gh gate, this generator needs the Issue's body
        # before it can know whether there is anything to review. What matters is that
        # a refusal never produces a packet, checked directly below.
        result = self.run_script("--issue", "1", "--branch", "main", "--base", "main")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_no_pr_found_states_the_caveat_explicitly(self):
        """The generator only calls `gh issue view` -- it has no other way to see
        whether a PR exists, and packets in this project are routinely built before one
        does. The packet must say so, and must name the two repo-steward checks
        (Closes #NNN, evidence) that do not apply without a PR body to check them
        against, rather than silently omitting the section."""
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No open PR found for 'feature'", result.stdout)
        self.assertIn("Closes #NNN", result.stdout)
        self.assertIn("do NOT apply at this stage", result.stdout)

    def test_explicit_pr_not_found_is_refused(self):
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  "--pr", "999")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PR #999 not found", result.stderr)

    def test_pr_found_by_branch_name_is_included_verbatim(self):
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        pr = {
            "lookup": "feature", "number": "108", "title": "Frobnicate the widget",
            "url": "https://github.com/example/repo/pull/108",
            "body": "Closes #1\n\n## Tests and evidence\n\n$ ./scripts/validate.sh full\nPASS\n",
        }
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main", pr=pr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#108 -- Frobnicate the widget", result.stdout)
        self.assertIn("https://github.com/example/repo/pull/108", result.stdout)
        self.assertIn("Closes #1", result.stdout)
        self.assertIn("./scripts/validate.sh full", result.stdout)
        self.assertNotIn("No open PR found", result.stdout)

    def test_pr_found_by_explicit_pr_flag(self):
        """`--pr` looks the PR up by number instead of by branch name -- needed once a
        branch is gone (deleted post-merge) but the PR itself still exists, which is
        exactly the situation for demonstrating this generator against an already-merged
        PR such as #47 or #45."""
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        pr = {
            "lookup": "108", "number": "108", "title": "Frobnicate the widget",
            "url": "https://github.com/example/repo/pull/108", "body": "Closes #1\n",
        }
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  "--pr", "108", pr=pr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#108 -- Frobnicate the widget", result.stdout)

    def test_no_test_in_this_module_can_reach_real_gh(self):
        """Prove the containment rather than asserting it in a docstring.

        tools/tests/test_new_issue.py documents exactly this failure mode: a stub that
        was claimed airtight and was not, and filed nine real Issues. This asserts the
        stub is what actually answered, not the real `gh`.
        """
        self._branch_with_change("feature", "src/existing.txt", "changed\n")
        result = self.run_script("--issue", "1", "--branch", "feature", "--base", "main",
                                  title="probe-title-from-stub")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("probe-title-from-stub", result.stdout)
        self.assertGreaterEqual(len(self.gh_calls()), 2)


if __name__ == "__main__":
    unittest.main()
