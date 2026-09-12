#!/usr/bin/env bash
# doctor.sh -- report the state of this development environment. Never change it.
#
# doctor.sh diagnoses; it does not repair. It will not install a package, create a
# config file, or export a variable for you, because an environment that silently
# fixes itself is an environment nobody can reason about. Every failure below prints
# the exact command or edit that resolves it.
#
# It sources scripts/lib/dotnet-env.sh below to resolve the SDK the same way
# validate.sh does, which prepends to PATH and sets DOTNET_ROOT -- but only inside this
# script's own process. Nothing on disk changes and the invoking shell's environment is
# untouched once doctor.sh exits; the "never change it" contract above still holds.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck source=lib/dotnet-env.sh
source "$REPO_ROOT/scripts/lib/dotnet-env.sh"

# source.local.json is gitignored and lives in the primary checkout; resolving it
# against REPO_ROOT (this checkout's own root) made it invisible from every worktree --
# the one place CLAUDE.md requires implementation to happen (#57). dotnet-env.sh, just
# sourced above, already defines framework_primary_checkout_root for this exact purpose
# (its own .dotnet/ lookup has the identical primary-checkout-vs-worktree shape); reuse
# it rather than a third copy of the same git-common-dir logic in this file too.
PRIMARY_ROOT="$(framework_primary_checkout_root "$REPO_ROOT")" || PRIMARY_ROOT="$REPO_ROOT"

if [[ -t 1 ]]; then BOLD=$'\033[1m'; RED=$'\033[31m'; YEL=$'\033[33m'; GRN=$'\033[32m'; OFF=$'\033[0m'
else BOLD=""; RED=""; YEL=""; GRN=""; OFF=""; fi

PROBLEMS=0
WARNINGS=0

ok()   { printf '  %sok%s    %-22s %s\n' "$GRN" "$OFF" "$1" "${2:-}"; }
warn() { printf '  %swarn%s  %-22s %s\n' "$YEL" "$OFF" "$1" "${2:-}"; WARNINGS=$((WARNINGS+1)); }
bad()  { printf '  %sFAIL%s  %-22s %s\n' "$RED" "$OFF" "$1" "${2:-}"; PROBLEMS=$((PROBLEMS+1)); }
hint() { printf '        %s\n' "$1"; }
section() { printf '\n%s%s%s\n' "$BOLD" "$1" "$OFF"; }

REPO_NAME="$(basename "$REPO_ROOT")"
printf '%s%s environment report%s\n' "$BOLD" "$REPO_NAME" "$OFF"

# ------------------------------------------------------------------------ tooling
section "Tooling"

if command -v git >/dev/null 2>&1; then ok "git" "$(git --version | awk '{print $3}')"
else bad "git" "not found"; hint "sudo apt install git"; fi

if command -v gh >/dev/null 2>&1; then
  gh_ver="$(gh --version 2>/dev/null | head -1 | awk '{print $3}')"
  if gh auth status >/dev/null 2>&1; then
    ok "gh" "$gh_ver (authenticated as $(gh api user --jq .login 2>/dev/null || echo '?'))"
  else
    bad "gh" "$gh_ver (not authenticated)"; hint "gh auth login"
  fi
else bad "gh" "not found"; hint "https://cli.github.com"; fi

case "$FRAMEWORK_DOTNET_SOURCE" in
  repo-local)        dotnet_origin="repo-local .dotnet/" ;;
  primary-checkout)  dotnet_origin="primary checkout's .dotnet/ ($FRAMEWORK_DOTNET_HOME)" ;;
  *)                 dotnet_origin="PATH" ;;
esac

if command -v dotnet >/dev/null 2>&1; then
  pinned="$(python3 -c 'import json;print(json.load(open("global.json"))["sdk"]["version"])' 2>/dev/null || echo '?')"
  # Branch on exit status, not on salvaging stdout: when no installed SDK satisfies
  # global.json, the muxer writes the INSTALLED SDK LIST to stdout and the real "no
  # compatible SDK" error to stderr, then exits non-zero. `2>/dev/null` discards the
  # real error and `actual` is left holding a list of versions dotnet never reported as
  # its own -- printing that as "have $actual" is a fabricated version string, not a
  # diagnosis.
  if actual="$(dotnet --version 2>/dev/null)"; then
    if [[ "$pinned" == "$actual" ]]; then
      ok ".NET SDK" "$actual (matches global.json pin, from $dotnet_origin)"
    else
      bad ".NET SDK" "have $actual, global.json pins $pinned (from $dotnet_origin)"
      hint "This repository pins an exact patch with rollForward=disable."
      hint "./scripts/bootstrap-dotnet.sh"
    fi
  else
    bad ".NET SDK" "cannot satisfy global.json's pin of $pinned (from $dotnet_origin)"
    hint "This repository pins an exact patch with rollForward=disable."
    hint "./scripts/bootstrap-dotnet.sh"
  fi
