"""Tests for tools/repo-checks.py.

A checker that has never caught anything is indistinguishable from a checker that
cannot catch anything. Every test here builds a fixture repository that violates one
invariant and asserts the check fails on it, then asserts the clean variant passes.
"""
from __future__ import annotations

import importlib.util
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("repo_checks", ROOT / "tools" / "repo-checks.py")
assert _spec and _spec.loader
repo_checks = importlib.util.module_from_spec(_spec)
sys.modules["repo_checks"] = repo_checks
_spec.loader.exec_module(repo_checks)


class FixtureRepo:
    """A throwaway git repository, because several checks read `git ls-files`."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="deckard-checks-"))
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)

    def write(self, rel: str, content: bytes | str, *, track: bool = True) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        if track:
            subprocess.run(["git", "add", "-f", rel], cwd=self.root, check=True)
        return path

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class CheckTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = FixtureRepo()
        self.addCleanup(self.repo.cleanup)

    def assertCaught(self, failures, needle: str) -> None:
        self.assertTrue(failures, f"expected a failure mentioning {needle!r}, got none")
        self.assertTrue(
            any(needle in f for f in failures),
            f"expected {needle!r} in {failures}",
        )


class TextHygieneTests(CheckTestCase):
    def test_clean_file_passes(self):
        self.repo.write("a.md", "hello\n")
        self.assertEqual(repo_checks.check_text_hygiene(self.repo.root), [])

    def test_bom_is_caught(self):
        self.repo.write("a.md", b"\xef\xbb\xbfhello\n")
        self.assertCaught(repo_checks.check_text_hygiene(self.repo.root), "BOM")

    def test_crlf_is_caught(self):
        self.repo.write("a.md", b"hello\r\nworld\n")
        self.assertCaught(repo_checks.check_text_hygiene(self.repo.root), "CRLF")

    def test_missing_trailing_newline_is_caught(self):
        self.repo.write("a.md", b"hello")
        self.assertCaught(repo_checks.check_text_hygiene(self.repo.root), "missing trailing newline")

    def test_double_trailing_newline_is_caught(self):
        self.repo.write("a.md", b"hello\n\n")
        self.assertCaught(repo_checks.check_text_hygiene(self.repo.root), "more than one trailing")

    def test_binary_suffixes_are_ignored(self):
        self.repo.write("logo.png", b"\x89PNG\r\n\x1a\n\xff")
        self.assertEqual(repo_checks.check_text_hygiene(self.repo.root), [])


class DeterminismTests(CheckTestCase):
    CLEAN = "namespace Deckard.Core;\npublic sealed class Roller { }\n"

    def test_clean_engine_source_passes(self):
        self.repo.write("src/Deckard.Core/Roller.cs", self.CLEAN)
        self.assertEqual(repo_checks.check_determinism(self.repo.root), [])

    def test_random_shared_is_caught(self):
        self.repo.write("src/Deckard.Core/R.cs", "var x = Random.Shared.Next();\n")
        self.assertCaught(repo_checks.check_determinism(self.repo.root), "Random.Shared")

    def test_new_random_is_caught(self):
        self.repo.write("src/Deckard.Core/R.cs", "var r = new Random(42);\n")
        self.assertCaught(repo_checks.check_determinism(self.repo.root), "IRandomSource")

    def test_datetime_now_is_caught(self):
        self.repo.write("src/Deckard.Rules/R.cs", "var t = DateTime.UtcNow;\n")
        self.assertCaught(repo_checks.check_determinism(self.repo.root), "ambient clock")

    def test_guid_newguid_is_caught(self):
        self.repo.write("src/Deckard.Core/R.cs", "var id = Guid.NewGuid();\n")
        self.assertCaught(repo_checks.check_determinism(self.repo.root), "non-reproducible")

    def test_parallelism_is_caught(self):
        self.repo.write("src/Deckard.Rules/R.cs", "items.AsParallel().Select(x => x);\n")
        self.assertCaught(repo_checks.check_determinism(self.repo.root), "non-deterministic")

    def test_tests_are_not_scanned(self):
        """Test code may legitimately construct a seeded Random to build fixtures."""
        self.repo.write("tests/Deckard.Core.Tests/T.cs", "var r = new Random(1);\n")
        self.assertEqual(repo_checks.check_determinism(self.repo.root), [])

    def test_explicit_marker_allows_an_exception(self):
        self.repo.write(
            "src/Deckard.Core/R.cs",
            "var t = DateTime.UtcNow; // deckard:allow-nondeterminism diagnostics only\n",
        )
        self.assertEqual(repo_checks.check_determinism(self.repo.root), [])

    def test_generated_obj_output_is_skipped(self):
        self.repo.write("src/Deckard.Core/obj/Debug/G.cs", "var x = Random.Shared.Next();\n")
        self.assertEqual(repo_checks.check_determinism(self.repo.root), [])


class LayeringTests(CheckTestCase):
    def _project(self, name: str, refs: list[str]) -> None:
        # Each project is written at its real location (PROJECT_DIRS), not assumed to be
        # under src/ -- Deckard.Testing lives under tests/ instead, and this fixture
        # exercises the same lookup check_layering itself uses.
        location = repo_checks.PROJECT_DIRS.get(name, f"src/{name}")
        body = "\n".join(
            f'    <ProjectReference Include="../../{repo_checks.PROJECT_DIRS.get(r, f"src/{r}")}/{r}.csproj" />'
            for r in refs
        )
        self.repo.write(
            f"{location}/{name}.csproj",
            f"<Project Sdk=\"Microsoft.NET.Sdk\">\n  <ItemGroup>\n{body}\n  </ItemGroup>\n</Project>\n",
        )

    def _all_projects(self, **overrides: list[str]) -> None:
        """Write every project in ALLOWED_PROJECT_REFS with its correct graph, except
        for names present in `overrides`, which get the given (possibly violating) refs
        instead. Keeps each test focused on the one edge it is checking."""
        graph = {
            "Deckard.Core": [],
            "Deckard.Data": ["Deckard.Core"],
            "Deckard.Rules": ["Deckard.Core", "Deckard.Data"],
            "Deckard.Testing": ["Deckard.Core"],
        }
        graph.update(overrides)
        for name, refs in graph.items():
            self._project(name, refs)

    def test_declared_graph_matching_the_spec_passes(self):
        self._all_projects()
        self.assertEqual(repo_checks.check_layering(self.repo.root), [])

    def test_core_depending_upward_is_caught(self):
        self._all_projects(**{"Deckard.Core": ["Deckard.Rules"]})
        self.assertCaught(repo_checks.check_layering(self.repo.root), "Deckard.Core declares forbidden")

    def test_data_depending_on_rules_is_caught(self):
        self._all_projects(**{"Deckard.Data": ["Deckard.Core", "Deckard.Rules"]})
        self.assertCaught(repo_checks.check_layering(self.repo.root), "Deckard.Data declares forbidden")

    def test_missing_project_is_caught(self):
        self._project("Deckard.Core", [])
        self.assertCaught(repo_checks.check_layering(self.repo.root), "missing expected project")

    def test_src_project_referencing_testing_is_caught(self):
        """The load-bearing rule this Issue adds: nothing under src/ may pull in the
        test-support project, or the type it carries walks straight back into the
        shipped graph."""
        self._all_projects(**{"Deckard.Rules": ["Deckard.Core", "Deckard.Data", "Deckard.Testing"]})
        self.assertCaught(repo_checks.check_layering(self.repo.root), "Deckard.Rules declares forbidden")

    def test_testing_project_referencing_anything_but_core_is_caught(self):
        """Deckard.Testing may see Core and nothing else -- not Data, not Rules."""
        self._all_projects(**{"Deckard.Testing": ["Deckard.Core", "Deckard.Data"]})
        self.assertCaught(repo_checks.check_layering(self.repo.root), "Deckard.Testing declares forbidden")


class CoreFilesystemBoundaryTests(CheckTestCase):
    """See ADR 0005 / Issue #38: Deckard.Core may not touch the filesystem (ADR 0001)."""

    def test_clean_core_source_passes(self):
        self.repo.write(
            "src/Deckard.Core/Replay/RandomAlgorithmId.cs",
            "namespace Deckard.Core.Replay;\npublic readonly record struct RandomAlgorithmId(string Name);\n",
        )
        self.assertEqual(repo_checks.check_core_filesystem_boundary(self.repo.root), [])

    def test_file_readalltext_is_caught(self):
        self.repo.write(
            "src/Deckard.Core/Leak.cs",
            "var text = File.ReadAllText(path);\n",
        )
        self.assertCaught(
            repo_checks.check_core_filesystem_boundary(self.repo.root), "touch the filesystem"
        )

    def test_fully_qualified_system_io_is_caught(self):
        self.repo.write(
            "src/Deckard.Core/Leak.cs",
            "var text = System.IO.File.ReadAllText(path);\n",
        )
        self.assertCaught(
            repo_checks.check_core_filesystem_boundary(self.repo.root), "touch the filesystem"
        )

    def test_directory_enumeration_is_caught(self):
        self.repo.write(
            "src/Deckard.Core/Leak.cs",
            "foreach (var f in Directory.GetFiles(root)) { }\n",
        )
        self.assertCaught(
            repo_checks.check_core_filesystem_boundary(self.repo.root), "touch the filesystem"
        )

    def test_streamreader_is_caught(self):
        self.repo.write(
            "src/Deckard.Core/Leak.cs",
            "using var reader = new StreamReader(path);\n",
        )
        self.assertCaught(
            repo_checks.check_core_filesystem_boundary(self.repo.root), "touch the filesystem"
        )

    def test_prose_mentioning_the_boundary_is_not_caught(self):
        """A doc comment explaining this exact rule must not trip the rule it explains --
        this is the ADR 0005 review finding: Core's own comments legitimately say things
        like 'Core touches no filesystem' and 'System.IO.File', which must stay legible
        without becoming false positives."""
        self.repo.write(
            "src/Deckard.Core/Replay/SourceBaselineId.cs",
            "namespace Deckard.Core.Replay;\n\n"
            "/// <summary>\n"
            "/// A value passed in, never read: Deckard.Core touches no filesystem, so this\n"
            "/// type has no knowledge of System.IO.File or where the manifest lives.\n"
            "/// </summary>\n"
            "public readonly record struct SourceBaselineId(string SourceId);\n",
        )
        self.assertEqual(repo_checks.check_core_filesystem_boundary(self.repo.root), [])

    def test_data_project_is_not_scanned(self):
        """Data's structured-data loaders will legitimately read files; only Core is banned."""
        self.repo.write(
            "src/Deckard.Data/Loader.cs",
            "var text = File.ReadAllText(path);\n",
        )
        self.assertEqual(repo_checks.check_core_filesystem_boundary(self.repo.root), [])

    def test_tests_are_not_scanned(self):
        self.repo.write(
            "tests/Deckard.Core.Tests/T.cs",
            "var text = File.ReadAllText(path);\n",
        )
        self.assertEqual(repo_checks.check_core_filesystem_boundary(self.repo.root), [])

    def test_generated_obj_output_is_skipped(self):
        self.repo.write(
            "src/Deckard.Core/obj/Debug/Deckard.Core.GlobalUsings.g.cs",
            "global using System.IO;\n",
        )
        self.assertEqual(repo_checks.check_core_filesystem_boundary(self.repo.root), [])

    def test_trailing_comment_on_a_code_line_is_still_caught(self):
        """Documents a known, deliberate limitation rather than leaving it unverified:
        CORE_COMMENT_LINE only recognises a whole-line comment. A trailing comment on a
        code line is not stripped first, so it is scanned along with the code and can
        still trip the check -- unlike a comment occupying its own line, which
        test_prose_mentioning_the_boundary_is_not_caught proves is safe."""
        self.repo.write(
            "src/Deckard.Core/Leak.cs",
            "var x = 1; // mentions File.ReadAllText in passing\n",
        )
        self.assertCaught(
            repo_checks.check_core_filesystem_boundary(self.repo.root), "touch the filesystem"
        )


