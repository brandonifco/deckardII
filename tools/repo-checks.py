#!/usr/bin/env python3
"""repo-checks -- mechanical enforcement of Deckard's written invariants.

Every check here exists because the alternative is trusting an agent to remember a
rule stated in prose. Prose does not fail a build. These do.

    tools/repo-checks.py            # run every check against the repo
    tools/repo-checks.py --json     # machine-readable result

Checks:
  text-hygiene   UTF-8, no BOM, LF endings, exactly one trailing newline
  determinism    no ambient randomness or ambient time in engine source
  layering       declared ProjectReference graph matches docs/architecture.md
  core-filesystem  Deckard.Core touches no filesystem API (ADR 0001)
  source-boundary  no rulebook, no source packet, no local source path committed
  action-pins    third-party GitHub Actions pinned to immutable commit SHAs
  readonly-agents  agents claiming to be read-only carry no write-capable tool
  phase-authority  current phase is stated in CLAUDE.md and nowhere else
  invariant-drift  prose restatements match their single authority
  single-queue   no competing task backlog outside GitHub Issues
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Suffixes whose text hygiene (BOM/CRLF/trailing newline) is checked. This is a
# formatting concern, so an allowlist is right here.
TEXT_SUFFIXES = {
    ".cs", ".csproj", ".props", ".targets", ".slnx", ".json", ".md",
    ".yml", ".yaml", ".sh", ".py", ".editorconfig", ".gitattributes", ".gitignore",
}

# Binary formats that will never be scanned for leaked source text. Everything else that
# decodes as UTF-8 IS scanned, including .txt and extensionless files.
#
# The source-boundary check originally reused TEXT_SUFFIXES, which has no ".txt" -- the
# exact extension every document here teaches for source packets ("--output packet.txt").
# A committed chapter4.txt full of rulebook prose produced no findings at all. An
# allowlist is the wrong shape for a check whose job is to catch the file nobody
# anticipated.
BINARY_SUFFIXES = {
    ".pdf", ".epub", ".mobi", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
    ".zip", ".gz", ".tar", ".7z", ".dll", ".exe", ".so", ".dylib", ".pdb",
    ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".wav",
}


def decode_text(path: Path) -> str | None:
    """Return the file's text, or None when it is binary or unreadable.

    Deliberately attempts every non-binary file rather than consulting a suffix
    allowlist: the leak this guards against arrives in whatever extension the person
    leaking it happened to type.
    """
    if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
        return None
    try:
        return path.read_bytes().decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return None

# --------------------------------------------------------------------------------
# Determinism. See docs/architecture.md and CLAUDE.md.
#
# Same rules version + same initial state + same seed + same ordered decisions
# => same outcomes and same ordered event history. Every pattern below breaks that
# by pulling entropy or wall-clock time out of the ambient environment.
# --------------------------------------------------------------------------------
# (display, pattern, why). `display` is the canonical name of the banned API, and it is
# what every prose restatement is checked against -- see check_invariant_drift. Adding an
# entry here without updating the documents that list it fails the build, which is the
# point: the ban list was restated in five places and had already drifted, with the agent
# most likely to write engine code holding the most incomplete copy.
BANNED_IN_ENGINE: list[tuple[str, str, str]] = [
    ("Random.Shared", r"\bRandom\s*\.\s*Shared\b",
     "Random.Shared is process-global ambient entropy"),
    ("new Random()", r"\bnew\s+Random\s*\(",
     "new Random() is ambient entropy; inject IRandomSource"),
    ("RandomNumberGenerator", r"\bRandomNumberGenerator\b",
     "cryptographic RNG is not replayable"),
    ("Guid.NewGuid()", r"\bGuid\s*\.\s*NewGuid\s*\(",
     "Guid.NewGuid() as game state is non-reproducible"),
    ("DateTime.Now", r"\bDateTime\s*\.\s*(Now|UtcNow|Today)\b",
     "ambient clock breaks replay"),
    ("DateTimeOffset.Now", r"\bDateTimeOffset\s*\.\s*(Now|UtcNow)\b",
     "ambient clock breaks replay"),
    ("Environment.TickCount", r"\bEnvironment\s*\.\s*TickCount",
     "process timing is not a rules input"),
    ("Stopwatch", r"\bStopwatch\s*\.\s*(GetTimestamp|StartNew)",
     "process timing is not a rules input"),
    ("Environment.GetEnvironmentVariable", r"\bEnvironment\s*\.\s*GetEnvironmentVariable",
     "engine must not read the environment"),
    ("Task.Run", r"\bTask\s*\.\s*Run\b",
     "concurrency makes resolution order non-deterministic"),
    ("AsParallel", r"\.\s*AsParallel\s*\(",
     "PLINQ makes iteration order non-deterministic"),
]

# An engine file may opt out only with an explicit, reviewed justification on the line.
ALLOW_MARKER = "deckard:allow-nondeterminism"

# The declared dependency graph. Core is the floor; nothing may point upward.
# Files that legitimately mention the source boundary machinery: the tool that emits
# packets, the checker that hunts for them, and the doc that explains both. Everything
# else mentioning a packet marker or a local rulebook path is a leak.
PACKET_MARKER = "DECKARD SOURCE" + " PACKET"
# Any absolute path into somebody's home directory. Deliberately generic rather than
# naming a particular file: the checker should not need to know, or publish, what
# Brandon called his copy.
LOCAL_PATH_LEAK = re.compile(r"(?:/home/|/Users/|/root/)[A-Za-z0-9_.\-]+/")
# Exemptions are PER CHECK, not per file. A blanket allowlist previously disabled BOTH
# checks on docs/source-handling.md -- the single document most likely to acquire a real
# local path in an example, and the one with least reason to be exempt from that check.
# Each entry below is exempt from exactly the one check it genuinely needs to be.
PACKET_MARKER_EXEMPT = {
    "tools/source-slice.py",            # emits the marker
    "tools/repo-checks.py",             # searches for the marker
    "tools/tests/test_repo_checks.py",  # asserts on the marker
}
LOCAL_PATH_EXEMPT = {
    "tools/repo-checks.py",             # defines the pattern
    "tools/tests/test_repo_checks.py",  # builds fake paths as fixtures
}

ALLOWED_PROJECT_REFS: dict[str, set[str]] = {
    "Deckard.Core": set(),
    "Deckard.Data": {"Deckard.Core"},
    "Deckard.Rules": {"Deckard.Core", "Deckard.Data"},
    # Deckard.Testing is test-support code, not a fourth engine layer: it lives under
    # tests/, ships to nobody, and may see only Core. See amendment to ADR 0001.
    "Deckard.Testing": {"Deckard.Core"},
}

# Where each project above actually lives. check_layering used to assume every project
# sat under src/<name>/ -- true while the graph was exactly Core, Data, Rules. Deckard.Testing
# lives under tests/ instead, so the directory is now looked up per project rather than
# hard-coded.
PROJECT_DIRS: dict[str, str] = {
    "Deckard.Core": "src/Deckard.Core",
    "Deckard.Data": "src/Deckard.Data",
    "Deckard.Rules": "src/Deckard.Rules",
    "Deckard.Testing": "tests/Deckard.Testing",
}


class Failure(str):
    """A single human-readable check failure."""


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return []
    return [root / name for name in result.stdout.split("\0") if name]


# --------------------------------------------------------------------------- checks


def check_text_hygiene(root: Path) -> list[Failure]:
    failures: list[Failure] = []
    for path in tracked_files(root):
        if path.suffix not in TEXT_SUFFIXES and path.name not in TEXT_SUFFIXES:
            continue
        if not path.is_file():
            continue
        raw = path.read_bytes()
        rel = path.relative_to(root)
        if raw.startswith(b"\xef\xbb\xbf"):
            failures.append(Failure(f"{rel}: UTF-8 BOM (spec requires no BOM)"))
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            failures.append(Failure(f"{rel}: not valid UTF-8"))
            continue
        if b"\r\n" in raw:
            failures.append(Failure(f"{rel}: CRLF line endings (spec requires LF)"))
        if raw and not raw.endswith(b"\n"):
            failures.append(Failure(f"{rel}: missing trailing newline"))
        if raw.endswith(b"\n\n"):
            failures.append(Failure(f"{rel}: more than one trailing newline"))
    return failures


def check_determinism(root: Path) -> list[Failure]:
    failures: list[Failure] = []
    engine = root / "src"
    if not engine.is_dir():
        return failures
    for path in sorted(engine.rglob("*.cs")):
        if any(part in {"obj", "bin"} for part in path.parts):
            continue
        rel = path.relative_to(root)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ALLOW_MARKER in line:
                continue
            for _display, pattern, why in BANNED_IN_ENGINE:
                if re.search(pattern, line):
                    failures.append(
                        Failure(f"{rel}:{lineno}: {why}  [{line.strip()[:70]}]")
                    )
    return failures


def check_layering(root: Path) -> list[Failure]:
    failures: list[Failure] = []
    for project, allowed in ALLOWED_PROJECT_REFS.items():
        csproj = root / PROJECT_DIRS[project] / f"{project}.csproj"
        if not csproj.is_file():
            failures.append(Failure(f"missing expected project: {csproj.relative_to(root)}"))
            continue
        text = csproj.read_text(encoding="utf-8")
        declared = {
            Path(m).stem
            for m in re.findall(r'ProjectReference\s+Include="([^"]+)"', text)
        }
        extra = declared - allowed
        if extra:
            failures.append(
                Failure(
                    f"{project} declares forbidden ProjectReference(s): {sorted(extra)}; "
                    f"allowed: {sorted(allowed) or 'none'}"
                )
            )
    return failures


# --------------------------------------------------------------------------------
# Core filesystem boundary (ADR 0001; docs/architecture.md). Scoped to Deckard.Core
# alone, not folded into BANNED_IN_ENGINE above: that list is deliberately
# engine-wide, but Deckard.Data's structured-data loaders will legitimately read
# files once Data exists, and a ban firing there would be wrong.
#
# Each pattern requires an actual call-site shape (a member access immediately
# followed by an identifier or a call, not a bare word) so it cannot match an
# ordinary sentence -- Core's own doc comments legitimately discuss this exact
# boundary (see Deckard.Core.Replay.SourceBaselineId, ADR 0005). Comment-only lines
# are skipped as a second, independent safeguard.
# --------------------------------------------------------------------------------
CORE_FILESYSTEM_APIS: list[tuple[str, str]] = [
    (r"\bSystem\.IO\.[A-Za-z]", "fully-qualified System.IO member access"),
    (r"\bFile\.[A-Za-z]", "System.IO.File"),
    (r"\bFileInfo\b", "System.IO.FileInfo"),
    (r"\bDirectory\.[A-Za-z]", "System.IO.Directory"),
    (r"\bDirectoryInfo\b", "System.IO.DirectoryInfo"),
    (r"\bFileStream\b", "System.IO.FileStream"),
    (r"\bStreamReader\b", "System.IO.StreamReader"),
    (r"\bStreamWriter\b", "System.IO.StreamWriter"),
    (r"\bPath\.[A-Za-z]+\(", "System.IO.Path"),
]
CORE_COMMENT_LINE = re.compile(r"^\s*(?://|\*)")


def check_core_filesystem_boundary(root: Path) -> list[Failure]:
    """Deckard.Core may not touch the filesystem -- ADR 0001, docs/architecture.md.

    CORE_COMMENT_LINE only recognises a WHOLE-LINE comment (a line whose first
    non-whitespace characters are `//` or `*`). A trailing comment on a code line --
    `var x = 1; // mentions File.ReadAllText` -- is still scanned in full and would be
    caught. This is a deliberate simplification, not an oversight: correctly stripping a
    trailing comment requires tracking string and char literals so a `//` inside one is
    not mistaken for a comment start, which is more machinery than this check's job
    justifies. Nothing in Core trips this today; if it ever does, move the mention to its
    own comment line rather than teaching this check string-literal awareness.
    """
    failures: list[Failure] = []
    core = root / "src" / "Deckard.Core"
    if not core.is_dir():
        return failures
    for path in sorted(core.rglob("*.cs")):
        if any(part in {"obj", "bin"} for part in path.parts):
            continue
        rel = path.relative_to(root)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CORE_COMMENT_LINE.match(line):
                continue
            for pattern, api in CORE_FILESYSTEM_APIS:
                if re.search(pattern, line):
                    failures.append(
                        Failure(
                            f"{rel}:{lineno}: Core must not touch the filesystem "
                            f"(ADR 0001) -- {api}  [{line.strip()[:70]}]"
                        )
                    )
    return failures


def check_source_boundary(root: Path) -> list[Failure]:
    """The rulebook is commercial copyrighted material. Nothing derived from it ships."""
    failures: list[Failure] = []
    manifest_path = root / ".github" / "source-manifest.json"

    for path in tracked_files(root):
        rel = path.relative_to(root)
        posix = rel.as_posix()

        if path.suffix.lower() in {".pdf", ".epub", ".mobi"}:
            failures.append(Failure(f"{rel}: rulebook-shaped binary is tracked in git"))

        text = decode_text(path)
        if text is None:
            continue

        if PACKET_MARKER in text and posix not in PACKET_MARKER_EXEMPT:
            failures.append(Failure(f"{rel}: contains an extracted source packet"))
        if LOCAL_PATH_LEAK.search(text) and posix not in LOCAL_PATH_EXEMPT:
            failures.append(Failure(f"{rel}: leaks a local authoritative-source path"))

    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("sources", []):
            if not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")):
                failures.append(
                    Failure(f"source-manifest.json: {entry.get('sourceId')} has no valid sha256")
                )
            for forbidden in ("path", "localPath", "file"):
                if forbidden in entry:
                    failures.append(
                        Failure(
                            f"source-manifest.json: {entry.get('sourceId')} carries a local "
                            f"'{forbidden}'; the local path belongs in "
                            f"${entry.get('envVar', 'SR6_CORE_PDF')}, never in git"
                        )
                    )
    return failures


def check_action_pins(root: Path) -> list[Failure]:
    """Third-party GitHub Actions must be pinned to immutable commit SHAs.

    A tag is mutable: whoever controls the action repository can repoint v4 at new code,
    and that code runs with this repository's workflow token. A 40-hex commit SHA cannot
    be repointed. This check exists because a floating tag is easy to reintroduce by
    copying an example from documentation.
    """
    failures: list[Failure] = []
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        return failures
    uses = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")
    for path in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
        rel = path.relative_to(root)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = uses.match(line)
            if not match:
                continue
            ref = match.group(1)
            if ref.startswith("./") or ref.startswith("."):
                continue  # a local composite action in this repository
            if "@" not in ref:
                failures.append(Failure(f"{rel}:{lineno}: action has no version: {ref}"))
                continue
            _, _, version = ref.partition("@")
            if not re.fullmatch(r"[0-9a-f]{40}", version):
                failures.append(
                    Failure(
                        f"{rel}:{lineno}: action pinned to mutable ref '{version}' "
                        f"({ref}); pin to a 40-character commit SHA"
                    )
                )
    return failures


# Tools through which an agent can modify the repository. `Bash` counts: `sed -i`,
# `cat >`, `git commit` and `git push` are all one command away.
WRITE_CAPABLE_TOOLS = {"Bash", "Edit", "Write", "NotebookEdit"}
READ_ONLY_MARKERS = ("read-only", "read only")


def check_readonly_agents(root: Path) -> list[Failure]:
    """An agent charter that calls itself read-only must not carry a write-capable tool.

    Four documents stated that review agents are read-only while both charters granted
    `Bash`. A reviewer that can edit what it reviews is not a reviewer, and the gap was
    invisible because the claim and the grant lived in different files.
    """
    failures: list[Failure] = []
    agents = root / ".claude" / "agents"
    if not agents.is_dir():
        return failures

    for path in sorted(agents.glob("*.md")):
        rel = path.relative_to(root)
        text = path.read_text(encoding="utf-8")
        front = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        if not front:
            continue
        tools_line = re.search(r"^tools:\s*(.+)$", front.group(1), re.MULTILINE)
        if not tools_line:
            continue  # No `tools:` key means all tools -- an implementation agent.
        granted = {t.strip() for t in tools_line.group(1).split(",") if t.strip()}

        claims_readonly = any(marker in text.lower() for marker in READ_ONLY_MARKERS)
        if not claims_readonly:
            continue
        offending = sorted(granted & WRITE_CAPABLE_TOOLS)
        if offending:
            failures.append(
                Failure(
                    f"{rel}: describes itself as read-only but is granted "
                    f"{offending}; these permit editing what it reviews"
                )
            )
    return failures


PHASE_CLAIM = re.compile(r"^.*\*\*Phase \d+[^*]*\*\*.*(complete|next|current)", re.IGNORECASE)
PHASE_AUTHORITY = "CLAUDE.md"


def check_phase_authority(root: Path) -> list[Failure]:
    """Current phase is stated in exactly one file.

    It was previously asserted in CLAUDE.md, README.md and docs/roadmap.md -- a fact that
    changes every phase, in three places, with nothing keeping them in step.
    """
    failures: list[Failure] = []
    for name in ("README.md", "docs/roadmap.md", "docs/scope.md", "AGENTS.md"):
        path = root / name
        if not path.is_file():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PHASE_CLAIM.match(line):
                failures.append(
                    Failure(
                        f"{name}:{lineno}: states the current phase, which is authoritative "
                        f"in {PHASE_AUTHORITY} only [{line.strip()[:50]}]"
                    )
                )
    return failures


# Documents that restate the determinism ban list in full and must stay complete.
BAN_LIST_RESTATEMENTS = ("docs/architecture.md", ".claude/agents/engine-dev.md")


def check_invariant_drift(root: Path) -> list[Failure]:
    """Prose restatements of a single-authority fact must match the authority.

    Restating an invariant where an agent will actually read it is worth the duplication.
    Unchecked duplication is not: the ban list lived in five documents and had already
    drifted at one hour old, and the manifest's page offset was restated in four places
    that a printing change would leave confidently wrong -- the exact off-by-one ADR 0003
    says the manifest exists to prevent.
    """
    failures: list[Failure] = []
    expected = {display for display, _pattern, _why in BANNED_IN_ENGINE}

    for name in BAN_LIST_RESTATEMENTS:
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        missing = sorted(d for d in expected if d.replace("()", "") not in text)
        if missing:
            failures.append(
                Failure(
                    f"{name}: restates the determinism ban list but omits {missing}; "
                    "authority is BANNED_IN_ENGINE in tools/repo-checks.py"
                )
            )

    # --- manifest facts -------------------------------------------------------------
    manifest_path = root / ".github" / "source-manifest.json"
    if manifest_path.is_file():
        entry = json.loads(manifest_path.read_text(encoding="utf-8"))["sources"][0]
        offset = entry["pageNumbering"]["printedPageEqualsPdfPageMinus"]
        page_count = entry["pdfPageCount"]

        offset_claim = re.compile(r"PDF page\s*[-−]\s*(\d+)")
        count_claim = re.compile(r"(\d+)\s*(?:PDF pages|pages\b(?=[^.]*\bPDF))")

        for path in sorted((root / "docs").rglob("*.md")) + sorted(
            (root / ".claude").rglob("*.md")
        ):
            rel = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
            for match in offset_claim.finditer(text):
                if int(match.group(1)) != offset:
                    failures.append(
                        Failure(
                            f"{rel}: states 'PDF page - {match.group(1)}' but the manifest "
                            f"says {offset}"
                        )
                    )
            for match in count_claim.finditer(text):
                if int(match.group(1)) != page_count:
                    failures.append(
                        Failure(
                            f"{rel}: states {match.group(1)} PDF pages but the manifest "
                            f"says {page_count}"
                        )
                    )

    # --- packet size limit ----------------------------------------------------------
    slice_tool = root / "tools" / "source-slice.py"
    if slice_tool.is_file():
        match = re.search(r"^MAX_PAGES\s*=\s*(\d+)", slice_tool.read_text(encoding="utf-8"),
                          re.MULTILINE)
        if match:
            limit = int(match.group(1))
            # Covers the phrasings actually used: "bounded to 24 pages",
            # "24 pages maximum", "limit 24", "a maximum of 24 pages".
            claim = re.compile(
                r"(?:bounded\s+to\s*(\d+)\s*pages"
                r"|(\d+)\s*pages?\s+maximum"
                r"|maximum\s+of\s+(\d+)\s*pages"
                r"|limit(?:ed)?\s+(?:to\s+)?(\d+)\s*pages?"
                r"|\(limit\s+(\d+)\))",
                re.IGNORECASE,
            )
            for path in sorted((root / "docs").rglob("*.md")) + sorted(
                (root / ".claude").rglob("*.md")
            ):
                rel = path.relative_to(root).as_posix()
                for m in claim.finditer(path.read_text(encoding="utf-8")):
                    stated = next(g for g in m.groups() if g is not None)
                    if int(stated) != limit:
                        failures.append(
                            Failure(
                                f"{rel}: states a {stated}-page packet limit but "
                                f"source-slice.py MAX_PAGES is {limit}"
                            )
                        )
    return failures


def check_single_queue(root: Path) -> list[Failure]:
    """GitHub Issues are the only live work queue (CLAUDE.md, governing principle 1).

    A markdown checklist in a governing document is how a second, silently stale
    backlog gets started. Roadmap phases are prose; task lists are not.
    """
    failures: list[Failure] = []
    watched = ["CLAUDE.md", "AGENTS.md", "README.md", "docs/roadmap.md", "docs/scope.md"]
    checkbox = re.compile(r"^\s*[-*]\s*\[[ xX]\]")
    for name in watched:
        path = root / name
        if not path.is_file():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if checkbox.match(line):
                failures.append(
                    Failure(
                        f"{name}:{lineno}: task checklist outside GitHub Issues "
                        f"[{line.strip()[:60]}]"
                    )
                )
    return failures


CHECKS = {
    "text-hygiene": check_text_hygiene,
    "determinism": check_determinism,
    "layering": check_layering,
    "core-filesystem": check_core_filesystem_boundary,
    "source-boundary": check_source_boundary,
    "action-pins": check_action_pins,
    "readonly-agents": check_readonly_agents,
    "phase-authority": check_phase_authority,
    "invariant-drift": check_invariant_drift,
    "single-queue": check_single_queue,
}


def run(root: Path, names: list[str]) -> dict[str, list[str]]:
    return {name: [str(f) for f in CHECKS[name](root)] for name in names}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repo-checks.py", description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--only", action="append", choices=sorted(CHECKS), default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    names = args.only or sorted(CHECKS)
    results = run(Path(args.root).resolve(), names)
    total = sum(len(v) for v in results.values())

    if args.json:
        print(json.dumps({"failures": total, "checks": results}, indent=2))
    else:
        for name in names:
            problems = results[name]
            if problems:
                print(f"FAIL  {name}  ({len(problems)})")
                for problem in problems:
                    print(f"        {problem}")
            else:
                print(f"ok    {name}")
        print()
        print("repo-checks: PASS" if total == 0 else f"repo-checks: {total} failure(s)")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