else bad ".NET SDK" "not found"; fi

if command -v python3 >/dev/null 2>&1; then ok "python3" "$(python3 --version | awk '{print $2}')"
else bad "python3" "not found -- source tooling requires it"; fi

if command -v pdftotext >/dev/null 2>&1; then
  poppler_actual="$(pdftotext -v 2>&1 | head -1 | awk '{print $3}')"
  poppler_pinned="$(python3 -c 'import json;print(json.load(open(".github/poppler-version.json"))["pdftotext"])' 2>/dev/null || echo '?')"
  if [[ "$poppler_actual" == "$poppler_pinned" ]]; then
    ok "pdftotext" "$poppler_actual (matches CI's pin)"
  else
    # Not `bad`: this never blocks a build or a test, only whether two packets
    # extracted on different machines are provably identical. This repository does not vendor
    # Poppler, so this cannot be forced to match across every developer machine.
    warn "pdftotext" "$poppler_actual (CI pins $poppler_pinned in .github/poppler-version.json)"
    hint "Every packet still records its own extractor/extractorVersion/bodySha256, so a"
    hint "real divergence stays visible and citable rather than silently assumed away."
  fi
else
  bad "pdftotext" "not found -- source-slice.py cannot extract"
  hint "sudo apt install poppler-utils"
fi

# ------------------------------------------------------------- authoritative source
section "Authoritative source"

MANIFEST_PATH="$REPO_ROOT/.github/source-manifest.json"
LOCAL_CONFIG_PATH="$PRIMARY_ROOT/source.local.json"

if [[ ! -f "$MANIFEST_PATH" ]]; then
  ok "manifest" "none declared (source-backed rules work not configured)"
else
  MANIFEST_STATUS=0
  MANIFEST_LINES="$(python3 - "$MANIFEST_PATH" 2>&1 <<'PYDOC'
import json
import sys

