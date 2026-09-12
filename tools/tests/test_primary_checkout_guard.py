"""Tests for .claude/hooks/primary-checkout-guard.py.

This guard had a real bug on first write: `git rev-parse --git-common-dir` returns a
path relative to the directory git ran in, so resolving it against the process cwd made
the check answer "not the primary checkout" everywhere except the repository root. The
guard appeared to work -- it blocked commits at the root -- while silently permitting
every file write in every subdirectory.

That is the failure mode these tests exist for. A guard nobody tests is a guard nobody
can trust, and a guard that fails open is worse than no guard, because it is believed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / ".claude" / "hooks" / "primary-checkout-guard.py"

ALLOW, BLOCK = 0, 2


class GuardTestCase(unittest.TestCase):
    """Each test gets a real throwaway git repo, plus a real linked worktree."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="framework-guard-"))
        cls.primary = cls.tmp / "primary"
        cls.primary.mkdir()
        run = lambda *a: subprocess.run(a, cwd=cls.primary, check=True, capture_output=True)
        run("git", "init", "-q", "-b", "main")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "T")
        (cls.primary / "src").mkdir()
        (cls.primary / "src" / "a.cs").write_text("// a\n")
        run("git", "add", "src/a.cs")
        run("git", "commit", "-qm", "seed")
        cls.worktree = cls.tmp / "wt"
        run("git", "worktree", "add", "-q", "-b", "issue-1-x", str(cls.worktree))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def guard(self, payload: dict, env_extra: dict | None = None) -> int:
        import os

        env = dict(os.environ)
        env.pop("FRAMEWORK_ALLOW_PRIMARY_MUTATION", None)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(GUARD)],
            input=json.dumps(payload), text=True, capture_output=True, env=env, check=False,
        ).returncode

    def bash(self, command: str, cwd: Path, **kw) -> int:
        return self.guard({"tool_name": "Bash", "cwd": str(cwd),
                           "tool_input": {"command": command}}, **kw)

    def write(self, file_path: Path, cwd: Path, tool: str = "Write", **kw) -> int:
        return self.guard({"tool_name": tool, "cwd": str(cwd),
                           "tool_input": {"file_path": str(file_path)}}, **kw)


class PrimaryCheckoutMutationTests(GuardTestCase):
    def test_commit_in_primary_is_blocked(self):
        self.assertEqual(self.bash("git commit -m x", self.primary), BLOCK)

    def test_commit_from_a_subdirectory_is_blocked(self):
        """The original bug: relative --git-common-dir defeated the check below root."""
        self.assertEqual(self.bash("git commit -m x", self.primary / "src"), BLOCK)

    def test_push_is_blocked(self):
        self.assertEqual(self.bash("git push origin main", self.primary), BLOCK)

    def test_reset_is_blocked(self):
        self.assertEqual(self.bash("git reset --hard HEAD~1", self.primary), BLOCK)

    def test_rebase_is_blocked(self):
        self.assertEqual(self.bash("git rebase main", self.primary), BLOCK)

    def test_stash_is_blocked(self):
        self.assertEqual(self.bash("git stash -u", self.primary), BLOCK)

    def test_cherry_pick_is_blocked(self):
        self.assertEqual(self.bash("git cherry-pick abc123", self.primary), BLOCK)

    def test_checking_out_a_feature_branch_is_blocked(self):
        self.assertEqual(self.bash("git checkout issue-1-x", self.primary), BLOCK)
        self.assertEqual(self.bash("git switch issue-1-x", self.primary), BLOCK)

    def test_force_removing_a_worktree_is_blocked(self):
        self.assertEqual(
            self.bash("git worktree remove --force /somewhere", self.primary), BLOCK
        )

    def test_non_fast_forward_merge_is_blocked(self):
        self.assertEqual(self.bash("git merge origin/feature", self.primary), BLOCK)


