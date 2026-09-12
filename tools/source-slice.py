#!/usr/bin/env python3
"""source-slice -- bounded, hash-verified extraction from the authoritative SR6 source.

Every agent that needs rulebook text gets it through this tool, so that every agent
sees the same bytes produced the same way, and so that no agent ever re-transcribes
the book from memory. It slices a page range and emits it with a self-describing
header identifying exactly what was extracted and from what.

    tools/source-slice.py --pages 45-48
    tools/source-slice.py --printed-pages 44-47 --layout
    tools/source-slice.py --pages 45 --expect "Success Test" --output /tmp/packet.txt

Design constraints (docs/source-handling.md):

  * There is NO --file argument, and there never will be. The source is resolved from
    the committed manifest, so an agent cannot substitute a different printing, a
    previous edition, a supplement, or a pirated scan.
  * The file's SHA-256 is verified against .github/source-manifest.json BEFORE any
    text is extracted. A mismatch extracts nothing and exits non-zero.
  * This is a page-slice tool, not a search tool. It does not index the book and it
    must not be used to build a searchable copy of the book in the repository.
  * Output packets are ephemeral. They are gitignored and must never be committed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / ".github" / "source-manifest.json"


def _primary_checkout_root() -> Path:
    """The root of the checkout that owns the shared `.git` -- the primary checkout.

    `source.local.json` is gitignored, so it exists only where someone put it, almost
    always the primary checkout: CLAUDE.md requires all implementation to happen in a
    worktree tools/dispatch-agent.sh created, and nobody hand-configures the source
    freshly in every one of those. Resolving it against ROOT (this script's own parent)
    made it invisible from every worktree -- the one place it is needed (#57).

    `git rev-parse --git-common-dir` is what dispatch-agent.sh already uses to tell a
    worktree from the primary checkout (its own assert_primary_checkout), and what
    scripts/lib/dotnet-env.sh's deckard_primary_checkout_root uses for the identical
    "one shared resource, every worktree must find it" problem with its own .dotnet/
    lookup -- this mirrors that shell function. The common-dir path points at the
    primary checkout's `.git` directory; its parent is the checkout root, since `.git`
    sits directly under the root in this repository's layout (checked below, rather
    than assumed, in case that ever stops holding -- a submodule's `.git` is a file, not
    a directory, for instance). The path comes back relative to whatever directory git
    ran in when it is already the primary checkout's own `.git` (the common case there),
    so it is resolved against ROOT rather than assumed absolute.

    Falls back to ROOT -- the pre-#57 behaviour -- when git is unavailable, this is not
    a git checkout at all, or the resolved common dir does not look like a `.git`
    directory, so the tool still degrades to "look next to me" rather than failing
    outright or resolving somewhere nonsensical.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ROOT
    if result.returncode != 0:
        return ROOT
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = ROOT / common_dir
    common_dir = common_dir.resolve()
    if common_dir.name != ".git":
        return ROOT
    return common_dir.parent


PRIMARY_CHECKOUT_ROOT = _primary_checkout_root()
LOCAL_CONFIG = PRIMARY_CHECKOUT_ROOT / "source.local.json"

# Test-only manifest override. Requires an explicit second opt-in, and any packet
# produced under it is stamped NON-AUTHORITATIVE in its own header so a substituted
# baseline can never masquerade as the real one in a review packet.
ENV_MANIFEST_OVERRIDE = "DECKARD_SOURCE_MANIFEST"
ENV_MANIFEST_OVERRIDE_OPT_IN = "DECKARD_ALLOW_TEST_MANIFEST"


class SourceSliceError(RuntimeError):
    """Any condition that must abort extraction before a single page is read."""


# --------------------------------------------------------------------------- manifest


def _manifest_path() -> tuple[Path, bool]:
    """Return (path, is_test_override)."""
    override = os.environ.get(ENV_MANIFEST_OVERRIDE)
    if not override:
        return DEFAULT_MANIFEST, False
    if os.environ.get(ENV_MANIFEST_OVERRIDE_OPT_IN) != "1":
        raise SourceSliceError(
            f"{ENV_MANIFEST_OVERRIDE} is set but {ENV_MANIFEST_OVERRIDE_OPT_IN}=1 is not.\n"
            "The manifest override exists only for the tooling tests. Unset "
            f"{ENV_MANIFEST_OVERRIDE} to use the authoritative manifest."
        )
    return Path(override), True


def load_source(source_id: str) -> tuple[dict, bool]:
    path, is_override = _manifest_path()
    if not path.is_file():
        raise SourceSliceError(f"Source manifest not found: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceSliceError(f"Source manifest is not valid JSON: {path}: {exc}") from exc

    for entry in manifest.get("sources", []):
        if entry.get("sourceId") == source_id:
            return entry, is_override

    known = ", ".join(e.get("sourceId", "?") for e in manifest.get("sources", [])) or "(none)"
    raise SourceSliceError(f"Unknown sourceId '{source_id}'. Manifest declares: {known}")


# ------------------------------------------------------------------------ source file


def resolve_source_path(source: dict, is_test_manifest: bool = False) -> Path:
    """Locate the local copy of the authoritative source.

    Resolution order, both deliberately outside version control:
      1. the environment variable named by the manifest (e.g. SR6_CORE_PDF)
      2. source.local.json in the PRIMARY CHECKOUT (see PRIMARY_CHECKOUT_ROOT above),
         mapping sourceId -> absolute path -- not the checkout this script happens to be
         running from, so it is visible from every worktree too (#57).

    Under a test manifest the source.local.json fallback is skipped entirely, so a
    developer's real local configuration can never leak into a fixture run and make
    the tooling tests pass or fail for reasons unrelated to the code.
    """
    env_var = source["envVar"]
    raw = os.environ.get(env_var)
    origin = f"${env_var}"

    if not raw and not is_test_manifest and LOCAL_CONFIG.is_file():
        try:
            local = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SourceSliceError(f"{LOCAL_CONFIG} is not valid JSON: {exc}") from exc
        raw = local.get(source["sourceId"])
        origin = str(LOCAL_CONFIG)

    if not raw:
        raise SourceSliceError(
            f"The authoritative source for '{source['sourceId']}' is not configured.\n"
            f"  Set {env_var} to the absolute path of your own copy of:\n"
            f"    {source['title']} -- {source['edition']}\n"
            f"  or create {LOCAL_CONFIG} (gitignored) containing:\n"
            f'    {{ "{source["sourceId"]}": "/absolute/path/to/the/file.pdf" }}\n'
            "  See docs/source-handling.md. The file itself is never committed."
        )

    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SourceSliceError(f"{origin} must be an absolute path, got: {raw}")
    if not path.is_file():
        raise SourceSliceError(f"{origin} points at a file that does not exist: {path}")
    return path


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source(source: dict, path: Path) -> str:
    """Verify the local file IS the pinned baseline. Refuse everything otherwise."""
    expected = source["sha256"].lower()
    actual = sha256_of(path).lower()
    if actual != expected:
        raise SourceSliceError(
            "AUTHORITATIVE SOURCE HASH MISMATCH -- extracted nothing.\n"
            f"  sourceId : {source['sourceId']}\n"
            f"  expected : {expected}\n"
            f"  actual   : {actual}\n"
            f"  file     : {path}\n\n"
            "The configured file is not the pinned baseline. This is either the wrong\n"
            "printing, a different scan, or a corrupted download. Do NOT edit the\n"
            "manifest to make this pass: changing the baseline is a deliberate decision\n"
            "that requires its own Issue, PR and ADR. See docs/source-handling.md."
        )
    return actual


# ----------------------------------------------------------------------- page ranges


def parse_range(text: str, label: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", text)
    if not match:
        raise SourceSliceError(f"--{label} expects N or N-M, got: {text!r}")
    first = int(match.group(1))
    last = int(match.group(2)) if match.group(2) else first
    if first < 1:
        raise SourceSliceError(f"--{label} must start at 1 or higher, got: {first}")
    if last < first:
        raise SourceSliceError(f"--{label} range is inverted: {first}-{last}")
    return first, last


def printed_to_pdf(source: dict, first: int, last: int) -> tuple[int, int]:
    offset = source["pageNumbering"]["printedPageEqualsPdfPageMinus"]
    return first + offset, last + offset


def check_bounds(source: dict, first: int, last: int) -> None:
    total = source["pdfPageCount"]
    if last > total:
        raise SourceSliceError(
            f"Requested PDF pages {first}-{last} but '{source['sourceId']}' has {total} pages."
        )


MAX_PAGES = 24


def check_packet_size(first: int, last: int) -> None:
    """Source packets are bounded on purpose, with no override.

    A large slice is how a repository accidentally grows a full transcription of a
    commercial rulebook, and how an agent's context fills with material it will not
    read carefully. Wanting 40 pages at once is almost always a sign the Issue is
    too broad, not that the limit is wrong.

    There used to be an `--allow-large` escape hatch here. It had no ceiling of its
    own, so it could extract the entire 322-page baseline in one packet -- the exact
    outcome this function exists to prevent (#42). Nothing in the repository ever
    invoked it: not the agent tooling, not docs/source-handling.md, not the
    source-packet skill. Deleting it is what makes "packets are bounded to
    MAX_PAGES pages" (docs/source-handling.md) true of the tool as shipped, rather
    than true only when nobody passes a flag. Wanting more than MAX_PAGES pages in
    one packet means narrowing the Issue, not reaching for a bigger flag.
    """
    count = last - first + 1
    if count > MAX_PAGES:
        raise SourceSliceError(
            f"Refusing to extract {count} pages in one packet (limit {MAX_PAGES}).\n"
            "Source packets are bounded by design -- see docs/source-handling.md.\n"
            "Narrow the Issue. There is no flag to extract more than this in one packet."
        )


# ------------------------------------------------------------------------ extraction

# CI (.github/workflows/build-and-test.yml) and scripts/doctor.sh both compare an
# installed pdftotext against the version pinned in .github/poppler-version.json -- the
# same granularity `pdftotext -v` reports, and exactly what ends up in every packet's
# `extractorVersion` field below. This tool itself does not read or enforce that pin: it
# reports whatever is actually installed, honestly, rather than silently blocking a
# developer whose OS cannot match CI's exactly. See docs/source-handling.md for what the
# pin can and cannot guarantee -- Deckard does not vendor Poppler (a deliberate
# non-goal), so it forces drift to be *visible and deliberate*, not impossible.


def require_pdftotext() -> None:
    if shutil.which("pdftotext") is None:
        raise SourceSliceError(
            "pdftotext not found. Install poppler-utils:  sudo apt install poppler-utils"
        )


def extractor_version() -> str:
    """pdftotext's own reported version string, e.g. "24.02.0".

    poppler-utils prints its banner (including the version) to stderr and exits 0 for
    `-v`; older or newer builds have printed it to stdout instead, so both streams are
    checked. This is the exact string written into every packet's `extractorVersion`
    field, and the granularity CI and scripts/doctor.sh pin against.
    """
    result = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True, check=False)
    banner = result.stderr or result.stdout
    match = re.search(r"version\s+(\S+)", banner)
    if not match:
        raise SourceSliceError(
            f"could not parse a version from `pdftotext -v` output: {banner.strip()!r}"
        )
    return match.group(1)


def build_argv(path: Path, first: int, last: int, layout: bool) -> list[str]:
    cmd = ["pdftotext", "-f", str(first), "-l", str(last)]
    if layout:
        cmd.append("-layout")
    cmd += [str(path), "-"]
    return cmd


def redact_argv(argv: list[str], path: Path) -> list[str]:
    """The argv as it goes into a packet header: every flag and page number, but never
    the local filesystem path.

    docs/source-handling.md already keeps scripts/doctor.sh from ever printing Brandon's
    full local path, because doctor output gets pasted into Issues -- the same reasoning
    applies here, since packets get read and occasionally quoted from even though they
    are never committed. Only the basename survives; the flags this field exists to
    disclose (`-layout`, `-f`, `-l`) are untouched.
    """
    needle = str(path)
    return [Path(item).name if item == needle else item for item in argv]


def extract(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SourceSliceError(f"pdftotext failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def hash_body(body: str) -> str:
    """Hash of the extracted body alone, over the exact bytes a packet's body is made
    of -- what a later 'verified against packet X' review citation actually verifies
    against, given the packet itself is never committed and so cannot be re-read."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_header(
    source: dict,
    first: int,
    last: int,
    layout: bool,
    is_override: bool,
    *,
    extractor_version: str,
    argv_display: list[str],
    body_sha256: str,
) -> str:
    offset = source["pageNumbering"]["printedPageEqualsPdfPageMinus"]

    def field(label: str, value: str) -> str:
        return f"{label:<16}: {value}"

    lines = [
        "=" * 78,
        "DECKARD SOURCE PACKET -- ephemeral, never commit this file",
        "=" * 78,
    ]
    if is_override:
        lines += [
            "!! NON-AUTHORITATIVE: produced under a test manifest override.        !!",
            "!! This packet must not be used as a rules-conformance reference.     !!",
            "=" * 78,
        ]
    lines += [
        field("sourceId", source["sourceId"]),
        field("title", source["title"]),
        field("edition", source["edition"]),
        field("sha256", source["sha256"]),
        field("pdf pages", f"{first}-{last}"),
        field("printed pages", f"{first - offset}-{last - offset}"),
        field("extractor", "pdftotext"),
        field("extractorVersion", extractor_version),
        field("argv", " ".join(argv_display)),
        field("bodySha256", body_sha256),
        "",
        "Cite rules as:  SR6 Core / <section> / printed p. X / PDF p. Y",
        "This excerpt is copyrighted material reproduced locally for implementation",
        "reference only. Do not commit it, paste it into an Issue, or redistribute it.",
        "=" * 78,
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="source-slice.py",
        description="Bounded, hash-verified extraction from the authoritative SR6 source.",
    )
    pages = parser.add_mutually_exclusive_group()
    pages.add_argument("--pages", help="PDF page or range, e.g. 45 or 45-48")
    pages.add_argument(
        "--printed-pages",
        help="Page or range as printed in the book; converted using the manifest offset",
    )
    parser.add_argument("--source-id", default="sr6-core", help="default: sr6-core")
    parser.add_argument(
        "--layout", action="store_true", help="preserve layout (use for printed tables)"
    )
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="REGEX",
        help="assert this pattern appears in the slice; repeatable. Fails loudly if absent.",
    )
    parser.add_argument("--output", help="write here instead of stdout")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify configuration and hash, extract nothing",
    )
    args = parser.parse_args(argv)

    source, is_override = load_source(args.source_id)
    path = resolve_source_path(source, is_override)
    verify_source(source, path)

    if args.verify_only:
        print(f"OK  {source['sourceId']}  sha256 verified  ({source['pdfPageCount']} pages)")
        return 0

    if not args.pages and not args.printed_pages:
        raise SourceSliceError("one of --pages, --printed-pages or --verify-only is required")

    if args.printed_pages:
        first, last = printed_to_pdf(source, *parse_range(args.printed_pages, "printed-pages"))
    else:
        first, last = parse_range(args.pages, "pages")

    check_bounds(source, first, last)
    check_packet_size(first, last)

    require_pdftotext()
    version = extractor_version()
    argv_used = build_argv(path, first, last, args.layout)
    body = extract(argv_used)

    missing = [p for p in args.expect if not re.search(p, body, re.IGNORECASE)]
    if missing:
        raise SourceSliceError(
            "Expected anchor(s) not found in PDF pages "
            f"{first}-{last}: {', '.join(repr(m) for m in missing)}\n"
            "The page range does not cover what it claims to. Locate the correct pages\n"
            "rather than weakening the anchor."
        )

    packet = (
        build_header(
            source,
            first,
            last,
            args.layout,
            is_override,
            extractor_version=version,
            argv_display=redact_argv(argv_used, path),
            body_sha256=hash_body(body),
        )
        + body
    )

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(packet, encoding="utf-8")
        print(f"wrote {out}  (PDF pp. {first}-{last}, {len(packet)} chars)", file=sys.stderr)
    else:
        sys.stdout.write(packet)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SourceSliceError as error:
        print(f"source-slice: {error}", file=sys.stderr)
        raise SystemExit(2) from None
