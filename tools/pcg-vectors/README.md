# PCG32 reference vectors

`tests/DeckardII.Core.Tests/Randomness/Pcg32ReferenceVectors.cs` is generated, not written.
Everything in this directory exists to make that file re-derivable by someone who does
not trust it.

## Why this is not a normal test fixture

The framework's replay contract is the PCG32 sequence itself. A wrong vector does not fail —
it passes, forever, pinning a bug as if it were the specification. the deterministic replay contract therefore
requires the vectors to come from the published reference implementation, obtained by
running it. Vectors nobody checked are worse than none, because they look like evidence.

## Regenerating

```bash
tools/pcg-vectors/generate.sh
```

Needs `cc`, `curl` and network access. The script:

1. fetches `pcg_basic.c` and `pcg_basic.h` from `imneme/pcg-c-basic` at the pinned
   commit recorded in `generate.sh`;
2. verifies both by SHA-256 against the hashes that pin was established with, and
   refuses to continue on a mismatch;
3. compiles them with `gen_vectors.c`, which only *calls* the reference;
4. transcribes the program's JSON output into C# with `emit_csharp.py`, which performs
   no arithmetic of its own.

```bash
tools/pcg-vectors/generate.sh --check
```

does the same but diffs against the committed fixture instead of overwriting it.

This is deliberately **not** wired into `./scripts/validate.sh`. The canonical gate must
stay hermetic and offline-capable; this needs the network. Run it when the vectors are
questioned, not on every build.

## The reference is fetched, never vendored

`pcg_basic.c` is third-party Apache-2.0 code. Committing it would make this repository a
redistributor of someone else's work for no benefit — the derived vectors and the exact
recipe are what the framework actually needs. Nothing under `tools/pcg-vectors/` is copied from
upstream.

## Independent cross-check

The `canonical-42-54` row uses the seed `(42, 54)` that upstream's own `pcg32-demo` and
`check-pcg32` use. Upstream publishes the expected output of those programs at
`test-high/expected/check-pcg32.out` and `test-low/expected/check-setseq-64-xsh-rr-32.out`
in `imneme/pcg-c`. Both record the same first six values:

```
0xa15c02b7 0x7b47f409 0xba1d3330 0x83d2f293 0xbfa4784b 0xcbed606e
```

So the leading row of the fixture is checkable against a file nobody in this repository
produced, without running anything at all.

## Changing the vectors

Don't, except to add rows. Changing an existing value changes what every stored seed in
the project means. is a replay-compatibility event and requires an explicit compatibility decision,
not a regenerated fixture in an unrelated change.
