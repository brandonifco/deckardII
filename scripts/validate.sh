#!/usr/bin/env bash
# validate.sh -- the single canonical gate for Deckard.
#
# Humans call it. Claude calls it. Subagents call it. CI calls it. There is exactly one
# definition of "this change is acceptable", and it lives here rather than being
# reimplemented in a workflow file where the two would silently drift apart.
#
#   ./scripts/validate.sh full      merge-equivalent gate (default)
#   ./scripts/validate.sh fast      Debug only; for the inner development loop
#   ./scripts/validate.sh sdk-pin   just prove the SDK pin resolves
#
# `full` must not require network access beyond ordinary NuGet restore, and must not
# require the authoritative rulebook: the tooling tests are hermetic on purpose so that
# CI can prove the source boundary without ever possessing a copy of the book.
#
# `full` also builds and tests once with CI=true set (see "CI parity" below), the same
# env var .github/workflows/build-and-test.yml sets, because CI builds under a
# different MSBuild configuration than every local shell and agent worktree
# (ContinuousIntegrationBuild, Directory.Build.props) and a test sensitive to that can
# otherwise pass here and fail on every CI run (#48).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Prefer a repo-local or primary-checkout .dotnet/ over PATH's dotnet when one exists
# (scripts/bootstrap-dotnet.sh installs it); otherwise change nothing, which is the path
# CI takes. Sourced before the SDK pin check so that check sees whatever this resolves.
# shellcheck source=lib/dotnet-env.sh
source "$REPO_ROOT/scripts/lib/dotnet-env.sh"

MODE="${1:-full}"
export DOTNET_NOLOGO=1
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1

SOLUTION="Deckard.slnx"
FAILED=0
STEP=0

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YEL=$'\033[33m'; OFF=$'\033[0m'
else
  BOLD=""; RED=""; GREEN=""; YEL=""; OFF=""
fi

step() {
  STEP=$((STEP + 1))
  printf '\n%s==> [%d] %s%s\n' "$BOLD" "$STEP" "$1" "$OFF"
}

fail() {
  printf '%sFAIL%s %s\n' "$RED" "$OFF" "$1"
  FAILED=1
}

run() {
  local label="$1"; shift
  if "$@"; then
    printf '%sok%s   %s\n' "$GREEN" "$OFF" "$label"
    return 0
  fi
  fail "$label"
  return 1
}

# Run a step only if the step it depends on succeeded. Without this, a failed build is
# followed by `dotnet test --no-build` against STALE binaries, which happily prints
# "ok test Debug". The overall verdict is still FAIL, but the evidence a developer
# pastes into a PR says the tests passed. In a project that requires pasted evidence,
# misleading output is its own defect.
skipped() {
  printf '%sskip%s %s (depends on a step that failed)\n' "$YEL" "$OFF" "$1"
}

# --------------------------------------------------------------------- sdk pin
verify_sdk_pin() {
  step "SDK pin"
  local pinned roll actual status
  pinned="$(python3 -c 'import json;print(json.load(open("global.json"))["sdk"]["version"])')"
  # rollForward is checked too, not just the version. Changing it to latestMajor would
  # silently reintroduce exactly the "close enough SDK" this pin exists to forbid, and
  # a version-string comparison alone would not notice.
  roll="$(python3 -c 'import json;print(json.load(open("global.json"))["sdk"].get("rollForward",""))')"

  # A bare `var=$(failing-command)` under `set -e` terminates the shell on the spot --
  # the exact trap the tooling-tests step below documents and guards against with
  # `|| tooling_status=$?`. `dotnet --version` exits non-zero for precisely the case
  # this check exists to catch (no installed SDK satisfies global.json), so capturing
  # it bare would kill the script before the comparison and the guidance beneath it
  # ever ran. This is the second place that trap has bitten; look here before a third.
  # Its stdout is also not trustworthy on failure -- the muxer writes the INSTALLED SDK
  # LIST there and the real error to stderr -- so it is discarded rather than reported
  # as though it were a version.
  status=0
  actual="$(dotnet --version 2>/dev/null)" || status=$?

  if [[ "$roll" != "disable" ]]; then
    fail "SDK pin: global.json rollForward is '$roll', expected 'disable'"
    echo "     ADR 0001 pins an exact SDK patch: a feature-band roll-forward is still a" >&2
    echo "     different compiler, and reproducibility is this project's central claim." >&2
    return 1
  fi
  if [[ "$status" -ne 0 ]]; then
    fail "SDK pin: global.json requires $pinned but the resolved dotnet cannot satisfy it"
    echo "     ./scripts/bootstrap-dotnet.sh, or change the pin deliberately in its own PR." >&2
    return 1
  fi
  if [[ "$pinned" != "$actual" ]]; then
    fail "SDK pin: global.json requires $pinned but 'dotnet --version' reports $actual"
    echo "     ./scripts/bootstrap-dotnet.sh, or change the pin deliberately in its own PR." >&2
    return 1
  fi
  printf '%sok%s   SDK %s (rollForward=%s)\n' "$GREEN" "$OFF" "$actual" "$roll"
}

if [[ "$MODE" == "sdk-pin" ]]; then
  verify_sdk_pin
  exit $FAILED
fi

verify_sdk_pin || exit 1

# ------------------------------------------------------------- prerequisites
# The source tooling tests skip themselves when pdftotext is absent. A silent skip of
# the source-boundary suite -- including the test asserting that source-slice.py has no
# --file argument -- is precisely the failure this project exists to prevent: a gate
# that reports PASS while proving less than it claims.
#
# This assertion previously lived only in the CI workflow, which made CI a second,
# stricter definition of "acceptable" than the canonical gate. There is one gate.
step "Prerequisites"
if command -v pdftotext >/dev/null 2>&1; then
  printf '%sok%s   pdftotext %s\n' "$GREEN" "$OFF" "$(pdftotext -v 2>&1 | head -1 | awk '{print $3}')"