class SourceBoundaryTests(CheckTestCase):
    def test_clean_repo_passes(self):
        self.repo.write("docs/architecture.md", "Deckard layering.\n")
        self.assertEqual(repo_checks.check_source_boundary(self.repo.root), [])

    def test_tracked_pdf_is_caught(self):
        self.repo.write("reference/core.pdf", b"%PDF-1.4\n")
        self.assertCaught(repo_checks.check_source_boundary(self.repo.root), "tracked in git")

    def test_committed_source_packet_is_caught(self):
        marker = "DECKARD SOURCE" + " PACKET"
        self.repo.write("docs/notes.md", f"{marker} -- pasted rulebook text follows\n")
        self.assertCaught(repo_checks.check_source_boundary(self.repo.root), "extracted source packet")

    def test_leaked_local_source_path_is_caught(self):
        self.repo.write("scripts/run.sh", "export SR6_CORE_PDF=" + "/home/" + "someone/book.pdf\n")
        self.assertCaught(repo_checks.check_source_boundary(self.repo.root), "leaks a local")

    def test_macos_home_path_is_also_caught(self):
        self.repo.write("scripts/run.sh", "export SR6_CORE_PDF=" + "/Users/" + "someone/book.pdf\n")
        self.assertCaught(repo_checks.check_source_boundary(self.repo.root), "leaks a local")

    def test_a_generic_absolute_path_is_fine(self):
        self.repo.write("scripts/run.sh", "cd /usr/share/doc\n")
        self.assertEqual(repo_checks.check_source_boundary(self.repo.root), [])

    def test_manifest_without_valid_hash_is_caught(self):
        self.repo.write(
            ".github/source-manifest.json",
            '{"sources": [{"sourceId": "sr6-core", "sha256": "TODO"}]}\n',
        )
        self.assertCaught(repo_checks.check_source_boundary(self.repo.root), "no valid sha256")

    # ---- regression: the suffix allowlist made this check blind to .txt -------------
    #
    # The original implementation only inspected files whose suffix was in
    # TEXT_SUFFIXES, which has no ".txt" -- the exact extension every document in this
    # repository teaches for source packets. A committed chapter4.txt full of rulebook
    # prose produced no findings at all.

    def test_packet_committed_as_txt_is_caught(self):
        marker = "DECKARD SOURCE" + " PACKET"
        self.repo.write("reference/chapter4.txt", f"{marker}\nrulebook prose here\n")
        self.assertCaught(
            repo_checks.check_source_boundary(self.repo.root), "extracted source packet"
        )

    def test_local_path_committed_as_txt_is_caught(self):
        self.repo.write("reference/config.txt", '{"path": "' + "/home/" + 'x/sr6.pdf"}\n')
        self.assertCaught(repo_checks.check_source_boundary(self.repo.root), "leaks a local")

    def test_extensionless_file_is_inspected(self):
        marker = "DECKARD SOURCE" + " PACKET"
        self.repo.write("notes", f"{marker}\n")
        self.assertCaught(
            repo_checks.check_source_boundary(self.repo.root), "extracted source packet"
        )

    def test_unusual_extensions_are_inspected(self):
        marker = "DECKARD SOURCE" + " PACKET"
        for name in ("a.xml", "b.csv", "c.html", "d.rst", "e.sql", "f.resx", "g.log"):
            with self.subTest(name=name):
                repo = FixtureRepo()
                self.addCleanup(repo.cleanup)
                repo.write(name, f"{marker}\n")
                self.assertCaught(
                    repo_checks.check_source_boundary(repo.root), "extracted source packet"
                )

    def test_binary_files_do_not_crash_the_scan(self):
        self.repo.write("logo.png", b"\x89PNG\r\n\x1a\n\xff\xfe\xfd")
        self.repo.write("blob.bin", b"\xff\xfe\x00\x01binary")
        self.assertEqual(repo_checks.check_source_boundary(self.repo.root), [])

    # ---- regression: exemptions must be per-check, not per-file --------------------

    def test_source_handling_doc_is_subject_to_the_local_path_check(self):
        """It was blanket-exempt, and it is the doc most likely to grow a real path."""
        self.repo.write("docs/source-handling.md", "export SR6_CORE_PDF=" + "/home/" + "x/b.pdf\n")
        self.assertCaught(repo_checks.check_source_boundary(self.repo.root), "leaks a local")

    def test_source_slice_tool_is_still_allowed_to_emit_the_marker(self):
        marker = "DECKARD SOURCE" + " PACKET"
        self.repo.write("tools/source-slice.py", f'HEADER = "{marker}"\n')
        self.assertEqual(repo_checks.check_source_boundary(self.repo.root), [])

    def test_source_slice_tool_is_NOT_exempt_from_the_local_path_check(self):
        """It needs the marker exemption. It has no business holding a local path."""
        self.repo.write("tools/source-slice.py", 'DEFAULT = "' + "/home/" + 'x/b.pdf"\n')
        self.assertCaught(repo_checks.check_source_boundary(self.repo.root), "leaks a local")

    def test_manifest_carrying_a_local_path_is_caught(self):
        self.repo.write(
            ".github/source-manifest.json",
            '{"sources": [{"sourceId": "sr6-core", "sha256": "' + "a" * 64
            + '", "path": "/home/x/b.pdf", "envVar": "SR6_CORE_PDF"}]}\n',
        )
        failures = repo_checks.check_source_boundary(self.repo.root)
        self.assertCaught(failures, "never in git")