class SanctionedPrimaryOperationTests(GuardTestCase):
    def test_fast_forward_sync_is_allowed(self):
        """Following origin/main is what the primary checkout is for."""
        self.assertEqual(self.bash("git merge --ff-only origin/main", self.primary), ALLOW)

    def test_checkout_main_is_allowed(self):
        self.assertEqual(self.bash("git checkout main", self.primary), ALLOW)

    def test_checkout_of_a_path_is_allowed(self):
        self.assertEqual(self.bash("git checkout -- src/a.cs", self.primary), ALLOW)

    def test_read_only_git_is_allowed(self):
        for command in ("git status", "git log --oneline", "git diff", "git fetch origin"):
            self.assertEqual(self.bash(command, self.primary), ALLOW, command)

    def test_running_the_gate_is_allowed(self):
        self.assertEqual(self.bash("./scripts/validate.sh full", self.primary), ALLOW)

    def test_dispatching_an_agent_is_allowed(self):
        self.assertEqual(self.bash("tools/dispatch-agent.sh 42", self.primary), ALLOW)


class FileWriteTests(GuardTestCase):
    def test_write_into_primary_is_blocked(self):
        self.assertEqual(self.write(self.primary / "src" / "b.cs", self.primary), BLOCK)

    def test_edit_of_a_primary_root_file_is_blocked(self):
        self.assertEqual(
            self.write(self.primary / "CLAUDE.md", self.primary, tool="Edit"), BLOCK
        )

    def test_write_into_a_nested_primary_directory_is_blocked(self):
        nested = self.primary / "src" / "deep" / "deeper"
        nested.mkdir(parents=True, exist_ok=True)
        self.assertEqual(self.write(nested / "c.cs", self.primary), BLOCK)

    def test_write_outside_any_repo_is_allowed(self):
        self.assertEqual(self.write(self.tmp / "scratch.txt", self.tmp), ALLOW)


class WorktreeTests(GuardTestCase):
    """A linked worktree is where implementation belongs. Nothing is blocked there."""

    def test_commit_in_a_worktree_is_allowed(self):
        self.assertEqual(self.bash("git commit -m x", self.worktree), ALLOW)

    def test_push_from_a_worktree_is_allowed(self):
        self.assertEqual(self.bash("git push -u origin issue-1-x", self.worktree), ALLOW)

    def test_write_in_a_worktree_is_allowed(self):
        self.assertEqual(self.write(self.worktree / "src" / "a.cs", self.worktree), ALLOW)

    def test_write_in_a_worktree_subdirectory_is_allowed(self):
        sub = self.worktree / "src"
        self.assertEqual(self.write(sub / "new.cs", sub), ALLOW)


class BulkStagingTests(GuardTestCase):
    """`git add -A` is blocked everywhere, worktrees included."""

    def test_add_all_is_blocked_in_primary(self):
        self.assertEqual(self.bash("git add -A", self.primary), BLOCK)

    def test_add_dot_is_blocked_in_a_worktree(self):
        self.assertEqual(self.bash("git add .", self.worktree), BLOCK)

    def test_add_all_long_flag_is_blocked(self):
        self.assertEqual(self.bash("git add --all", self.worktree), BLOCK)

    def test_explicit_paths_are_allowed(self):
        self.assertEqual(self.bash("git add src/a.cs src/b.cs", self.worktree), ALLOW)

    def test_add_patch_is_allowed(self):
        self.assertEqual(self.bash("git add -p src/a.cs", self.worktree), ALLOW)


