#!/usr/bin/env python3
"""pr-policy -- enforce the small set of PR facts worth enforcing mechanically.

This is deliberately not an orchestration state machine. It checks a handful of things
that are cheap to verify and expensive to discover missing later, and it does not attempt
to parse prose into meaning.

    tools/pr-policy.py --pr 12                 # check a real PR via gh
    tools/pr-policy.py --body-file body.md \\
                       --changed-files files.txt --no-verify-issue
    tools/pr-policy.py --pr 12 --json evidence.json

A PR fails policy when:
  1. it does not close exactly one real Issue
  2. "Exact behavioral claim" is empty
  3. "Scope" is empty
  4. "Tests and evidence" is empty
  5. tests/evidence claims success without naming a command that produced it
  6. "Known limitations" is empty
  7. "Agent provenance" is empty
  8. rules files changed but "Rules conformance" is empty or N/A
  9. the linked Issue is state:needs-decision, and so is not implementable
"""
from __future__ import annotations

import argparse
import functools
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_SURFACE_LIB = ROOT / "tools" / "lib" / "rules-surface.sh"

# A module-level constant, not a literal in the subprocess.run() call, so a test can
# monkeypatch it down to something short and force a real subprocess.TimeoutExpired
# without the test suite paying for the full timeout.
CLASSIFY_TIMEOUT_SECONDS = 10


class RulesSurfaceClassifierError(RuntimeError):
    """tools/lib/rules-surface.sh --classify could not be run, or did not run cleanly.

    This must never be swallowed into an empty classification. Before this class existed,
    a non-zero exit here silently became "no changed file is a rules surface" -- fine
    while this call site only fetched the (optional) exclusion list (#86), but wrong once
    it became the WHOLE classification (#89): an empty result then means pr-policy skips
    the "Rules conformance" requirement entirely for a PR that rewrites the rules engine,
    and reports PASS while doing it. CLAUDE.md's "fail visibly" invariant exists exactly
    for this shape of failure -- an unresolved check must never do nothing or invent a
    default -- and it applies doubly here, since pr-policy is a required merge gate whose
    silence reads as approval.
    """


