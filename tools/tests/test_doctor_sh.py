"""Tests for scripts/doctor.sh's authoritative-source detection (#57).

`source.local.json` is gitignored and lives in the primary checkout. doctor.sh used to
look for it next to itself -- the checkout it happened to be running from -- so it
reported "not configured" from a worktree even though the primary checkout had it
configured moments earlier, while `./scripts/doctor.sh` in the primary checkout agreed
it was fine. This is the same defect class, and now reuses the same fix
(scripts/lib/dotnet-env.sh's deckard_primary_checkout_root), as #33's `.dotnet/`
resolution -- see test_dotnet_env.py's WorktreeFallbackTests for that half.

These build a real, independent primary checkout + linked worktree (not the one this
suite itself runs in), with real copies of doctor.sh, dotnet-env.sh and
source-slice.py, so the whole "Authoritative source" section is exercised end to end
rather than any one function in isolation. `SR6_CORE_PDF` is deliberately unset in every
test: this environment has it set, and a consistent report for that reason would prove
nothing about `source.local.json` specifically.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCTOR = ROOT / "scripts" / "doctor.sh"
DOTNET_ENV = ROOT / "scripts" / "lib" / "dotnet-env.sh"
SOURCE_SLICE = ROOT / "tools" / "source-slice.py"


class AuthoritativeSourceWorktreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="deckard-doctor-"))
        cls.primary = cls.tmp / "primary"
        (cls.primary / "scripts" / "lib").mkdir(parents=True)
        (cls.primary / "tools").mkdir(parents=True)
        (cls.primary / ".github").mkdir(parents=True)
        # Real copies of the actual implementation, not stand-ins, so a future edit to
        # any of the three is exercised by these tests too.
        shutil.copy(DOCTOR, cls.primary / "scripts" / "doctor.sh")
        shutil.copy(DOTNET_ENV, cls.primary / "scripts" / "lib" / "dotnet-env.sh")
        shutil.copy(SOURCE_SLICE, cls.primary / "tools" / "source-slice.py")
        (cls.primary / ".github" / "source-manifest.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "sources": [
                        {
                            "sourceId": "sr6-core",
                            "authority": 1,
                            "title": "Fixture Book",
                            "edition": "Fixture Edition",
                            "sha256": "0" * 64,
                            "pdfPageCount": 5,
                            "pageNumbering": {"printedPageEqualsPdfPageMinus": 1},
                            "envVar": "SR6_CORE_PDF",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        run = lambda *a: subprocess.run(a, cwd=cls.primary, check=True, capture_output=True)
        run("git", "init", "-q", "-b", "main")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "T")
        run(
            "git", "add",
            "scripts/doctor.sh", "scripts/lib/dotnet-env.sh",
            "tools/source-slice.py", ".github/source-manifest.json",
        )
        run("git", "commit", "-qm", "seed")

        # A real linked worktree, checked out from the same commit -- it gets its own
        # copy of every file above at the same relative path, exactly like
        # tools/dispatch-agent.sh produces.
        cls.worktree = cls.tmp / "worktree"
        run("git", "worktree", "add", "-q", "-b", "issue-1-x", str(cls.worktree))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _authoritative_source_section(self, checkout: Path) -> str:
        env = dict(os.environ)
        env.pop("SR6_CORE_PDF", None)
        result = subprocess.run(
            ["bash", str(checkout / "scripts" / "doctor.sh")],
            cwd=checkout, capture_output=True, text=True, env=env, check=False,
        )
        lines = result.stdout.splitlines()
        self.assertIn("Authoritative source", lines, result.stdout)
        start = lines.index("Authoritative source")
        end = next(
            (i for i in range(start + 1, len(lines)) if not lines[i].strip()), len(lines)
        )
        return "\n".join(lines[start:end])

    def _write_local_config(self, pdf_path: str = "/tmp/does-not-matter.pdf") -> Path:
        local_config = self.primary / "source.local.json"
        local_config.write_text(json.dumps({"sr6-core": pdf_path}), encoding="utf-8")
        self.addCleanup(local_config.unlink, missing_ok=True)
        return local_config

    def test_configured_source_is_visible_from_the_worktree(self):
        self._write_local_config()
        primary_report = self._authoritative_source_section(self.primary)
        worktree_report = self._authoritative_source_section(self.worktree)
        self.assertNotIn("not configured", primary_report)
        self.assertNotIn("not configured", worktree_report)
        self.assertIn("source.local.json", primary_report)
        self.assertIn("source.local.json", worktree_report)

    def test_missing_source_is_reported_consistently_from_both(self):
        """No source.local.json anywhere: absence must read the same from both places."""
        primary_report = self._authoritative_source_section(self.primary)
        worktree_report = self._authoritative_source_section(self.worktree)
        self.assertIn("not configured", primary_report)
        self.assertIn("not configured", worktree_report)

    def test_worktree_report_names_the_primary_checkout_it_reads_from(self):
        """#57's other half: doctor.sh must say plainly where it looked, not just agree."""
        worktree_report = self._authoritative_source_section(self.worktree)
        self.assertIn(str(self.primary), worktree_report)

    def test_primary_report_does_not_print_the_worktree_aside(self):
        """The primary checkout IS where source.local.json lives; no aside is needed."""
        primary_report = self._authoritative_source_section(self.primary)
        self.assertNotIn("is read from the primary checkout", primary_report)

    def test_hint_names_the_primary_checkout_path_when_unconfigured(self):
        """The create-it hint must point at a real, reachable path, not "next to me"."""
        worktree_report = self._authoritative_source_section(self.worktree)
        self.assertIn(str(self.primary / "source.local.json"), worktree_report)


if __name__ == "__main__":
    unittest.main()