class BypassRegressionTests(GuardTestCase):
    """Every command below walked through the first version of this guard.

    A fresh-context review ran 20 payloads against the real hook with cwd set to the
    primary checkout. These are the ones it got past. The regex approach could not see
    git's two-token global options, guarded no shell write path at all, and was missing
    several mutating verbs outright.
    """

    def test_global_config_option_does_not_hide_a_commit(self):
        self.assertEqual(self.bash("git -c user.name=x commit -m x", self.primary), BLOCK)

    def test_dash_C_into_the_primary_is_blocked_from_anywhere(self):
        """`git -C <primary> ...` is the exact form the open-pr skill teaches."""
        self.assertEqual(
            self.bash(f"git -C {self.primary} commit -m x", self.tmp), BLOCK
        )

    def test_dash_C_into_a_worktree_is_still_allowed(self):
        self.assertEqual(
            self.bash(f"git -C {self.worktree} commit -m x", self.tmp), ALLOW
        )

    def test_revert_is_blocked(self):
        self.assertEqual(self.bash("git revert HEAD", self.primary), BLOCK)

    def test_am_is_blocked(self):
        self.assertEqual(self.bash("git am patch.diff", self.primary), BLOCK)

    def test_force_moving_a_branch_is_blocked(self):
        self.assertEqual(self.bash("git branch -f main HEAD~3", self.primary), BLOCK)

    def test_update_ref_is_blocked(self):
        self.assertEqual(
            self.bash("git update-ref refs/heads/main HEAD~3", self.primary), BLOCK
        )

    def test_clean_is_blocked(self):
        self.assertEqual(self.bash("git clean -xfd", self.primary), BLOCK)

    def test_restore_staged_is_blocked(self):
        self.assertEqual(self.bash("git restore --staged .", self.primary), BLOCK)

    def test_detached_checkout_is_blocked(self):
        """`--detach` was read as the `checkout -- <path>` form."""
        self.assertEqual(self.bash("git checkout --detach abc123", self.primary), BLOCK)

    def test_worktree_inside_the_repository_is_blocked(self):
        self.assertEqual(
            self.bash("git worktree add -b b ./inside-repo main", self.primary), BLOCK
        )

    def test_chained_command_is_inspected_in_every_segment(self):
        self.assertEqual(
            self.bash("echo hello && git commit -m x", self.primary), BLOCK
        )


class ShellWriteTests(GuardTestCase):
    """The first version guarded only Write/Edit, leaving every shell write path open.

    Agents edit through the shell constantly. A guard that blocks the Write tool and
    permits `sed -i` is not a guard, it is a speed bump with good documentation.
    """

    def test_append_redirect_into_primary_is_blocked(self):
        self.assertEqual(self.bash("echo x >> CLAUDE.md", self.primary), BLOCK)

    def test_truncating_redirect_into_primary_is_blocked(self):
        self.assertEqual(self.bash("echo x > src/a.cs", self.primary), BLOCK)

    def test_in_place_sed_into_primary_is_blocked(self):
        self.assertEqual(self.bash("sed -i s/a/b/ src/a.cs", self.primary), BLOCK)

    def test_tee_into_primary_is_blocked(self):
        self.assertEqual(self.bash("echo x | tee src/a.cs", self.primary), BLOCK)

    def test_rm_in_primary_is_blocked(self):
        self.assertEqual(self.bash("rm -rf src", self.primary), BLOCK)

    def test_mv_in_primary_is_blocked(self):
        self.assertEqual(self.bash("mv src/a.cs src/b.cs", self.primary), BLOCK)

    def test_reading_is_still_allowed(self):
        for command in ("cat src/a.cs", "grep -r foo src", "ls -la", "git diff"):
            self.assertEqual(self.bash(command, self.primary), ALLOW, command)

    def test_writing_outside_the_repo_is_allowed(self):
        self.assertEqual(self.bash("echo x > /tmp/scratch.txt", self.primary), ALLOW)

    def test_shell_writes_in_a_worktree_are_allowed(self):
        self.assertEqual(self.bash("sed -i s/a/b/ src/a.cs", self.worktree), ALLOW)
        self.assertEqual(self.bash("echo x >> src/a.cs", self.worktree), ALLOW)