@functools.lru_cache(maxsize=8)
def _classify_rules_surface(changed_files: tuple[str, ...]) -> frozenset[str]:
    """Ask tools/lib/rules-surface.sh -- the one definition of what counts as a rules
    surface (Issue #89) -- which of `changed_files` match. The directory regex and the
    packages.lock.json exclusion (Issue #86) live there once, via the same
    rules_surface_path_matches this library's other consumers use for a rename-safe
    `git diff --name-status` check; this file never sees rename pairs, only the plain
    "path" list `gh pr view --json files` reports, but it must not keep its own copy of
    the matching rule that produces the same verdict. `--classify` takes the whole list
    in a single subprocess call (one path per line on stdin, matches echoed back), so a
    large diff still costs one call rather than one per path; `lru_cache` also collapses
    pr-policy's own two call sites (check() and the --json evidence writer) onto that one
    call when they share the same input.

    A change to the directory or exclusion definitions in rules-surface.sh alone is
    picked up here automatically -- this file holds no copy of either list to fall out
    of step with it.

    Raises RulesSurfaceClassifierError -- never returns a default -- when the classifier
    itself could not be run or did not exit cleanly: a non-zero exit, a timeout, or the
    interpreter/script being unreachable at all. lru_cache does not cache a raised
    exception, so a later, working call is retried rather than being stuck on a cached
    failure.
    """
    if not changed_files:
        return frozenset()
    stdin_text = "\n".join(changed_files) + "\n"
    try:
        result = subprocess.run(
            ["bash", str(RULES_SURFACE_LIB), "--classify"],
            input=stdin_text, capture_output=True, text=True,
            timeout=CLASSIFY_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RulesSurfaceClassifierError(
            f"{RULES_SURFACE_LIB} --classify timed out after {exc.timeout}s; "
            "cannot classify changed files without it -- refusing to guess."
        ) from exc
    except OSError as exc:
        # Covers FileNotFoundError (bash itself, or RULES_SURFACE_LIB, missing) and any
        # other failure to even start the subprocess.
        raise RulesSurfaceClassifierError(
            f"could not run {RULES_SURFACE_LIB} --classify: {exc}"
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.strip() or "(no stderr)"
        raise RulesSurfaceClassifierError(
            f"{RULES_SURFACE_LIB} --classify exited {result.returncode}, "
            f"cannot classify changed files: {stderr}"
        )
    return frozenset(line for line in result.stdout.splitlines() if line)


def rules_files_in(changed_files: list[str]) -> list[str]:
    """Changed files that are a rules surface, per tools/lib/rules-surface.sh -- preserves
    the input order (and any duplicates), matching the classifier's verdict against each
    entry rather than re-deciding it locally."""
    matched = _classify_rules_surface(tuple(changed_files))
    return [f for f in changed_files if f in matched]


# Machine-generated dependency PRs have no Issue, no behavioural claim and no agent
# provenance, and never will. They are still fully subject to build-and-test; what they
# are exempt from is the narrative that exists to make human and agent work reviewable.
# Deliberately an exact allowlist rather than a "looks like a bot" heuristic.
# Both spellings are required. GitHub's REST API reports a bot as "dependabot[bot]";
# `gh pr view --json author` goes through GraphQL and reports "app/dependabot". This tool
# reads the GraphQL form, but the REST form is what appears in webhooks and in most
# documentation, so a future caller will reach for it. Neither is a heuristic -- both are
# exact strings for the same single allowlisted bot.
#
# Deliberately NOT keyed on the author's `is_bot` flag: that would exempt every bot with
# access to the repository, which is a much wider door than this needs.
BOT_AUTHORS = frozenset({"dependabot[bot]", "app/dependabot"})

CLOSES = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)\b", re.IGNORECASE)

# A source locator names a page. "Matches the book" is the exact phrasing the PR template
# and the open-pr skill call unacceptable, and it previously passed policy unchallenged.
LOCATOR = re.compile(r"\bp(?:rinted)?\.?\s*pp?\.?\s*\d+|\bpp?\.\s*\d+", re.IGNORECASE)

SOURCE_MANIFEST = ".github/source-manifest.json"
ADR_DIR = "docs/decisions/"
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
FENCE = re.compile(r"```.*?```", re.DOTALL)

# Fenced blocks (``` or ~~~), indented code blocks, and inline code spans -- the three
# CommonMark shapes for "this text is quoted, not written." Not a Markdown parser (see
# module docstring): each is a plain regex, and none of it understands nesting. Good
# enough to stop a quoted "Closes #N" inside evidence from reading as a second
# declaration (#52) without pulling in a dependency for it.
_FENCED_BLOCK = re.compile(r"(```|~~~).*?\1", re.DOTALL)
_INDENTED_LINE = re.compile(r"^(?:[ ]{4,}|\t).*$", re.MULTILINE)
_INLINE_CODE = re.compile(r"`[^`\n]+`")


def strip_quoted(text: str) -> str:
    """Remove fenced blocks, indented code blocks, and inline code spans.

    Used only for the linked-Issue scan: a PR body that quotes generated output
    containing its own "Closes #N" (tools/review-packet.sh's whole purpose, per PR #51)
    must not be read as declaring a second Issue.
    """
    text = _FENCED_BLOCK.sub("", text)
    text = _INDENTED_LINE.sub("", text)
    text = _INLINE_CODE.sub("", text)
    return text


# A line that plausibly names a command someone actually ran: an optional shell prompt,
# optional leading `NAME=value` environment assignments (e.g. `CI=true`), then either a
# bare `tools/<anything>` or `scripts/<anything>` invocation -- which ages with the repo
# instead of needing a new entry every time a script is added -- or one of a handful of
# interpreter/VCS names that are not usually invoked through a repo-relative path.
COMMAND_LINE = re.compile(
    r"^\s*[$>]?\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*"
    r"(?:"
    r"(?:\./|/|~?/)?(?:scripts|tools)/\S+"
    r"|(?:\./|/|~?/)?(?:scripts/|tools/)?"
    r"(?:validate\.sh|doctor\.sh|dotnet|python3?|pytest|git|gh|make|repo-checks)\b"
    r")",
    re.MULTILINE,
)

# Phrases that assert success without evidence for it.
BARE_CLAIM = re.compile(
    r"^\W*(all\s+)?(the\s+)?(unit\s+|integration\s+)?tests?\s*"
    r"(all\s+)?(pass(ed|ing)?|are\s+green|green|ok)\W*$",
    re.IGNORECASE,
)


def strip_boilerplate(text: str) -> str:
    """Remove template comments so an untouched template reads as empty, not as content."""
    return HTML_COMMENT.sub("", text).strip()


def split_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in body.splitlines():
        heading = re.match(r"^#{1,3}\s+(.*?)\s*$", line)
        if heading:
            if current is not None:
                sections[current] = "\n".join(buffer)
            current = heading.group(1).strip().lower()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer)
    return sections


