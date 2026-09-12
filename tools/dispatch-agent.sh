#!/usr/bin/env bash
# dispatch-agent.sh -- create an isolated worktree for one Issue.
#
#   tools/dispatch-agent.sh 42              create a worktree for Issue #42
#   tools/dispatch-agent.sh --cleanup 42    remove it after the PR merged
#   tools/dispatch-agent.sh --list          show live worktrees
#
# The primary checkout is for orchestration. Its steady state is: on main, clean. An
# implementation agent that develops in the primary checkout produces a repository where
# nobody can tell which change belongs to which Issue, and where two agents working at
# once corrupt each other's tree.
#
# Worktrees live OUTSIDE the repository. A worktree inside the repo eventually gets
# committed, scanned by a tool that did not expect it, or deleted by a clean step.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

WORKTREE_ROOT="${DECKARD_WORKTREE_ROOT:-$HOME/deckard-worktrees}"

# A worktree inside the repository eventually gets committed, scanned by a tool that did
# not expect it, or deleted by a clean step. The rule was previously prose only, and
# DECKARD_WORKTREE_ROOT could point anywhere.
assert_worktree_root_is_outside_repo() {
  local resolved
  resolved="$(mkdir -p "$WORKTREE_ROOT" 2>/dev/null; cd "$WORKTREE_ROOT" 2>/dev/null && pwd -P)" || {
    printf 'error: cannot create worktree root: %s\n' "$WORKTREE_ROOT" >&2; exit 1; }
  case "$resolved/" in
    "$REPO_ROOT"/*)
      printf 'error: DECKARD_WORKTREE_ROOT resolves inside the repository:\n' >&2
      printf '         %s\n       repo: %s\n\n' "$resolved" "$REPO_ROOT" >&2
      printf '       Worktrees must live outside the repo. Pick a path elsewhere.\n' >&2
      exit 1
      ;;
  esac
}

if [[ -t 1 ]]; then BOLD=$'\033[1m'; RED=$'\033[31m'; GRN=$'\033[32m'; OFF=$'\033[0m'
else BOLD=""; RED=""; GRN=""; OFF=""; fi

die() { printf '%serror%s %s\n' "$RED" "$OFF" "$1" >&2; exit 1; }

usage() {
  sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

# ------------------------------------------------------------------------ guards

assert_primary_checkout() {
  local common dir
  common="$(git rev-parse --git-common-dir)"
  dir="$(git rev-parse --git-dir)"
  [[ "$common" == "$dir" ]] || die "run this from the primary checkout, not a worktree"
}

slugify() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/[^a-z0-9]\+/-/g' -e 's/^-\+//' -e 's/-\+$//' \
    | cut -c1-40 \
    | sed -e 's/-\+$//'
}

# ------------------------------------------------------------------------ actions

do_list() {
  printf '%sLive worktrees%s\n' "$BOLD" "$OFF"
  git worktree list | sed 's/^/  /'
}

do_cleanup() {
  local issue="$1"
  local found=0
  while read -r path _; do
    [[ "$path" == "$REPO_ROOT" ]] && continue
    if [[ "$(basename "$path")" == issue-"$issue"-* ]]; then
      found=1
      printf 'Removing worktree %s\n' "$path"
      git worktree remove "$path" 2>/dev/null \
        || die "worktree has uncommitted changes: $path
       Inspect it, then force with: git worktree remove --force '$path'"
      printf '%sremoved%s %s\n' "$GRN" "$OFF" "$path"
    fi
  done < <(git worktree list --porcelain | awk '/^worktree /{print $2}')

  [[ "$found" -eq 1 ]] || die "no worktree found for Issue #$issue"
  git worktree prune

  local branch
  branch="$(git branch --list "issue-$issue-*" --format='%(refname:short)' | head -1)"
  if [[ -n "$branch" ]]; then
    if git branch -d "$branch" 2>/dev/null; then
      printf '%sdeleted%s branch %s (was merged)\n' "$GRN" "$OFF" "$branch"
    else
      printf 'branch %s kept -- not merged into main.\n' "$branch"
      printf 'Delete it deliberately with: git branch -D %s\n' "$branch"
    fi
  fi
}

do_create() {
  local issue="$1"

  assert_worktree_root_is_outside_repo
  command -v gh >/dev/null 2>&1 || die "gh is required to verify the Issue exists"

  local title state labels
  if ! title="$(gh issue view "$issue" --json title --jq .title 2>/dev/null)"; then
    die "Issue #$issue does not exist or is not visible.
       Work starts from an Issue. File one first -- GitHub is the only queue."
  fi
  state="$(gh issue view "$issue" --json state --jq .state)"
  labels="$(gh issue view "$issue" --json labels --jq '[.labels[].name] | join(",")')"

  [[ "$state" == "OPEN" ]] || die "Issue #$issue is $state. Reopen it or pick another."

  if [[ ",$labels," == *",state:needs-decision,"* ]]; then
    die "Issue #$issue is state:needs-decision and is not implementable.
       An implementation agent may not resolve the open question itself.
       Escalate to Brandon; once decided, the answer is persisted and the label changes."
  fi
  if [[ ",$labels," == *",state:blocked,"* ]]; then
    die "Issue #$issue is state:blocked. Resolve the blocker first."
  fi

  local branch path
  branch="issue-$issue-$(slugify "$title")"
  path="$WORKTREE_ROOT/$branch"

  [[ -e "$path" ]] && die "worktree path already exists: $path
       Clean it up first:  tools/dispatch-agent.sh --cleanup $issue"

  # Base the branch on the freshest origin/main we can see, but never fail because the
  # network is down -- an offline agent should still get a worktree off local main.
  local base="main"
  if git remote get-url origin >/dev/null 2>&1 && git fetch -q origin main 2>/dev/null; then
    base="origin/main"
  else
    printf 'note: could not fetch origin; basing on local main\n' >&2
  fi

  mkdir -p "$WORKTREE_ROOT"
  # `git worktree add -b` creates the branch and checks it out IN THE WORKTREE.
  # The primary checkout is never moved off main by this command.
  git worktree add -b "$branch" "$path" "$base" >/dev/null

  printf '\n%sWorktree ready%s\n' "$BOLD$GRN" "$OFF"
  printf '  issue    #%s  %s\n' "$issue" "$title"
  printf '  labels   %s\n' "${labels:-none}"
  printf '  branch   %s\n' "$branch"
  printf '  path     %s\n' "$path"
  printf '  base     %s\n\n' "$base"
  printf 'Work there, not here:\n  cd %s\n\n' "$path"
  printf 'Before opening the PR:\n  ./scripts/validate.sh full\n'
  printf 'The PR must say:  Closes #%s\n' "$issue"
}

# --------------------------------------------------------------------------- main

[[ $# -eq 0 ]] && usage 1

case "${1:-}" in
  -h|--help) usage 0 ;;
  --list) assert_primary_checkout; do_list ;;
  --cleanup)
    [[ $# -eq 2 ]] || die "usage: dispatch-agent.sh --cleanup <issue-number>"
    [[ "$2" =~ ^[0-9]+$ ]] || die "issue number must be numeric, got: $2"
    assert_primary_checkout; do_cleanup "$2" ;;
  *)
    [[ "$1" =~ ^[0-9]+$ ]] || die "issue number must be numeric, got: $1"
    assert_primary_checkout; do_create "$1" ;;
esac
