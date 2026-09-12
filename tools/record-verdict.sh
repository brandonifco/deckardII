#!/usr/bin/env bash
# record-verdict.sh -- record a rules-conformance verdict against one commit.
#
#   tools/record-verdict.sh --sha <head-sha> --reviewer rules-conformance \
#     --verdict pass --packet-sha256 <bodySha256 hex> --pages 44-47
#   tools/record-verdict.sh --sha <head-sha> --reviewer codex \
#     --verdict fail --packet-sha256 <hex> --pages 44-47 --notes "row 9 disagrees"
#   tools/record-verdict.sh --sha <head-sha> --reviewer in-house-independent \
#     --verdict pass --packet-sha256 <hex> --pages 44-47 --notes "codex, gemini unreachable"
#   tools/record-verdict.sh --sha <head-sha> --reviewer rules-conformance \
#     --verdict pass --packet-sha256 <hex> --pages 44-47 --pr 108 --rerun-gate
#
# Issue #41: a rules-conformance review that lives only in an agent's chat transcript is
# a claim, not a merge gate. This is the ONE place a verdict becomes a fact GitHub can
# check: a commit status, posted directly against the head SHA it was verified against.
# tools/rules-conformance-gate.py reads these back (as
# repos/{owner}/{repo}/commits/{sha}/status) when it decides whether a PR touching a
# rules surface may merge.
#
# A commit status is intrinsically tied to one SHA -- there is no "amend the PR and keep
# the verdict" failure mode to guard against separately, because amending produces a new
# SHA that simply has no status of its own yet.
#
# `--reviewer rules-conformance` is the in-house agent's verdict, always required. The
# independent verdict a risk:rules-conformance Issue also requires (AGENTS.md,
# docs/agent-team.md) is not pinned to one vendor: it follows an ordered fallback chain --
# `codex` first, then `gemini`, then `in-house-independent` only when neither vendor is
# reachable (Issue #83, ADR 0010-independent-verdict-fallback-chain.md). Each reviewer
# posts to its own context (deckard-verdict/<reviewer>), so a merged commit's statuses
# name which vendor actually produced the second verdict rather than hiding a
# same-vendor fallback behind a generic name. `in-house-independent` is the fallback of
# last resort, not an equivalent option: a second reviewer from the same model family as
# the implementer tends to reproduce the implementer's misreading, which is the entire
# reason a cross-vendor verdict is required in the first place (AGENTS.md, ADR 0010).
#
# This script does not and cannot verify WHICH model actually produced a verdict, or
# choose one on the caller's behalf: that a human or agent invoking this with `--reviewer
# codex` actually ran Codex rather than a different reviewer, is a process obligation
# this file records but does not enforce -- the caller decides which reviewer ran and
# this script only validates and records that (Issue #83 non-goals: no automatic vendor
# selection here). Say so plainly rather than implying otherwise (docs/agent-team.md, and
# Issue #12's standard).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SHA=""
REVIEWER=""
VERDICT=""
PACKET_SHA256=""
PAGES=""
NOTES=""
REPO=""
PR=""
RERUN_GATE=0

die() { printf 'error: %s\n' "$1" >&2; exit 1; }

usage() {
  sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) SHA="${2:-}"; shift 2 ;;
    --reviewer) REVIEWER="${2:-}"; shift 2 ;;
    --verdict) VERDICT="${2:-}"; shift 2 ;;
    --packet-sha256) PACKET_SHA256="${2:-}"; shift 2 ;;
    --pages) PAGES="${2:-}"; shift 2 ;;
    --notes) NOTES="${2:-}"; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    --pr) PR="${2:-}"; shift 2 ;;
    --rerun-gate) RERUN_GATE=1; shift ;;
    -h|--help) usage 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

command -v gh >/dev/null 2>&1 || die "gh is required"

[[ -n "$SHA" ]] || die "--sha is required -- the exact head commit this verdict covers"
[[ "$SHA" =~ ^[0-9a-fA-F]{7,40}$ ]] || die "--sha does not look like a commit SHA: $SHA"

