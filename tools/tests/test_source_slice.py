"""Tests for tools/source-slice.py -- the authoritative-source boundary.

These test the CLI contract by running the real script as a subprocess, because the
contract that matters is what an agent invoking the tool actually experiences.

The invariant under test throughout: the tool must REFUSE rather than approximate.
A wrong hash, a missing file, an out-of-range page, or an absent anchor must all
produce a loud non-zero failure that extracts nothing -- never a quiet partial packet.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pdfbuild import make_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "source-slice.py"

PAGES = [
    "ALPHA PAGE ONE Success Test",
    "BRAVO PAGE TWO Dice Pool",
    "CHARLIE PAGE THREE Glitch Table",
    "DELTA PAGE FOUR Edge Actions",
    "ECHO PAGE FIVE Damage Resistance",
]


NEEDS_PDFTOTEXT = unittest.skipIf(
    shutil.which("pdftotext") is None, "poppler-utils not installed"
)


class SourceSliceTests(unittest.TestCase):
    """Each test gets its own tmp source + tmp manifest; nothing touches the real ones."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="deckard-slice-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.pdf = self.tmp / "fixture.pdf"
        self.pdf.write_bytes(make_pdf(PAGES))
        self.sha = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        self.manifest = self._write_manifest(self.sha)

    def _write_manifest(self, sha: str, *, page_count: int = len(PAGES), offset: int = 1) -> Path:
        path = self.tmp / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "sources": [
                        {
                            "sourceId": "sr6-core",
                            "authority": 1,
                            "title": "Fixture Book",
                            "edition": "Fixture Edition",
                            "sha256": sha,
                            "pdfPageCount": page_count,
                            "pageNumbering": {"printedPageEqualsPdfPageMinus": offset},
                            "envVar": "SR6_CORE_PDF",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def run_tool(self, *args: str, source: str | None = "", manifest: Path | None = None):
        env = dict(os.environ)
        env["DECKARD_SOURCE_MANIFEST"] = str(manifest or self.manifest)
        env["DECKARD_ALLOW_TEST_MANIFEST"] = "1"
        env["SR6_CORE_PDF"] = str(self.pdf) if source == "" else (source or "")
        if not env["SR6_CORE_PDF"]:
            env.pop("SR6_CORE_PDF")
        return subprocess.run(
            [sys.executable, str(TOOL), *args],
            capture_output=True, text=True, env=env, cwd=str(ROOT), check=False,
        )

    # ------------------------------------------------------------------ happy paths

    @NEEDS_PDFTOTEXT
    def test_verify_only_accepts_the_pinned_hash(self):
        result = self.run_tool("--verify-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sha256 verified", result.stdout)

    @NEEDS_PDFTOTEXT
    def test_extracts_exactly_the_requested_pages(self):
        result = self.run_tool("--pages", "2-3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BRAVO PAGE TWO", result.stdout)
        self.assertIn("CHARLIE PAGE THREE", result.stdout)
        self.assertNotIn("ALPHA PAGE ONE", result.stdout)
        self.assertNotIn("DELTA PAGE FOUR", result.stdout)

    @NEEDS_PDFTOTEXT
    def test_single_page_range_is_accepted(self):
        result = self.run_tool("--pages", "4")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DELTA PAGE FOUR", result.stdout)
        self.assertNotIn("CHARLIE PAGE THREE", result.stdout)

    @NEEDS_PDFTOTEXT
    def test_printed_pages_are_converted_using_the_manifest_offset(self):
        # Offset 1: printed p.2 is PDF p.3.
        result = self.run_tool("--printed-pages", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CHARLIE PAGE THREE", result.stdout)
        self.assertIn("pdf pages       : 3-3", result.stdout)
        self.assertIn("printed pages   : 2-2", result.stdout)

    @NEEDS_PDFTOTEXT
    def test_layout_mode_still_extracts(self):
        result = self.run_tool("--pages", "1", "--layout")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"argv\s+: pdftotext -f 1 -l 1 -layout \S+ -")

    @NEEDS_PDFTOTEXT
    def test_output_file_is_written(self):
        out = self.tmp / "packet.txt"
        result = self.run_tool("--pages", "1", "--output", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ALPHA PAGE ONE", out.read_text(encoding="utf-8"))

    # -------------------------------------------------------------- provenance

    @NEEDS_PDFTOTEXT
    def test_packet_header_carries_full_provenance(self):
        result = self.run_tool("--pages", "1")
        for expected in ("sourceId        : sr6-core", "edition         : Fixture Edition",
                         f"sha256          : {self.sha}", "pdf pages       : 1-1"):
            self.assertIn(expected, result.stdout)

    @NEEDS_PDFTOTEXT
    def test_packet_warns_against_committing_it(self):
        result = self.run_tool("--pages", "1")
        self.assertIn("never commit", result.stdout.lower())

    # ---------------------------------------------------------- extraction provenance
    #
    # Up to now source packets were convenience; from here on (see Issue #40) they are
    # evidence a rules-conformance review is conducted against, so the header must
    # attest to its own derivation -- not just the source PDF's hash, which extraction
    # provenance says nothing about.

    @NEEDS_PDFTOTEXT
    def test_packet_header_carries_extraction_provenance(self):
        result = self.run_tool("--pages", "1", "--layout")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("extractor       : pdftotext", result.stdout)
        self.assertRegex(result.stdout, r"extractorVersion: \S+")
        self.assertRegex(result.stdout, r"argv\s+: pdftotext -f 1 -l 1 -layout \S+ -")
        self.assertRegex(result.stdout, r"bodySha256\s+: [0-9a-f]{64}")

    @NEEDS_PDFTOTEXT
    def test_argv_does_not_leak_the_local_source_path(self):
        """The path is real (a tmpdir under self.tmp); only its basename may appear.

        scripts/doctor.sh already refuses to print Brandon's full local path because
        doctor output gets pasted into Issues -- packets get read and quoted from too,
        even though they are never committed, so the same redaction applies here.
        """
        result = self.run_tool("--pages", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(self.tmp), result.stdout)
        self.assertIn(self.pdf.name, result.stdout)

    @NEEDS_PDFTOTEXT
    def test_body_sha256_in_header_matches_the_actual_extracted_body(self):
        """bodySha256 must actually verify something, not just be present.

        The header's claim is checked against an independently reproduced extraction
        (calling the tool's own extract()/hash_body() directly), not against a second
        read of the same subprocess output -- a test that only re-parsed its own fixture
        would prove the field is well-formed, not that it is correct.
        """
        result = self.run_tool("--pages", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        match = re.search(r"bodySha256\s*: ([0-9a-f]{64})", result.stdout)
        self.assertIsNotNone(match, result.stdout)

        module = self._load_tool()
        argv = module.build_argv(self.pdf, 2, 2, False)
        body = module.extract(argv)
        self.assertEqual(match.group(1), module.hash_body(body))

    @NEEDS_PDFTOTEXT
    def test_regenerating_the_same_slice_reproduces_the_same_body_hash(self):
        first = self.run_tool("--pages", "3")
        second = self.run_tool("--pages", "3")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        h1 = re.search(r"bodySha256\s*: ([0-9a-f]{64})", first.stdout).group(1)
        h2 = re.search(r"bodySha256\s*: ([0-9a-f]{64})", second.stdout).group(1)
        self.assertEqual(h1, h2)

    @NEEDS_PDFTOTEXT
    def test_different_pages_produce_different_body_hashes(self):
        a = self.run_tool("--pages", "1")
        b = self.run_tool("--pages", "2")
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(b.returncode, 0, b.stderr)
        ha = re.search(r"bodySha256\s*: ([0-9a-f]{64})", a.stdout).group(1)
        hb = re.search(r"bodySha256\s*: ([0-9a-f]{64})", b.stdout).group(1)
        self.assertNotEqual(ha, hb)

    def test_hash_body_changes_when_the_body_is_deliberately_altered(self):
        """A field nothing verifies is decoration, not evidence (Issue #40).

        Hermetic and unconditional -- no pdftotext required -- because the property
        under test is hash_body() itself, not the extraction pipeline around it: a
        single deliberately altered character must change bodySha256, and the value
        recorded must be exactly sha256 of the body's UTF-8 bytes, not some other
        derived quantity.
        """
        module = self._load_tool()
        original = "ALPHA PAGE ONE Success Test\n"
        altered = original.replace("Success", "Suxcess")
        self.assertNotEqual(module.hash_body(original), module.hash_body(altered))
        self.assertEqual(
            module.hash_body(original),
            hashlib.sha256(original.encode("utf-8")).hexdigest(),
        )

    # ------------------------------------------------------------------ refusals

    @NEEDS_PDFTOTEXT
    def test_hash_mismatch_refuses_and_extracts_nothing(self):
        wrong = self._write_manifest("0" * 64)
        result = self.run_tool("--pages", "1", manifest=wrong)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HASH MISMATCH", result.stderr)
        self.assertNotIn("ALPHA PAGE ONE", result.stdout)

    @NEEDS_PDFTOTEXT
    def test_hash_mismatch_is_checked_before_extraction(self):
        wrong = self._write_manifest("0" * 64)
        result = self.run_tool("--verify-only", manifest=wrong)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    @NEEDS_PDFTOTEXT
    def test_unconfigured_source_refuses(self):
        result = self.run_tool("--pages", "1", source=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not configured", result.stderr)

    @NEEDS_PDFTOTEXT
    def test_nonexistent_source_path_refuses(self):
        result = self.run_tool("--pages", "1", source=str(self.tmp / "nope.pdf"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)

    @NEEDS_PDFTOTEXT
    def test_relative_source_path_refuses(self):
        result = self.run_tool("--pages", "1", source="relative/path.pdf")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("absolute path", result.stderr)

    @NEEDS_PDFTOTEXT
    def test_unknown_source_id_refuses(self):
        result = self.run_tool("--pages", "1", "--source-id", "sr6-supplement")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown sourceId", result.stderr)

    @NEEDS_PDFTOTEXT
    def test_out_of_range_pages_refuse(self):
        result = self.run_tool("--pages", "4-9")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has 5 pages", result.stderr)

    @NEEDS_PDFTOTEXT
    def test_inverted_range_refuses(self):
        result = self.run_tool("--pages", "4-2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inverted", result.stderr)

    @NEEDS_PDFTOTEXT
    def test_malformed_range_refuses(self):
        result = self.run_tool("--pages", "forty-two")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expects N or N-M", result.stderr)

    @NEEDS_PDFTOTEXT
    def test_missing_anchor_refuses(self):
        result = self.run_tool("--pages", "1", "--expect", "Vehicle Handling Table")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected anchor", result.stderr)

    @NEEDS_PDFTOTEXT
    def test_present_anchor_passes(self):
        result = self.run_tool("--pages", "1", "--expect", "Success Test")
        self.assertEqual(result.returncode, 0, result.stderr)

    @NEEDS_PDFTOTEXT
    def test_all_anchors_must_be_present(self):
        result = self.run_tool("--pages", "1", "--expect", "Success Test", "--expect", "Glitch")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Glitch", result.stderr)

    @NEEDS_PDFTOTEXT
    def test_oversize_packet_always_refuses(self):
        """There is no opt-in left: a request above MAX_PAGES refuses, full stop (#42)."""
        big = self._write_manifest(self.sha, page_count=400)
        result = self.run_tool("--pages", "1-40", manifest=big)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to extract", result.stderr)

    def test_there_is_no_allow_large_escape_hatch(self):
        """`--allow-large` is gone entirely, not merely undocumented (#42).

        It used to impose no ceiling of its own: passing it let a single packet
        request extract the entire 322-page baseline. Removing the flag -- rather
        than giving it a second, larger ceiling -- is what makes "packets are
        bounded to 24 pages" (docs/source-handling.md) true of the tool as shipped.
        Nothing in the repository (agent tooling, docs, or the source-packet skill)
        ever invoked it.
        """
        help_text = subprocess.run(
            [sys.executable, str(TOOL), "--help"], capture_output=True, text=True, check=False
        ).stdout
        self.assertNotIn("--allow-large", help_text)

    @NEEDS_PDFTOTEXT
    def test_oversize_packet_refuses_even_if_the_old_flag_is_passed(self):
        """Regression guard for the exact bug #42 reports.

        Before this fix, `--allow-large` made check_packet_size's ceiling
        unconditional-bypass-able with no ceiling of its own. This asserts a
        request above MAX_PAGES is refused even when a caller still passes the
        now-nonexistent flag -- it must fail as an unrecognized argument, never as
        a silently accepted, unbounded extraction.
        """
        big = self._write_manifest(self.sha, page_count=400)
        result = self.run_tool("--pages", "1-40", "--allow-large", manifest=big)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments", result.stderr)
        self.assertIn("--allow-large", result.stderr)

    @NEEDS_PDFTOTEXT
    def test_no_page_selection_refuses(self):
        result = self.run_tool()
        self.assertNotEqual(result.returncode, 0)

    # -------------------------------------------- the source-substitution boundary

    def test_there_is_no_file_argument(self):
        """The absence of --file is a load-bearing invariant, not an oversight.

        If this ever passes, an agent can point the tool at a different printing, a
        previous edition, a supplement, or a pirated scan, and every downstream
        provenance claim in the repository becomes unverifiable.
        """
        help_text = subprocess.run(
            [sys.executable, str(TOOL), "--help"], capture_output=True, text=True, check=False
        ).stdout
        self.assertNotIn("--file", help_text)
        self.assertNotIn("--source-path", help_text)
        self.assertNotIn("--pdf", help_text)

    @NEEDS_PDFTOTEXT
    def test_manifest_override_requires_explicit_opt_in(self):
        env = dict(os.environ)
        env["DECKARD_SOURCE_MANIFEST"] = str(self.manifest)
        env.pop("DECKARD_ALLOW_TEST_MANIFEST", None)
        env["SR6_CORE_PDF"] = str(self.pdf)
        result = subprocess.run(
            [sys.executable, str(TOOL), "--verify-only"],
            capture_output=True, text=True, env=env, cwd=str(ROOT), check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DECKARD_ALLOW_TEST_MANIFEST", result.stderr)

    @NEEDS_PDFTOTEXT
    def test_override_packets_are_stamped_non_authoritative(self):
        result = self.run_tool("--pages", "1")
        self.assertIn("NON-AUTHORITATIVE", result.stdout)

    def test_packets_from_the_real_manifest_carry_no_override_stamp(self):
        """Assert the behaviour the name promises, rather than the manifest's shape.

        The previous version of this test never generated a packet and never looked for
        a stamp -- it checked three manifest fields and was named for something else
        entirely. build_header is called directly here so the assertion is hermetic: the
        authoritative-source path is what makes a real packet impossible in CI.
        """
        header = self._load_tool().build_header(
            {
                "sourceId": "sr6-core",
                "title": "Fixture Book",
                "edition": "Fixture Edition",
                "sha256": "a" * 64,
                "pageNumbering": {"printedPageEqualsPdfPageMinus": 1},
            },
            first=10, last=11, layout=False, is_override=False,
            extractor_version="24.02.0", argv_display=["pdftotext", "-f", "10", "-l", "11"],
            body_sha256="b" * 64,
        )
        self.assertNotIn("NON-AUTHORITATIVE", header)
        self.assertIn("sourceId        : sr6-core", header)

    def test_packets_from_a_test_manifest_are_stamped(self):
        header = self._load_tool().build_header(
            {
                "sourceId": "sr6-core",
                "title": "Fixture Book",
                "edition": "Fixture Edition",
                "sha256": "a" * 64,
                "pageNumbering": {"printedPageEqualsPdfPageMinus": 1},
            },
            first=10, last=11, layout=False, is_override=True,
            extractor_version="24.02.0", argv_display=["pdftotext", "-f", "10", "-l", "11"],
            body_sha256="b" * 64,
        )
        self.assertIn("NON-AUTHORITATIVE", header)

    def test_committed_manifest_has_the_expected_shape(self):
        """Split out from the test above, which was named for stamping and did this."""
        real = json.loads((ROOT / ".github" / "source-manifest.json").read_text(encoding="utf-8"))
        entry = real["sources"][0]
        self.assertEqual(entry["sourceId"], "sr6-core")
        self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(entry["pdfPageCount"], 0)

    @staticmethod
    def _load_tool():
        import importlib.util

        spec = importlib.util.spec_from_file_location("source_slice", TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class WorktreeLocalConfigTests(unittest.TestCase):
    """#57: `source.local.json` lives in the primary checkout; every worktree must see it.

    The tool used to resolve `source.local.json` against its own script's parent
    directory -- the checkout it happened to be running from. A worktree is its own
    root, so a file created in the primary checkout was invisible from every worktree,
    which is the one place CLAUDE.md requires implementation to happen.

    This builds a real, independent primary checkout + linked worktree (not the one
    this test suite itself runs in) with its own copy of the real script, so resolution
    is proven against git's actual `--git-common-dir` semantics rather than assumed.
    `SR6_CORE_PDF` is deliberately unset in every test here: this environment has it
    set, and slicing succeeding for that reason would prove nothing about
    `source.local.json` specifically.
    """

    @classmethod
    def setUpClass(cls) -> None:
        # `resolve_source_path` deliberately skips the source.local.json fallback
        # entirely under a test-manifest override (DECKARD_SOURCE_MANIFEST), so it
        # cannot be used here -- that would test the wrong thing. A real, uncommitted
        # `.github/source-manifest.json` is committed into the fake repo instead, so
        # `DEFAULT_MANIFEST` (resolved from each checkout's own ROOT) finds it exactly
        # as it would in the real repository, in both the primary and the worktree.
        cls.tmp = Path(tempfile.mkdtemp(prefix="deckard-slice-worktree-"))
        cls.primary = cls.tmp / "primary"
        (cls.primary / "tools").mkdir(parents=True)
        (cls.primary / ".github").mkdir(parents=True)
        # A real copy of the actual implementation, not a stand-in, so a future edit to
        # source-slice.py is exercised by these tests too.
        shutil.copy(TOOL, cls.primary / "tools" / "source-slice.py")

        cls.pdf = cls.tmp / "fixture.pdf"
        cls.pdf.write_bytes(make_pdf(PAGES))
        sha = hashlib.sha256(cls.pdf.read_bytes()).hexdigest()
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
                            "sha256": sha,
                            "pdfPageCount": len(PAGES),
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
        run("git", "add", "tools/source-slice.py", ".github/source-manifest.json")
        run("git", "commit", "-qm", "seed")
        cls.worktree = cls.tmp / "worktree"
        run("git", "worktree", "add", "-q", "-b", "issue-1-x", str(cls.worktree))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, script: Path, cwd: Path, *args: str):
        env = dict(os.environ)
        env.pop("SR6_CORE_PDF", None)
        env.pop("DECKARD_SOURCE_MANIFEST", None)
        env.pop("DECKARD_ALLOW_TEST_MANIFEST", None)
        return subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, env=env, cwd=str(cwd), check=False,
        )

    def _write_local_config(self) -> Path:
        local_config = self.primary / "source.local.json"
        local_config.write_text(json.dumps({"sr6-core": str(self.pdf)}), encoding="utf-8")
        self.addCleanup(local_config.unlink, missing_ok=True)
        return local_config

    def test_source_local_json_in_primary_is_found_from_the_worktree(self):
        self._write_local_config()
        result = self._run(
            self.worktree / "tools" / "source-slice.py", self.worktree, "--verify-only"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sha256 verified", result.stdout)

    def test_source_local_json_in_primary_is_still_found_from_the_primary_itself(self):
        """Regression: centralising resolution must not break the primary checkout too."""
        self._write_local_config()
        result = self._run(
            self.primary / "tools" / "source-slice.py", self.primary, "--verify-only"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sha256 verified", result.stdout)

    def test_missing_source_local_json_still_refuses_from_the_worktree(self):
        """No accidental fail-open: absence must still refuse, loudly, from a worktree."""
        result = self._run(
            self.worktree / "tools" / "source-slice.py", self.worktree, "--verify-only"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not configured", result.stderr)

    def _run_with_env_var(self, script: Path, cwd: Path, pdf: Path):
        env = dict(os.environ)
        env["SR6_CORE_PDF"] = str(pdf)
        env.pop("DECKARD_SOURCE_MANIFEST", None)
        env.pop("DECKARD_ALLOW_TEST_MANIFEST", None)
        return subprocess.run(
            [sys.executable, str(script), "--verify-only"],
            capture_output=True, text=True, env=env, cwd=str(cwd), check=False,
        )

    def test_env_var_still_works_unchanged_from_the_worktree(self):
        """SR6_CORE_PDF keeps working regardless of source.local.json -- #57 is a non-goal
        for the env-var path, and this must stay true even with no source.local.json at all.
        """
        result = self._run_with_env_var(
            self.worktree / "tools" / "source-slice.py", self.worktree, self.pdf
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sha256 verified", result.stdout)

    def test_env_var_takes_precedence_over_source_local_json(self):
        """Unchanged precedence: the env var wins even when source.local.json also exists."""
        self._write_local_config()
        result = self._run_with_env_var(
            self.worktree / "tools" / "source-slice.py", self.worktree, self.pdf
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sha256 verified", result.stdout)


if __name__ == "__main__":
    unittest.main()
