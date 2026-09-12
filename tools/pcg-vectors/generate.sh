#!/usr/bin/env bash
# generate.sh -- regenerate the pinned PCG32 reference vectors.
#
#   tools/pcg-vectors/generate.sh           rewrite the C# fixture
#   tools/pcg-vectors/generate.sh --check   fail if the committed fixture has drifted
#
# The vectors that pin the framework's replay contract must come from the reference
# implementation, not from anybody's recollection of it. So this script fetches
# imneme/pcg-c-basic at a pinned commit, verifies the two files it uses by SHA-256,
# compiles them together with gen_vectors.c, and transcribes the program's output
# mechanically into C#.
#
# The reference is fetched, never vendored: it is third-party Apache-2.0 code, and
# committing it would make this repository a redistributor for no benefit. What this repository
# keeps is the derived facts and the exact recipe for re-deriving them.
#
# Not part of ./scripts/validate.sh: this needs network access and a C compiler, and the
# canonical gate must stay hermetic. Run it when the vectors are questioned, not on
# every build.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$REPO_ROOT/tools/pcg-vectors"

UPSTREAM_REPO="https://github.com/imneme/pcg-c-basic"
UPSTREAM_COMMIT="bc39cd76ac3d541e618606bcc6e1e5ba5e5e6aa3"
RAW_BASE="https://raw.githubusercontent.com/imneme/pcg-c-basic/$UPSTREAM_COMMIT"

# Pinning the commit alone is not enough: a raw.githubusercontent response is whatever
# arrived over the wire. These are the hashes of the reference files this pin was
# established against.
SHA_PCG_BASIC_C="b6582a071a8a090a293621523c063d125a532d772a2a1eb7d60b3e695fe47746"
SHA_PCG_BASIC_H="cd823ddc225da9be520a54f13ef2c491506c353800cae04e8b673b2de58f2cc4"

FIXTURE="$REPO_ROOT/tests/DeckardII.Core.Tests/Randomness/Pcg32ReferenceVectors.cs"

if [[ -t 1 ]]; then BOLD=$'\033[1m'; RED=$'\033[31m'; GRN=$'\033[32m'; OFF=$'\033[0m'
else BOLD=""; RED=""; GRN=""; OFF=""; fi

die() { printf '%serror%s %s\n' "$RED" "$OFF" "$1" >&2; exit 1; }

MODE="${1:-write}"
case "$MODE" in
  write|--check) ;;
  -h|--help) sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "unknown argument: $MODE (expected --check or nothing)" ;;
esac

command -v cc >/dev/null 2>&1 || die "a C compiler (cc) is required"
command -v curl >/dev/null 2>&1 || die "curl is required"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fetch_verified() {
  local name="$1" expected="$2" actual
  curl -fsS --max-time 60 -o "$WORK/$name" "$RAW_BASE/$name" \
    || die "could not fetch $name from $RAW_BASE"
  actual="$(sha256sum "$WORK/$name" | cut -d' ' -f1)"
  [[ "$actual" == "$expected" ]] || die "$name sha256 mismatch
       expected $expected
       actual   $actual
       The pinned reference file is not what this repository pinned. Do not
       regenerate vectors from it."
  printf '%sok%s   %s  %s\n' "$GRN" "$OFF" "$name" "${actual:0:16}..."
}

printf '%sFetching reference implementation%s  %s@%s\n' \
  "$BOLD" "$OFF" "$UPSTREAM_REPO" "${UPSTREAM_COMMIT:0:12}"
fetch_verified pcg_basic.c "$SHA_PCG_BASIC_C"
fetch_verified pcg_basic.h "$SHA_PCG_BASIC_H"

printf '\n%sCompiling%s\n' "$BOLD" "$OFF"
cc -O2 -std=c99 -Wall -Wextra -Werror -I"$WORK" \
   -o "$WORK/gen_vectors" "$HERE/gen_vectors.c" "$WORK/pcg_basic.c"
printf '%sok%s   gen_vectors\n' "$GRN" "$OFF"

TARGET="$FIXTURE"
if [[ "$MODE" == "--check" ]]; then
  TARGET="$WORK/Pcg32ReferenceVectors.cs"
fi

mkdir -p "$(dirname "$TARGET")"
"$WORK/gen_vectors" | python3 "$HERE/emit_csharp.py" \
  --commit "$UPSTREAM_COMMIT" --repository "$UPSTREAM_REPO" --out "$TARGET"

printf '\n'
if [[ "$MODE" == "--check" ]]; then
  if diff -u "$FIXTURE" "$TARGET"; then
    printf '%s%spcg-vectors: committed fixture matches the reference%s\n' "$BOLD" "$GRN" "$OFF"
  else
    die "the committed fixture does not match a fresh run against the reference.
       Replacing it changes the replay contract; treat that as an explicit compatibility change."
  fi
else
  printf '%s%swrote%s %s\n' "$BOLD" "$GRN" "$OFF" "${FIXTURE#"$REPO_ROOT"/}"
fi