# rules-conformance is the in-house verdict, always required. codex, gemini and
# in-house-independent are the independent verdict's ordered fallback chain (Issue #83,
# ADR 0010-independent-verdict-fallback-chain.md) -- codex first, then gemini, then
# in-house-independent only when neither vendor is reachable. This script does not
# choose among them: the caller names which one actually ran.
case "$REVIEWER" in
  rules-conformance|codex|gemini|in-house-independent) ;;
  "") die "--reviewer is required: rules-conformance (in-house), or one of codex, gemini, in-house-independent (independent, tried in that order)" ;;
  *) die "--reviewer must be one of rules-conformance, codex, gemini, in-house-independent, got: $REVIEWER" ;;
esac
CONTEXT="deckard-verdict/$REVIEWER"

case "$VERDICT" in
  pass) STATE="success"; LABEL="PASS" ;;
  fail) STATE="failure"; LABEL="FAIL" ;;
  "") die "--verdict is required: pass or fail" ;;
  *) die "--verdict must be 'pass' or 'fail', got: $VERDICT" ;;
esac

[[ -n "$PACKET_SHA256" ]] || die "--packet-sha256 is required -- the source-slice.py bodySha256 this verdict was checked against"
[[ "$PACKET_SHA256" =~ ^[0-9a-fA-F]{64}$ ]] || die "--packet-sha256 must be 64 hex characters (source-slice.py's bodySha256), got: $PACKET_SHA256"
[[ -n "$PAGES" ]] || die "--pages is required -- the page range cited, e.g. 44-47"
[[ "$RERUN_GATE" -eq 0 || -n "$PR" ]] || die "--rerun-gate requires --pr"

DESCRIPTION="$LABEL bodySha256=${PACKET_SHA256,,} pages=$PAGES"
if [[ -n "$NOTES" ]]; then
  DESCRIPTION="$DESCRIPTION -- $NOTES"
fi
# The GitHub Statuses API truncates (or may reject) a description over 140 characters.
# Fail loudly here instead of silently posting a verdict whose notes were cut off mid
# sentence -- notes are the one field length here that is not fixed by this script.
if [[ "${#DESCRIPTION}" -gt 140 ]]; then
  die "verdict description is ${#DESCRIPTION} chars (>140); shorten --notes.
       Full text: $DESCRIPTION"
fi

if [[ -z "$REPO" ]]; then
  REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)" \
    || die "could not resolve owner/repo; pass --repo owner/repo"
fi

printf 'Recording %s verdict for %s at %s...\n' "$REVIEWER" "$SHA" "$CONTEXT" >&2

gh api --method POST "repos/$REPO/statuses/$SHA" \
  -f "state=$STATE" \
  -f "context=$CONTEXT" \
  -f "description=$DESCRIPTION" \
  >/dev/null

printf '%s verdict recorded: %s\n' "$CONTEXT" "$DESCRIPTION"

# The Actions run that produced the FIRST (necessarily failing, since no verdict existed
# yet) evaluation of rules-conformance-gate for this SHA does not re-run itself just
# because a status was posted afterward -- GitHub does not re-trigger a `pull_request`
# workflow on a plain commit-status event. `workflow_dispatch` against the PR's branch
# re-evaluates the gate for whatever commit is currently at the tip of that branch; when
# that tip is still this SHA (the usual case -- nothing was pushed since), the resulting
# check run lands on exactly the commit this verdict was just recorded for.
if [[ "$RERUN_GATE" -eq 1 ]]; then
  branch="$(gh pr view "$PR" --json headRefName --jq .headRefName)" \
    || die "could not resolve the branch for PR #$PR"
  gh workflow run rules-conformance-gate.yml --repo "$REPO" --ref "$branch" -f "pr=$PR" \
    || die "could not dispatch a re-run of rules-conformance-gate for PR #$PR"
  printf 'Requested a rules-conformance-gate re-run for PR #%s (%s).\n' "$PR" "$branch"
fi