else
  fail "pdftotext not found -- the source-boundary tests would silently skip"
  echo "     Install it:  sudo apt install poppler-utils" >&2
  exit 1
fi

# ------------------------------------------------------------------- restore
# Locked mode (RestorePackagesWithLockFile in Directory.Build.props) restores exactly
# the versions committed in each project's packages.lock.json and fails the moment a
# fresh resolution would pick something else -- an added, removed, or upgraded package,
# or a corrupted/edited lock file -- instead of a transitive version drifting in quietly
# (#43). See docs/architecture.md, "Dependency resolution and build reconstruction".
step "Restore"
if dotnet restore "$SOLUTION" --locked-mode; then
  printf '%sok%s   dotnet restore --locked-mode\n' "$GREEN" "$OFF"
else
  fail "dotnet restore --locked-mode"
  echo "     The committed packages.lock.json files don't match what restore resolves" >&2
  echo "     today. If you deliberately added, removed, or upgraded a package, that is" >&2
  echo "     expected -- regenerate the lock files and commit them:" >&2
  echo "         dotnet restore $SOLUTION --force-evaluate" >&2
  echo "     then re-run this script. If you did not touch a dependency, something else" >&2
  echo "     changed resolution (a moved or delisted package version, a feed change) --" >&2
  echo "     investigate before regenerating over it." >&2
fi

# -------------------------------------------------------------------- format
step "Format"
run "dotnet format --verify-no-changes" \
    dotnet format "$SOLUTION" --verify-no-changes --no-restore || true

# ------------------------------------------------------- build + test (Debug)
step "Build + test (Debug)"
if run "build Debug (0 warnings)" dotnet build "$SOLUTION" -c Debug --no-restore -warnaserror; then
  run "test Debug" dotnet test "$SOLUTION" -c Debug --no-build --nologo || true
else
  skipped "test Debug"
fi

if [[ "$MODE" != "fast" ]]; then
  # ----------------------------------------------------- build + test (Release)
  # Release is not ceremonial here: different optimisation settings have historically
  # been where "works on my machine" determinism bugs surface.
  step "Build + test (Release)"
  if run "build Release (0 warnings)" dotnet build "$SOLUTION" -c Release --no-restore -warnaserror; then
    run "test Release" dotnet test "$SOLUTION" -c Release --no-build --nologo || true
  else
    skipped "test Release"
  fi

  # --------------------------------------------------- build + test (CI parity)
  # CI sets CI=true (.github/workflows/build-and-test.yml), which flips
  # ContinuousIntegrationBuild on via Directory.Build.props. That property enables
  # deterministic source paths, rewriting compile-time source paths to `/_/...`, which
  # changes the value of anything derived from them: [CallerFilePath], StackTrace file
  # paths. Neither pass above exercises that -- local shells and every agent worktree
  # run with CI unset -- so a test that depends on it can pass here and fail on every
  # CI run (#48). Reproduce CI's own env var, not an equivalent MSBuild property, so
  # this pass is exactly what CI does rather than something merely similar to it.
  # Debug, not Release: this pass exists to exercise the CI-conditional property, not
  # to re-run the optimisation-level check Release above already covers.
  step "Build + test (CI parity, CI=true)"
  if run "build Debug w/ CI=true (0 warnings)" env CI=true dotnet build "$SOLUTION" -c Debug --no-restore -warnaserror; then
    run "test Debug w/ CI=true" env CI=true dotnet test "$SOLUTION" -c Debug --no-build --nologo || true
  else
    skipped "test Debug w/ CI=true"
  fi
fi

# ------------------------------------------------------------- repo invariants
step "Repository invariants"
run "repo-checks" tools/repo-checks.py || true

# ------------------------------------------------------------- tooling tests
step "Tooling tests"
# `unittest` exits 0 when tests SKIP, so a skipped suite is indistinguishable from a
# passing one by exit code alone. Deckard's tooling tests are the only proof the source
# boundary works, so a skip here is a failure, not a note.
# `|| tooling_status=$?` is load-bearing: under `set -e`, a bare
# `var=$(failing-command)` terminates the shell immediately, which would skip the FAIL
# line, the test output, and the final verdict banner -- in a gate whose whole job is
# producing evidence someone can paste into a PR.
tooling_status=0
tooling_output="$(python3 -m unittest discover -s tools/tests 2>&1)" || tooling_status=$?
if [[ "$tooling_status" -ne 0 ]]; then
  printf '%s\n' "$tooling_output" | tail -20
  fail "python tooling tests"
elif printf '%s' "$tooling_output" | grep -q "skipped="; then
  printf '%s\n' "$tooling_output" | tail -3
  fail "python tooling tests skipped some cases -- the gate must prove all of them"
else
  printf '%sok%s   %s\n' "$GREEN" "$OFF" "$(printf '%s' "$tooling_output" | grep -E '^Ran ' || echo 'python tooling tests')"
fi

# ------------------------------------------------------------ whitespace
step "Whitespace"
run "git diff --check" git diff --check || true
run "git diff --cached --check" git diff --cached --check || true

# ------------------------------------------------------------------- verdict
echo
if [[ "$FAILED" -eq 0 ]]; then
  printf '%s%svalidate.sh %s: PASS%s\n' "$BOLD" "$GREEN" "$MODE" "$OFF"
else
  printf '%s%svalidate.sh %s: FAIL%s\n' "$BOLD" "$RED" "$MODE" "$OFF"
fi
exit "$FAILED"