class SingleQueueTests(CheckTestCase):
    def test_prose_roadmap_passes(self):
        self.repo.write("docs/roadmap.md", "Phase 1 depends on Phase 0.\n")
        self.assertEqual(repo_checks.check_single_queue(self.repo.root), [])

    def test_checklist_in_a_governing_doc_is_caught(self):
        self.repo.write("CLAUDE.md", "# Deckard\n\n- [ ] implement dice pools\n")
        self.assertCaught(repo_checks.check_single_queue(self.repo.root), "outside GitHub Issues")

    def test_completed_checklist_is_also_caught(self):
        self.repo.write("docs/roadmap.md", "- [x] done thing\n")
        self.assertCaught(repo_checks.check_single_queue(self.repo.root), "outside GitHub Issues")

    def test_bullet_lists_are_fine(self):
        self.repo.write("README.md", "- Deckard is a rules engine\n- It is deterministic\n")
        self.assertEqual(repo_checks.check_single_queue(self.repo.root), [])


class ActionPinTests(CheckTestCase):
    """A mutable action tag is remote code execution with this repo's workflow token."""

    def _workflow(self, uses: str) -> None:
        self.repo.write(
            ".github/workflows/ci.yml",
            f"jobs:\n  build:\n    steps:\n      - uses: {uses}\n",
        )

    def test_sha_pinned_action_passes(self):
        self._workflow("actions/checkout@" + "a" * 40 + " # v4.2.2")
        self.assertEqual(repo_checks.check_action_pins(self.repo.root), [])

    def test_tag_pinned_action_is_caught(self):
        self._workflow("actions/checkout@v4")
        self.assertCaught(repo_checks.check_action_pins(self.repo.root), "mutable ref")

    def test_semver_tag_is_caught(self):
        self._workflow("actions/setup-dotnet@v4.3.1")
        self.assertCaught(repo_checks.check_action_pins(self.repo.root), "mutable ref")

    def test_branch_ref_is_caught(self):
        self._workflow("some/action@main")
        self.assertCaught(repo_checks.check_action_pins(self.repo.root), "mutable ref")

    def test_short_sha_is_caught(self):
        """An abbreviated SHA is ambiguous and not what the pin contract means."""
        self._workflow("actions/checkout@a1b2c3d")
        self.assertCaught(repo_checks.check_action_pins(self.repo.root), "mutable ref")

    def test_unversioned_action_is_caught(self):
        self._workflow("actions/checkout")
        self.assertCaught(repo_checks.check_action_pins(self.repo.root), "no version")

    def test_local_composite_action_is_allowed(self):
        self._workflow("./.github/actions/local-thing")
        self.assertEqual(repo_checks.check_action_pins(self.repo.root), [])