def section(sections: dict[str, str], name: str) -> str:
    return strip_boilerplate(sections.get(name.lower(), ""))


def is_effectively_empty(text: str) -> bool:
    """Empty, or nothing but placeholder tokens left over from the template."""
    cleaned = re.sub(r"^[\s\-*>#`]+$", "", text, flags=re.MULTILINE).strip()
    cleaned = re.sub(r"```\s*```", "", cleaned).strip()
    if not cleaned:
        return True
    # "Implemented by:" with nothing after it is an unfilled template line.
    residue = re.sub(r"^\s*[-*]?\s*\w[\w /]*:\s*$", "", cleaned, flags=re.MULTILINE).strip()
    return not residue


def is_na(text: str) -> bool:
    """True when the section leads with N/A.

    "N/A -- reason" is the correct form for non-rules work, so matching only a bare
    "N/A" would let any PR excuse itself from conformance simply by adding a reason.
    """
    stripped = text.strip()
    if not stripped:
        return False
    first_line = stripped.splitlines()[0].strip()
    return bool(re.match(r"^\W*n/?a\b", first_line, re.IGNORECASE))


def is_exempt(author: str | None) -> bool:
    """True for allowlisted bots. Narrow on purpose -- a broad bypass hollows out the policy."""
    return author is not None and author in BOT_AUTHORS


def check(body: str, changed_files: list[str], issue_labels, author: str | None = None) -> list[str]:
    """Return a list of policy failures. Empty means the PR passes."""
    if is_exempt(author):
        return []

    failures: list[str] = []
    sections = split_sections(body)

    # ------------------------------------------------------------- exactly one Issue
    # strip_quoted, not body: a PR quoting a generated packet that itself contains
    # "Closes #N" (evidence, not a declaration) must not count as a second linked Issue.
    linked = sorted(set(CLOSES.findall(strip_quoted(body))))
    if not linked:
        failures.append(
            'no linked Issue: the PR body must contain "Closes #NNN". '
            "Every PR closes exactly one Issue."
        )
    elif len(linked) > 1:
        failures.append(
            f"closes {len(linked)} Issues (#{', #'.join(linked)}); a PR must close exactly one. "
            "Split the work."
        )
    else:
        labels = issue_labels(linked[0])
        if labels is None:
            failures.append(f"Issue #{linked[0]} does not exist or is not visible")
        elif "state:needs-decision" in labels:
            failures.append(
                f"Issue #{linked[0]} is state:needs-decision and is not implementable. "
                "The open question must be resolved and recorded before implementation."
            )
        elif "state:blocked" in labels:
            failures.append(f"Issue #{linked[0]} is state:blocked")

    # ----------------------------------------------------------- required narrative
    for heading, hint in [
        ("Exact behavioral claim", "state what is true now that was not true before"),
        ("Scope", "state what changed and what nearby behaviour deliberately did not"),
        ("Known limitations", "state what remains unsupported; 'none' is a valid answer"),
        ("Agent provenance", "state which agent/model implemented and which verified"),
    ]:
        if is_effectively_empty(section(sections, heading)):
            failures.append(f'"{heading}" is empty -- {hint}')

    # ---------------------------------------------------------------- real evidence
    evidence = section(sections, "Tests and evidence")
    if is_effectively_empty(evidence):
        failures.append('"Tests and evidence" is empty -- paste the commands and results')
    else:
        prose = FENCE.sub("", evidence).strip()
        if not COMMAND_LINE.search(evidence):
            failures.append(
                '"Tests and evidence" names no command. "tests pass" is a claim, not '
                "evidence -- paste what you ran and what it printed."
            )
        elif any(BARE_CLAIM.match(line.strip()) for line in prose.splitlines() if line.strip()) \
                and not FENCE.search(evidence):
            failures.append('"Tests and evidence" asserts success without showing output')

    # ------------------------------------------------------------ rules conformance
    rules_files = rules_files_in(changed_files)
    if rules_files:
        conformance = section(sections, "Rules conformance")
        if is_effectively_empty(conformance):
            failures.append(
                f'"Rules conformance" is empty but {len(rules_files)} rules file(s) changed '
                f"({rules_files[0]}...). Cite the source locator and say what was verified."
            )
        elif is_na(conformance):
            failures.append(
                f'"Rules conformance" says N/A but {len(rules_files)} rules file(s) changed '
                f"({rules_files[0]}...). Rules changes require a source locator."
            )
        elif not LOCATOR.search(conformance):
            failures.append(
                '"Rules conformance" names no page. Cite the location, e.g. '
                '"SR6 Core / Tests / printed pp. 35-36 / PDF pp. 36-37". '
                '"Matches the book" is a claim, not a locator.'
            )

    # Changing the pinned source baseline is a deliberate decision, not an edit. Four
    # documents said so; nothing enforced it, though the changed-file list was already
    # here and the manifest was already flagged as rules work.
    if SOURCE_MANIFEST in changed_files:
        if not any(f.startswith(ADR_DIR) for f in changed_files):
            failures.append(
                f"{SOURCE_MANIFEST} changed but no ADR under {ADR_DIR} accompanies it. "
                "Changing the pinned source baseline requires its own Issue, PR and ADR "
                "-- see docs/decisions/0003-source-baseline.md."
            )

    return failures