class EscapeHatchTests(GuardTestCase):
    def test_escape_hatch_permits_sanctioned_primary_work(self):
        self.assertEqual(
            self.bash("git commit -m x", self.primary,
                      env_extra={"FRAMEWORK_ALLOW_PRIMARY_MUTATION": "1"}),
            ALLOW,
        )

    def test_escape_hatch_permits_writes(self):
        self.assertEqual(
            self.write(self.primary / "src" / "d.cs", self.primary,
                       env_extra={"FRAMEWORK_ALLOW_PRIMARY_MUTATION": "1"}),
            ALLOW,
        )


class RobustnessTests(GuardTestCase):
    def test_unparseable_payload_does_not_break_the_session(self):
        result = subprocess.run(
            [sys.executable, str(GUARD)], input="not json", text=True,
            capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, ALLOW)

    def test_unrelated_tools_are_ignored(self):
        self.assertEqual(
            self.guard({"tool_name": "Read", "cwd": str(self.primary),
                        "tool_input": {"file_path": str(self.primary / "src" / "a.cs")}}),
            ALLOW,
        )


# Issue #74 folded #32, #52 and #60 into one; #32 is the guard itself. Each false
# positive it lists was hit for real, on a command that wrote nothing in the repository
# -- including the project's own mandated commit trailer, which made `git commit -m`
# unusable without `-F`. Every case below is paired with a same-shape case that is a
# real mutation and must still be blocked: a fix that only silences the noise, without
# proving the real cases it sits next to are still caught, is not a fix.
TRAILER = "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"


class RedirectFalsePositiveTests(GuardTestCase):
    """Text that merely *looks* like a redirect, quoted or inside a heredoc body."""

    def test_commit_trailer_is_not_read_as_a_redirect(self):
        self.assertEqual(self.bash(f'git commit -m "{TRAILER}"', self.worktree), ALLOW)

    def test_dash_C_worktree_commit_with_trailer_from_primary_is_allowed(self):
        """The exact form dispatched agents use: orchestrator, primary cwd, `-C` worktree."""
        self.assertEqual(
            self.bash(f'git -C {self.worktree} commit -m "{TRAILER}"', self.primary), ALLOW
        )

    def test_quoted_pipe_and_arrow_in_a_regex_is_not_a_redirect(self):
        self.assertEqual(self.bash('grep -E "^(ok|FAIL)|==>" src/a.cs', self.primary), ALLOW)

    def test_quoted_arrow_in_prose_is_not_a_redirect(self):
        self.assertEqual(
            self.bash('gh issue comment --body "... >> 18) ..." --repo x/y', self.primary),
            ALLOW,
        )

    def test_heredoc_body_arithmetic_is_not_a_redirect(self):
        command = (
            "python3 - <<'PY'\n"
            "print((((1 >> 18) ^ 1) >> 27))\n"
            "PY\n"
        )
        self.assertEqual(self.bash(command, self.primary), ALLOW)

    def test_heredoc_does_not_swallow_a_real_command_that_follows(self):
        """A heredoc body must be dropped -- but only the body, not what comes after it."""
        command = (
            "python3 - <<'PY'\n"
            "print((((1 >> 18) ^ 1) >> 27))\n"
            "PY\n"
            "git commit -m x\n"
        )
        self.assertEqual(self.bash(command, self.primary), BLOCK)

    def test_newline_still_separates_two_commands(self):
        """A bare newline is a command separator too; collapsing it would hide `git commit`."""
        self.assertEqual(self.bash("echo hello\ngit commit -m x", self.primary), BLOCK)

    def test_multiline_quoted_value_does_not_split_the_command(self):
        self.assertEqual(
            self.bash('git commit -m "line one\nline two"', self.primary), BLOCK
        )


