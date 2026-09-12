#!/usr/bin/env bash
# rules-surface.sh -- the one definition of what counts as a repository "rules surface"
# change, shared by the review packet, PR policy, and rules-conformance merge gate.
#
# The bare framework deliberately assumes no runtime/data/test project names. A consuming
# ruleset may declare additional rules-surface paths in:
#
#   .github/rules-surface-paths.txt
#
# One repository-relative literal path is allowed per line. Blank lines and lines
# beginning with # are ignored. An entry ending in "/" matches that directory and all
# descendants; any other entry matches one exact file. Globs and regular expressions are
# deliberately unsupported: another ruleset genuinely needs different paths, not a new
# pattern language.
#
# .github/source-manifest.json is always a rules surface when it exists. The bare
# framework may legitimately have no source manifest and no configured rules paths yet.
#
# `git diff --name-status` puts a rename or copy's destination on the SAME line as its
# source: "R100<TAB>old/path<TAB>new/path" (copies are "C<score>", same two-path shape).
# `rules_surface_touched` strips the status column and checks every remaining path, so a
# rename in either direction is visible.
#
# packages.lock.json is excluded by exact basename. It is dependency metadata, not rules
# content, even when a consuming ruleset stores it beside a rules project.
#
# No `set -euo pipefail` at file scope: this file is normally sourced, and a sourced
# file's `set` calls would change the calling shell's options.

RULES_SURFACE_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RULES_SURFACE_CONFIG="${FRAMEWORK_RULES_SURFACE_CONFIG:-$RULES_SURFACE_REPO_ROOT/.github/rules-surface-paths.txt}"

RULES_SURFACE_BUILTIN_EXACT_PATHS=(
  ".github/source-manifest.json"
)

RULES_SURFACE_EXCLUDED_BASENAMES=(
  "packages.lock.json"
)

rules_surface_excluded_basename() {
  local base="$1" excluded
  for excluded in "${RULES_SURFACE_EXCLUDED_BASENAMES[@]}"; do
    if [[ "$base" == "$excluded" ]]; then
      return 0
    fi
  done
  return 1
}

rules_surface_config_entry_valid() {
  local entry="$1"
  local trimmed="${entry%/}"

  [[ -n "$entry" ]] || return 1
  [[ "$entry" != /* ]] || return 1
  [[ "$trimmed" != "." && "$trimmed" != ".." ]] || return 1
  [[ "$trimmed" != ../* && "$trimmed" != */../* && "$trimmed" != */.. ]] || return 1
  [[ "$trimmed" != ".git" && "$trimmed" != .git/* ]] || return 1
  return 0
}

rules_surface_declared_path_matches() {
  local path="$1" builtin entry

  for builtin in "${RULES_SURFACE_BUILTIN_EXACT_PATHS[@]}"; do
    if [[ "$path" == "$builtin" ]]; then
      return 0
    fi
  done

  [[ -f "$RULES_SURFACE_CONFIG" ]] || return 1

  while IFS= read -r entry || [[ -n "$entry" ]]; do
    entry="${entry%$'\r'}"
    [[ -z "$entry" || "$entry" == \#* ]] && continue

    if ! rules_surface_config_entry_valid "$entry"; then
      printf 'rules-surface: invalid entry in %s: %s\n' "$RULES_SURFACE_CONFIG" "$entry" >&2
      return 2
    fi

    if [[ "$entry" == */ ]]; then
      [[ "$path" == "$entry"* ]] && return 0
    elif [[ "$path" == "$entry" ]]; then
      return 0
    fi
  done < "$RULES_SURFACE_CONFIG"

  return 1
}

rules_surface_path_matches() {
  local path="$1" status

  if rules_surface_declared_path_matches "$path"; then
    status=0
  else
    status=$?
  fi

  [[ "$status" -eq 0 ]] || return "$status"

  if rules_surface_excluded_basename "${path##*/}"; then
    return 1
  fi

  return 0
}

rules_surface_touched() {
  local path status

  while IFS= read -r path; do
    [[ -z "$path" ]] && continue

    if rules_surface_path_matches "$path"; then
      return 0
    else
      status=$?
      [[ "$status" -eq 1 ]] || return "$status"
    fi
  done < <(cut -f2- <<<"$1" | tr '\t' '\n')

  return 1
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -euo pipefail

  if [[ "${1:-}" == "--print-excluded-basenames" ]]; then
    printf '%s\n' "${RULES_SURFACE_EXCLUDED_BASENAMES[@]}"
    exit 0
  fi

  if [[ "${1:-}" == "--classify" ]]; then
    while IFS= read -r path; do
      [[ -z "$path" ]] && continue

      if rules_surface_path_matches "$path"; then
        printf '%s\n' "$path"
      else
        status=$?
        [[ "$status" -eq 1 ]] || exit "$status"
      fi
    done
    exit 0
  fi

  input="$(cat)"
  if rules_surface_touched "$input"; then
    exit 0
  else
    status=$?
    exit "$status"
  fi
fi