# ------------------------------------------------------------------------------- gh


def gh_json(args: list[str]):
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False, timeout=30
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pr-policy.py")
    parser.add_argument("--pr", help="pull request number to check via gh")
    parser.add_argument("--body-file", help="read the PR body from this file instead")
    parser.add_argument("--changed-files", help="file listing changed paths, one per line")
    parser.add_argument(
        "--no-verify-issue", action="store_true", help="skip the GitHub Issue lookup"
    )
    parser.add_argument("--author", help="PR author login, when not reading from gh")
    parser.add_argument("--json", help="write a machine-readable evidence artifact here")
    args = parser.parse_args(argv)

    if args.pr:
        data = gh_json(["pr", "view", args.pr, "--json", "body,files,author"])
        if data is None:
            print(f"pr-policy: cannot read PR #{args.pr}", file=sys.stderr)
            return 2
        body = data.get("body") or ""
        changed = [f["path"] for f in data.get("files", [])]
        author = (data.get("author") or {}).get("login")
    elif args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
        changed = (
            [l.strip() for l in Path(args.changed_files).read_text(encoding="utf-8").splitlines() if l.strip()]
            if args.changed_files else []
        )
        author = args.author
    else:
        parser.error("one of --pr or --body-file is required")

    if args.no_verify_issue:
        def issue_labels(_number: str):
            return []
    else:
        def issue_labels(number: str):
            data = gh_json(["issue", "view", number, "--json", "labels"])
            if data is None:
                return None
            return [label["name"] for label in data.get("labels", [])]

    exempt = is_exempt(author)
    failures = check(body, changed, issue_labels, author)

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "pr": args.pr,
                    "author": author,
                    "exempt": exempt,
                    "passed": not failures,
                    "failureCount": len(failures),
                    "failures": failures,
                    "changedFiles": changed,
                    "rulesFilesChanged": rules_files_in(changed),
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

    if failures:
        print(f"pr-policy: {len(failures)} failure(s)\n")
        for failure in failures:
            print(f"  - {failure}")
        print("\nSee .github/pull_request_template.md")
        return 1

    if exempt:
        print(f"pr-policy: PASS (author '{author}' is an allowlisted bot; "
              "narrative requirements waived, build-and-test still applies)")
    else:
        print("pr-policy: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