class ReadOnlyAgentTests(CheckTestCase):
    def _agent(self, name: str, tools: str | None, body: str) -> None:
        front = f"---\nname: {name}\ndescription: x\n"
        if tools is not None:
            front += f"tools: {tools}\n"
        front += "---\n"
        self.repo.write(f".claude/agents/{name}.md", front + body)

    def test_readonly_agent_without_write_tools_passes(self):
        self._agent("reviewer", "Read, Grep, Glob", "You are read-only.\n")
        self.assertEqual(repo_checks.check_readonly_agents(self.repo.root), [])

    def test_readonly_agent_granted_bash_is_caught(self):
        """Bash is a write tool: sed -i, cat >, git commit are each one command away."""
        self._agent("reviewer", "Read, Grep, Glob, Bash", "You are read-only.\n")
        self.assertCaught(repo_checks.check_readonly_agents(self.repo.root), "read-only")

    def test_readonly_agent_granted_edit_is_caught(self):
        self._agent("reviewer", "Read, Edit", "This agent is read only.\n")
        self.assertCaught(repo_checks.check_readonly_agents(self.repo.root), "read-only")

    def test_implementation_agent_may_hold_write_tools(self):
        self._agent("engine-dev", None, "You implement one Issue and may edit.\n")
        self.assertEqual(repo_checks.check_readonly_agents(self.repo.root), [])

    def test_agent_not_claiming_readonly_is_unaffected(self):
        self._agent("helper", "Read, Bash", "You run commands.\n")
        self.assertEqual(repo_checks.check_readonly_agents(self.repo.root), [])


