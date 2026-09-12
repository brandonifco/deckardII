#!/usr/bin/env bash
# new-issue.sh -- file a structured Deckard Issue from the command line.
#
#   tools/new-issue.sh --title "Implement PCG32 random source" \
#                      --label area:core --label phase:1-kernel
#
# GitHub's web Issue Forms are bypassed entirely by `gh issue create`, which is how
# agents file Issues. This script is where the structure actually comes from, so that an
# agent-filed Issue and a human-filed one look the same six months later.
#
# Opens $EDITOR with the template pre-filled. Add --dry-run to print it instead.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TITLE=""
LABELS=()
DRY_RUN=0

die() { printf 'error: %s\n' "$1" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --title) TITLE="${2:-}"; shift 2 ;;
    --label) LABELS+=("${2:-}"); shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$TITLE" ]] || die "--title is required"
command -v gh >/dev/null 2>&1 || die "gh is required"

TEMPLATE="$(cat <<'TPL'
## Purpose

<!-- Why does this Issue exist? What becomes possible when it closes? -->

## Source

<!-- RULES WORK: SR6 Core / <section> / printed p. X / PDF p. Y
     Identify the location precisely enough to implement from. Cite it; do not paste it.
     NON-RULES WORK: N/A -->

N/A

## Exact scope

<!-- Precisely what is being built. One concern. If it needs "and", it is two Issues. -->

## Non-goals

<!-- What this Issue deliberately does NOT do. This is what keeps the PR reviewable. -->

## Acceptance criteria

<!-- Observable conditions that make this Issue closeable. -->

## Required tests/evidence

<!-- What must be demonstrated, and how. Where the book prints a finite table, the whole
     table is verified, not a sample. -->

## Dependencies

<!-- Issues, ADRs or phases this depends on. "None" is a valid answer. -->

None.

## Known ambiguity

<!-- None, or an explicit description. If the source is genuinely ambiguous this Issue is
     state:needs-decision and an implementation agent may not resolve it. -->

None.

## Risk

<!-- What breaks if this is done wrong, and how far the damage spreads. -->
TPL
)"

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf 'Title: %s\nLabels: %s\n\n%s\n' "$TITLE" "${LABELS[*]:-none}" "$TEMPLATE"
  exit 0
fi

BODY_FILE="$(mktemp -t deckard-issue-XXXXXX.md)"
trap 'rm -f "$BODY_FILE"' EXIT
printf '%s\n' "$TEMPLATE" > "$BODY_FILE"

"${EDITOR:-nano}" "$BODY_FILE"

# A body that is still entirely comments means the editor was closed without writing.
if ! grep -qvE '^\s*(<!--.*|.*-->|#.*|N/A|None\.|)\s*$' "$BODY_FILE"; then
  die "issue body is still empty -- nothing filed"
fi

# A mechanics Issue must identify its source location precisely enough to implement
# from. An Issue that reaches an agent without one produces a mechanic written from
# memory, which is the single thing this project's source handling exists to prevent.
IS_MECHANICS=0
for label in "${LABELS[@]:-}"; do
  case "$label" in area:rules|area:data) IS_MECHANICS=1 ;; esac
done

if [[ "$IS_MECHANICS" -eq 1 ]]; then
  SOURCE_SECTION="$(awk '/^## Source/{f=1;next} /^## /{f=0} f' "$BODY_FILE" \
                    | grep -vE '^\s*(<!--|.*-->)' || true)"
  if ! printf '%s' "$SOURCE_SECTION" | grep -qiE 'p(rinted)?\.? ?p?\.? *[0-9]+'; then
    die "this Issue is labelled area:rules or area:data but its ## Source section names
       no page. A mechanics Issue must cite its location, e.g.

         SR6 Core / Tests / printed pp. 35-36 / PDF pp. 36-37

       Locate it first -- tools/source-slice.py can confirm a range with --expect.
       Nothing was filed."
  fi
fi

ARGS=(issue create --title "$TITLE" --body-file "$BODY_FILE")
for label in "${LABELS[@]:-}"; do
  [[ -n "$label" ]] && ARGS+=(--label "$label")
done

gh "${ARGS[@]}"
