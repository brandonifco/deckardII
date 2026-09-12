#!/usr/bin/env bash
# bootstrap-dotnet.sh -- install the exact SDK global.json pins, no judgement call needed.
#
# ./scripts/validate.sh full cannot run at all until dotnet reports the pinned patch, and
# scripts/doctor.sh can only diagnose that gap, not close it. This script closes it:
#
#   ./scripts/bootstrap-dotnet.sh
#
# It installs into a REPO-LOCAL .dotnet/, in the PRIMARY checkout, never into $HOME and
# never system-wide. That is a deliberate, considered choice, not the path of least
# resistance:
#   - no global PATH edit is ever required, so the system SDK stays the default for
#     every other project on the machine;
#   - one install has to serve every worktree tools/dispatch-agent.sh creates, since all
#     implementation happens in a worktree and a worktree has no .dotnet of its own.
# The costs are accepted knowingly, not hidden: .dotnet/ is large, `git clean -xfd`
# would delete it, and Deckard's `dotnet` differs from the one on PATH. Both are written
# down in README.md so the next session does not have to rediscover them.
#
# It does NOT edit a shell profile, and will not tell you to. scripts/doctor.sh states
# plainly that it never changes the environment; a bootstrap that rewrites ~/.bashrc
# would make that untrue of the pair. scripts/lib/dotnet-env.sh is how validate.sh and
# doctor.sh find what this script installs, with no PATH change needed anywhere.
#
# No sudo, ever. Nothing outside the repository is touched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=lib/dotnet-env.sh
source "$SCRIPT_DIR/lib/dotnet-env.sh"

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GRN=$'\033[32m'; OFF=$'\033[0m'
else
  BOLD=""; RED=""; GRN=""; OFF=""
fi

die() { printf '%serror%s %s\n' "$RED" "$OFF" "$1" >&2; exit 1; }

# Read from global.json, never hardcode -- the same one-line idiom validate.sh and the
# CI workflow already use, so there is exactly one way this repo parses the pin.
PINNED="$(python3 -c 'import json;print(json.load(open("global.json"))["sdk"]["version"])')"

# Install into the PRIMARY checkout even when invoked from a worktree: every dispatched
# agent needs the same answer, and only one download should ever happen. This reuses
# scripts/lib/dotnet-env.sh's own primary-root lookup rather than reimplementing it.
PRIMARY_ROOT="$(deckard_primary_checkout_root "$REPO_ROOT")" \
  || die "not inside a git repository: $REPO_ROOT"
TARGET_DIR="$PRIMARY_ROOT/.dotnet"

sdk_installed() {
  [[ -x "$TARGET_DIR/dotnet" ]] || return 1
  "$TARGET_DIR/dotnet" --list-sdks 2>/dev/null | awk '{print $1}' | grep -qx "$PINNED"
}

# The strongest verification available: ask for --version from INSIDE the primary
# checkout, exactly as validate.sh's verify_sdk_pin does after sourcing dotnet-env.sh.
# A bootstrap that claims success without checking is the same class of defect as a gate
# that prints "ok" without running anything.
verify_resolves_to_pinned() {
  local reported
  reported="$(cd "$PRIMARY_ROOT" \
    && PATH="$TARGET_DIR:$PATH" DOTNET_ROOT="$TARGET_DIR" dotnet --version 2>&1)" \
    || { printf '%s\n' "$reported" >&2; return 1; }
  [[ "$reported" == "$PINNED" ]] || {
    printf 'dotnet --version reports %s, expected %s\n' "$reported" "$PINNED" >&2
    return 1
  }
}

if sdk_installed; then
  printf '%sok%s   %s already installed at %s -- skipping download.\n' \
    "$GRN" "$OFF" "$PINNED" "$TARGET_DIR"
else
  printf 'Installing .NET SDK %s into %s ...\n' "$PINNED" "$TARGET_DIR"
  mkdir -p "$TARGET_DIR"
  INSTALL_SCRIPT="$(mktemp -t dotnet-install-XXXXXX.sh)"
  trap 'rm -f "$INSTALL_SCRIPT"' EXIT
  curl -fsSL https://dot.net/v1/dotnet-install.sh -o "$INSTALL_SCRIPT" \
    || die "could not download dotnet-install.sh from https://dot.net/v1/dotnet-install.sh"
  chmod +x "$INSTALL_SCRIPT"
  # --install-dir keeps this entirely repo-local; no other flag touches PATH, a profile,
  # or anything outside $TARGET_DIR.
  "$INSTALL_SCRIPT" --version "$PINNED" --install-dir "$TARGET_DIR" \
    || die "dotnet-install.sh failed for version $PINNED"
fi

sdk_installed || die "install finished but $TARGET_DIR/dotnet does not report $PINNED in --list-sdks"
verify_resolves_to_pinned \
  || die "$TARGET_DIR/dotnet does not resolve global.json's pin from $PRIMARY_ROOT"

printf '\n%s%sInstalled .NET SDK %s%s\n' "$BOLD" "$GRN" "$PINNED" "$OFF"
printf '  location    %s\n' "$TARGET_DIR"
printf '  gitignored  yes -- git clean -xfd would delete it; rerun this script to restore it\n'
printf '\n./scripts/validate.sh full is now runnable, from this checkout and every worktree of it, with no further setup.\n'
