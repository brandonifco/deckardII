#!/usr/bin/env bash
# dotnet-env.sh -- resolve which .NET SDK this checkout should use.
#
# validate.sh and doctor.sh both need the exact same answer to "which dotnet". Inlining
# the lookup twice is exactly the failure tools/repo-checks.py's invariant-drift check
# exists to catch elsewhere in this repo: a fact restated in more than one place has
# already drifted once before. This file is the one definition; source it, never
# reimplement it.
#
# Resolution order, cheapest and most-local first:
#   1. <this checkout>/.dotnet      -- installed by scripts/bootstrap-dotnet.sh
#   2. <primary checkout>/.dotnet   -- the case every worktree tools/dispatch-agent.sh
#      creates will hit: a linked worktree has no .dotnet of its own, and one install
#      has to serve every worktree.
#   3. neither exists -- change nothing. This is the CI path: actions/setup-dotnet puts
#      the pinned SDK straight on PATH and no repo-local .dotnet/ ever exists. Do not
#      "fix" this branch; CI depends on it staying a no-op.
#
# Must be SOURCED, not executed: it has to run inside the caller's shell to change the
# caller's PATH. On return it sets, for doctor.sh to report:
#   DECKARDII_DOTNET_SOURCE   "repo-local" | "primary-checkout" | "path"
#   DECKARDII_DOTNET_HOME     the .dotnet directory used, empty when DECKARDII_DOTNET_SOURCE=path
#
# This file changes nothing when case 3 applies -- no PATH edit, no export -- which is
# the same "diagnose, do not repair" contract doctor.sh carries. It only ever prepends
# to PATH; it never touches a shell profile.

(return 0 2>/dev/null) || { echo "dotnet-env.sh must be sourced, not executed" >&2; exit 1; }

# Print the primary checkout root as seen from directory $1; print nothing and return 1
# when it cannot be determined. A linked worktree's --git-common-dir points at the
# primary checkout's shared .git directory, so its parent is the primary checkout root.
# That output can be relative to wherever git happened to run rather than to $1, so it
# is resolved against $1 explicitly rather than against the process's own cwd -- the
# exact bug .claude/hooks/primary-checkout-guard.py documents fixing.
deckardii_primary_checkout_root() {
  local from="$1" common
  common="$(git -C "$from" rev-parse --git-common-dir 2>/dev/null)" || return 1
  case "$common" in
    /*) : ;;
    *) common="$from/$common" ;;
  esac
  common="$(cd "$common" 2>/dev/null && pwd)" || return 1
  [[ "$(basename "$common")" == ".git" ]] || return 1
  dirname "$common"
}

# The repo root of wherever THIS file lives -- the primary checkout when sourced there,
# the worktree when sourced from one. Not the process cwd: validate.sh and doctor.sh
# already cd to their own repo root before sourcing this, but resolving from our own
# path keeps that assumption from being a second place this logic could drift from.
_deckardii_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

DECKARDII_DOTNET_SOURCE="path"
DECKARDII_DOTNET_HOME=""

if [[ -d "$_deckardii_repo_root/.dotnet" ]]; then
  DECKARDII_DOTNET_SOURCE="repo-local"
  DECKARDII_DOTNET_HOME="$_deckardii_repo_root/.dotnet"
else
  _deckardii_primary_root="$(deckardii_primary_checkout_root "$_deckardii_repo_root")" || _deckardii_primary_root=""
  if [[ -n "$_deckardii_primary_root" && -d "$_deckardii_primary_root/.dotnet" ]]; then
    DECKARDII_DOTNET_SOURCE="primary-checkout"
    DECKARDII_DOTNET_HOME="$_deckardii_primary_root/.dotnet"
  fi
fi

if [[ "$DECKARDII_DOTNET_SOURCE" != "path" ]]; then
  export DOTNET_ROOT="$DECKARDII_DOTNET_HOME"
  export PATH="$DECKARDII_DOTNET_HOME:$PATH"
fi

unset _deckardii_repo_root _deckardii_primary_root