path = sys.argv[1]
try:
    manifest = json.load(open(path, encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    print(f"invalid source manifest: {exc}", file=sys.stderr)
    raise SystemExit(1)

sources = manifest.get("sources", [])
if not isinstance(sources, list):
    print("source manifest field 'sources' must be an array", file=sys.stderr)
    raise SystemExit(1)

for entry in sources:
    required = ("sourceId", "envVar", "edition", "pdfPageCount")
    missing = [key for key in required if key not in entry]
    if missing:
        print(
            f"source manifest entry missing required field(s): {', '.join(missing)}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    values = (
        str(entry["sourceId"]),
        str(entry["envVar"]),
        str(entry["edition"]),
        str(entry["pdfPageCount"]),
    )
    print("\t".join(value.replace("\t", " ").replace("\n", " ") for value in values))
PYDOC
)" || MANIFEST_STATUS=$?

  if [[ "$MANIFEST_STATUS" -ne 0 ]]; then
    bad "manifest" "invalid"
    printf '%s\n' "$MANIFEST_LINES" | sed 's/^/        /' | head -6
  elif [[ -z "$MANIFEST_LINES" ]]; then
    ok "manifest" "0 authoritative sources declared"
  else
    SOURCE_COUNT="$(printf '%s\n' "$MANIFEST_LINES" | wc -l | tr -d ' ')"
    ok "manifest" "$SOURCE_COUNT authoritative source(s) declared"

    # source.local.json lives in the primary checkout so one local configuration is
    # visible from every linked worktree.
    if [[ "$PRIMARY_ROOT" != "$REPO_ROOT" ]]; then
      hint "(source.local.json is read from the primary checkout: $PRIMARY_ROOT)"
    fi

    while IFS=$'\t' read -r SRC_ID SRC_ENV SRC_EDITION SRC_PAGES; do
      [[ -n "$SRC_ID" ]] || continue
      ok "source" "$SRC_ID -- $SRC_EDITION (${SRC_PAGES}p)"

      if [[ -n "${!SRC_ENV:-}" ]]; then
        CONFIGURED="${!SRC_ENV}"; ORIGIN="\$$SRC_ENV"
      elif [[ -f "$LOCAL_CONFIG_PATH" ]]; then
        CONFIGURED="$(python3 -c "import json;print(json.load(open('$LOCAL_CONFIG_PATH')).get('$SRC_ID',''))" 2>/dev/null)"
        ORIGIN="source.local.json"
      else
        CONFIGURED=""; ORIGIN=""
      fi

      if [[ -z "$CONFIGURED" ]]; then
        warn "$SRC_ID" "$SRC_ENV not configured"
        hint "Source-backed rules work for this source needs a local copy; build/tests/CI do not."
        hint "export $SRC_ENV=/absolute/path/to/your/own/copy.pdf"
        hint "or add to source.local.json in the primary checkout (gitignored):"
        hint "  { \"$SRC_ID\": \"/absolute/path.pdf\" } -> $LOCAL_CONFIG_PATH"
      elif [[ ! -f "$CONFIGURED" ]]; then
        bad "$SRC_ID" "$ORIGIN points at a missing file"
        hint "configured value does not exist on disk"
      else
        # Report only the basename; doctor output is routinely pasted into Issues and PRs.
        ok "$SRC_ID" "$ORIGIN -> $(basename "$CONFIGURED")"
        if out="$(tools/source-slice.py --source-id "$SRC_ID" --verify-only 2>&1)"; then
          ok "sha256" "$SRC_ID matches the pinned baseline"
        else
          bad "sha256" "$SRC_ID does NOT match the pinned baseline"
          printf '%s\n' "$out" | sed 's/^/        /' | head -6
          hint "The configured file is not the pinned source. Do not edit the manifest to make it pass."
        fi
      fi
    done <<< "$MANIFEST_LINES"
  fi
fi

# ----------------------------------------------------------------------- checkout
section "Checkout"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  bad "git repository" "not a git repository"
else
  # symbolic-ref, not rev-parse: on an unborn branch (a fresh repo with no commits)
  # rev-parse --abbrev-ref HEAD reports the literal string "HEAD".
  BRANCH="$(git symbolic-ref --short -q HEAD || git rev-parse --short HEAD 2>/dev/null || echo detached)"
  IS_WORKTREE="no"
  git_common="$(git rev-parse --git-common-dir 2>/dev/null)"
  git_dir="$(git rev-parse --git-dir 2>/dev/null)"
  [[ "$git_common" != "$git_dir" ]] && IS_WORKTREE="yes"

  if [[ "$IS_WORKTREE" == "yes" ]]; then
    ok "role" "linked worktree (implementation work belongs here)"
    ok "branch" "$BRANCH"
  else
    ok "role" "primary checkout (orchestration only)"
    if [[ "$BRANCH" == "main" ]]; then
      ok "branch" "main"
    else
      bad "branch" "$BRANCH -- the primary checkout should sit on main"
      hint "Feature branches belong in a worktree: tools/dispatch-agent.sh <issue>"
    fi
  fi

  if [[ -z "$(git status --porcelain 2>/dev/null)" ]]; then
    ok "working tree" "clean"
  else
    count="$(git status --porcelain | wc -l | tr -d ' ')"
    if [[ "$IS_WORKTREE" == "yes" ]]; then
      ok "working tree" "$count change(s) -- expected in a worktree"
    else
      warn "working tree" "$count uncommitted change(s) in the primary checkout"
      hint "The primary checkout's steady state is: main, clean."
    fi
  fi

  if git remote get-url origin >/dev/null 2>&1; then
    ok "origin" "$(git remote get-url origin)"
  else
    warn "origin" "no remote configured"
  fi

  if [[ -n "$(git ls-files '*.pdf' 2>/dev/null)" ]]; then
    bad "source boundary" "a PDF is tracked in git"
    hint "Authoritative source PDFs must never be committed."
  else
    ok "source boundary" "no rulebook tracked"
  fi
fi

# ------------------------------------------------------------------------ verdict
section "Summary"
if [[ "$PROBLEMS" -eq 0 && "$WARNINGS" -eq 0 ]]; then
  printf '  %s%sEnvironment is fully configured.%s\n' "$BOLD" "$GRN" "$OFF"
elif [[ "$PROBLEMS" -eq 0 ]]; then
  printf '  %s%s%d warning(s), 0 blocking problems.%s\n' "$BOLD" "$YEL" "$WARNINGS" "$OFF"
  printf '  Build, tests and CI are unaffected; rules work may be blocked.\n'
else
  printf '  %s%s%d blocking problem(s), %d warning(s).%s\n' "$BOLD" "$RED" "$PROBLEMS" "$WARNINGS" "$OFF"
fi
echo
exit $(( PROBLEMS > 0 ? 1 : 0 ))
