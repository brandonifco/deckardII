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
# sourced above, already defines deckard_primary_checkout_root for this exact purpose
# (its own .dotnet/ lookup has the identical primary-checkout-vs-worktree shape); reuse
# it rather than a third copy of the same git-common-dir logic in this file too.
PRIMARY_ROOT="$(deckard_primary_checkout_root "$REPO_ROOT")" || PRIMARY_ROOT="$REPO_ROOT"

if [[ -t 1 ]]; then BOLD=$'\033[1m'; RED=$'\033[31m'; YEL=$'\033[33m'; GRN=$'\033[32m'; OFF=$'\033[0m'
else BOLD=""; RED=""; YEL=""; GRN=""; OFF=""; fi

PROBLEMS=0
WARNINGS=0

ok()   { printf '  %sok%s    %-22s %s\n' "$GRN" "$OFF" "$1" "${2:-}"; }
warn() { printf '  %swarn%s  %-22s %s\n' "$YEL" "$OFF" "$1" "${2:-}"; WARNINGS=$((WARNINGS+1)); }
bad()  { printf '  %sFAIL%s  %-22s %s\n' "$RED" "$OFF" "$1" "${2:-}"; PROBLEMS=$((PROBLEMS+1)); }
hint() { printf '        %s\n' "$1"; }
section() { printf '\n%s%s%s\n' "$BOLD" "$1" "$OFF"; }

printf '%sDeckard environment report%s\n' "$BOLD" "$OFF"

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

case "$DECKARD_DOTNET_SOURCE" in
  repo-local)        dotnet_origin="repo-local .dotnet/" ;;
  primary-checkout)  dotnet_origin="primary checkout's .dotnet/ ($DECKARD_DOTNET_HOME)" ;;
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
      hint "Deckard pins an exact patch with rollForward=disable."
      hint "./scripts/bootstrap-dotnet.sh"
    fi
  else
    bad ".NET SDK" "cannot satisfy global.json's pin of $pinned (from $dotnet_origin)"
    hint "Deckard pins an exact patch with rollForward=disable."
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
    # extracted on different machines are provably identical. Deckard does not vendor
    # Poppler, so this cannot be forced to match -- see docs/source-handling.md.
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

read -r SRC_ID SRC_ENV SRC_EDITION SRC_PAGES <<<"$(python3 - <<'PY' 2>/dev/null || echo "? ? ? ?"
import json
e = json.load(open(".github/source-manifest.json"))["sources"][0]
print(e["sourceId"], e["envVar"], e["edition"].replace(" ", "_"), e["pdfPageCount"])
PY
)"
ok "manifest" "$SRC_ID -- ${SRC_EDITION//_/ } (${SRC_PAGES}p)"

# source.local.json is looked up in the primary checkout (PRIMARY_ROOT above), the same
# place tools/source-slice.py resolves it from, so this reports the same source state
# whether run from a worktree or from the primary checkout itself. Say so plainly when
# the two differ, so "not configured" from a worktree does not read as "look here".
LOCAL_CONFIG_PATH="$PRIMARY_ROOT/source.local.json"
if [[ "$PRIMARY_ROOT" != "$REPO_ROOT" ]]; then
  hint "(source.local.json is read from the primary checkout: $PRIMARY_ROOT)"
fi

if [[ -n "${!SRC_ENV:-}" ]]; then
  CONFIGURED="${!SRC_ENV}"; ORIGIN="\$$SRC_ENV"
elif [[ -f "$LOCAL_CONFIG_PATH" ]]; then
  CONFIGURED="$(python3 -c "import json;print(json.load(open('$LOCAL_CONFIG_PATH')).get('$SRC_ID',''))" 2>/dev/null)"
  ORIGIN="source.local.json"
else
  CONFIGURED=""; ORIGIN=""
fi

if [[ -z "$CONFIGURED" ]]; then
  warn "$SRC_ENV" "not configured"
  hint "Rules work needs it; everything else (build, tests, CI) does not."
  hint "export $SRC_ENV=/absolute/path/to/your/own/copy.pdf"
  hint "or create source.local.json in the primary checkout (gitignored):"
  hint "  { \"$SRC_ID\": \"/absolute/path.pdf\" } -> $LOCAL_CONFIG_PATH"
elif [[ ! -f "$CONFIGURED" ]]; then
  bad "$ORIGIN" "points at a missing file"
  hint "configured value does not exist on disk"
else
  # Report the filename only. The full local path is deliberately not printed:
  # doctor output gets pasted into Issues and PRs.
  ok "$ORIGIN" "-> $(basename "$CONFIGURED")"
  if out="$(tools/source-slice.py --verify-only 2>&1)"; then
    ok "sha256" "matches the pinned baseline"
  else
    bad "sha256" "does NOT match the pinned baseline"
    printf '%s\n' "$out" | sed 's/^/        /' | head -6
    hint "This is the wrong printing or a corrupt file. Do not edit the manifest."
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
    hint "The rulebook must never be committed. See docs/source-handling.md."
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