class RedirectTruePositiveTests(GuardTestCase):
    """The same shapes as above, but a genuine write into the primary checkout."""

    def test_plain_commit_trailer_in_primary_is_still_blocked(self):
        self.assertEqual(self.bash(f'git commit -m "{TRAILER}"', self.primary), BLOCK)

    def test_dash_C_into_primary_with_trailer_is_still_blocked(self):
        self.assertEqual(
            self.bash(f'git -C {self.primary} commit -m "{TRAILER}"', self.tmp), BLOCK
        )

    def test_redirect_after_a_quoted_regex_is_still_blocked(self):
        self.assertEqual(
            self.bash('grep -E "^(ok|FAIL)|==>" src/a.cs > CLAUDE.md', self.primary), BLOCK
        )

    def test_redirect_after_a_heredoc_marker_is_still_blocked(self):
        command = (
            "cat > CLAUDE.md <<'EOF'\n"
            "not shell syntax >> 18) ^ s\n"
            "EOF\n"
        )
        self.assertEqual(self.bash(command, self.primary), BLOCK)


class ModeArgumentFalsePositiveTests(GuardTestCase):
    """A mode/ownership argument, or an unresolvable scratchpad variable, is not a path."""

    def test_chmod_symbolic_mode_is_not_read_as_a_path(self):
        self.assertEqual(self.bash('chmod +x "$SP/fill.sh"', self.primary), ALLOW)

    def test_chmod_octal_mode_on_a_worktree_path_is_allowed(self):
        target = self.worktree / "src" / "a.cs"
        self.assertEqual(self.bash(f"chmod 755 {target}", self.primary), ALLOW)

    def test_chmod_on_a_path_outside_any_repo_is_allowed(self):
        outside = self.tmp / "scratch.sh"
        self.assertEqual(self.bash(f"chmod +x {outside}", self.primary), ALLOW)

    def test_chown_ownership_spec_is_not_read_as_a_path(self):
        self.assertEqual(self.bash('chown user:group "$SP/fill.sh"', self.primary), ALLOW)

    def test_install_mode_value_is_not_read_as_a_path(self):
        """`755` must be skipped as the mode, not checked as a source/dest path itself."""
        self.assertEqual(
            self.bash('install -m 755 /tmp/src.cs "$SP/dst.cs"', self.primary), ALLOW
        )

    def test_sed_script_argument_is_not_read_as_a_path(self):
        self.assertEqual(
            self.bash("sed -i 's/.../.../' \"$SP/f.md\"", self.primary), ALLOW
        )

    def test_rm_of_a_scratchpad_variable_is_allowed(self):
        self.assertEqual(self.bash('rm -f "$SP/probe.txt"', self.primary), ALLOW)


class ModeArgumentTruePositiveTests(GuardTestCase):
    """The same commands, aimed at a real, resolvable path in the primary checkout."""

    def test_chmod_symbolic_mode_on_primary_path_is_still_blocked(self):
        self.assertEqual(self.bash("chmod +x CLAUDE.md", self.primary), BLOCK)

    def test_chmod_octal_mode_on_primary_path_is_still_blocked(self):
        self.assertEqual(self.bash("chmod 755 src/a.cs", self.primary), BLOCK)

    def test_chown_on_primary_path_is_still_blocked(self):
        self.assertEqual(self.bash("chown user:group src/a.cs", self.primary), BLOCK)

    def test_install_mode_onto_primary_path_is_still_blocked(self):
        self.assertEqual(self.bash("install -m 755 /tmp/x src/a.cs", self.primary), BLOCK)

    def test_sed_script_onto_primary_path_is_still_blocked(self):
        self.assertEqual(
            self.bash("sed -i 's/.../.../' src/a.cs", self.primary), BLOCK
        )

    def test_rm_of_a_real_primary_path_is_still_blocked(self):
        self.assertEqual(self.bash('rm -f src/a.cs', self.primary), BLOCK)

    def test_chmod_of_a_primary_path_from_a_worktree_cwd_is_allowed(self):
        """Sanity check: these commands are only interesting when cwd is the primary."""
        self.assertEqual(self.bash("chmod +x CLAUDE.md", self.worktree), ALLOW)


if __name__ == "__main__":
    unittest.main()