class PhaseAuthorityTests(CheckTestCase):
    def test_phase_stated_only_in_claude_md_passes(self):
        self.repo.write("README.md", "Current phase is recorded in CLAUDE.md.\n")
        self.assertEqual(repo_checks.check_phase_authority(self.repo.root), [])

    def test_phase_restated_in_readme_is_caught(self):
        self.repo.write("README.md", "**Phase 0 - foundation.** Complete.\n")
        self.assertCaught(repo_checks.check_phase_authority(self.repo.root), "current phase")

    def test_phase_restated_in_roadmap_is_caught(self):
        self.repo.write("docs/roadmap.md", "**Phase 1 - kernel.** Next.\n")
        self.assertCaught(repo_checks.check_phase_authority(self.repo.root), "current phase")

    def test_roadmap_phase_table_is_not_a_phase_claim(self):
        """The roadmap lists every phase; that is its job. Only 'this is where we are' counts."""
        self.repo.write("docs/roadmap.md", "| 1 | Deterministic randomness | vectors pinned |\n")
        self.assertEqual(repo_checks.check_phase_authority(self.repo.root), [])


class InvariantDriftTests(CheckTestCase):
    """Restating an invariant is fine. Restating it unchecked is how it drifts.

    At one hour old, engine-dev.md -- the charter of the agent most likely to write
    engine code -- was missing 5 of the 11 banned APIs.
    """

    MANIFEST = (
        '{"sources": [{"sourceId": "sr6-core", "sha256": "' + "a" * 64 + '",'
        ' "pdfPageCount": 322, "pageNumbering": {"printedPageEqualsPdfPageMinus": 1},'
        ' "envVar": "SR6_CORE_PDF"}]}\n'
    )

    def _full_ban_list(self) -> str:
        return "\n".join(f"| `{d}` | why |" for d, _p, _w in repo_checks.BANNED_IN_ENGINE)

    def test_complete_ban_list_passes(self):
        self.repo.write("docs/architecture.md", self._full_ban_list())
        self.repo.write(".claude/agents/engine-dev.md", self._full_ban_list())
        self.assertEqual(repo_checks.check_invariant_drift(self.repo.root), [])

    def test_incomplete_ban_list_is_caught(self):
        partial = "\n".join(
            f"| `{d}` |" for d, _p, _w in repo_checks.BANNED_IN_ENGINE[:4]
        )
        self.repo.write("docs/architecture.md", self._full_ban_list())
        self.repo.write(".claude/agents/engine-dev.md", partial)
        self.assertCaught(
            repo_checks.check_invariant_drift(self.repo.root), "omits"
        )

    def test_the_exact_historical_drift_is_caught(self):
        """Regression: these 5 were the ones actually missing from engine-dev.md."""
        omitted = {"RandomNumberGenerator", "DateTimeOffset.Now", "Environment.TickCount",
                   "Stopwatch", "Environment.GetEnvironmentVariable"}
        kept = "\n".join(
            f"| `{d}` |" for d, _p, _w in repo_checks.BANNED_IN_ENGINE if d not in omitted
        )
        self.repo.write("docs/architecture.md", self._full_ban_list())
        self.repo.write(".claude/agents/engine-dev.md", kept)
        failures = repo_checks.check_invariant_drift(self.repo.root)
        self.assertCaught(failures, "engine-dev.md")
        for name in omitted:
            self.assertIn(name, " ".join(failures))

    def test_correct_page_offset_passes(self):
        self.repo.write("docs/architecture.md", self._full_ban_list())
        self.repo.write(".claude/agents/engine-dev.md", self._full_ban_list())
        self.repo.write(".github/source-manifest.json", self.MANIFEST)
        self.repo.write("docs/source-handling.md", "printed page = PDF page - 1\n")
        self.assertEqual(repo_checks.check_invariant_drift(self.repo.root), [])

    def test_drifted_page_offset_is_caught(self):
        self.repo.write("docs/architecture.md", self._full_ban_list())
        self.repo.write(".claude/agents/engine-dev.md", self._full_ban_list())
        self.repo.write(".github/source-manifest.json", self.MANIFEST)
        self.repo.write("docs/source-handling.md", "printed page = PDF page - 2\n")
        self.assertCaught(
            repo_checks.check_invariant_drift(self.repo.root), "manifest says 1"
        )

    def test_drifted_page_count_is_caught(self):
        self.repo.write("docs/architecture.md", self._full_ban_list())
        self.repo.write(".claude/agents/engine-dev.md", self._full_ban_list())
        self.repo.write(".github/source-manifest.json", self.MANIFEST)
        self.repo.write("docs/source-handling.md", "The book has 999 PDF pages.\n")
        self.assertCaught(repo_checks.check_invariant_drift(self.repo.root), "manifest says 322")


class RealRepositoryTests(unittest.TestCase):
    """The repository itself must satisfy every check it ships."""

    def test_repo_passes_all_of_its_own_checks(self):
        results = repo_checks.run(ROOT, sorted(repo_checks.CHECKS))
        problems = {k: v for k, v in results.items() if v}
        self.assertEqual(problems, {})


if __name__ == "__main__":
    unittest.main()
