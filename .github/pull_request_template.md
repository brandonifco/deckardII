<!-- Target length: roughly 500-1,000 words for ordinary work. This body answers four
     questions -- what changed, why, what was verified, what remains -- it is not an
     engineering report.

     Durable design rationale a future reader would need → an ADR (docs/decisions/).
     Acceptance criteria and scope → the Issue, where they already are.
     Detailed test enumeration and validation output → a generated artifact or the CI
     run, not pasted prose here.

     This is the single authority for the target length; docs/agent-team.md ("PR body
     length") points here rather than restating the number, so the two cannot drift
     apart. Nothing mechanical enforces the count -- see Issue #44 for why a hard
     word-count check in pr-policy was deliberately rejected. -->

## Linked Issue

Closes #

## Exact behavioral claim

<!-- What is TRUE NOW that was not true before? One or two sentences.
     Not a list of files changed. -->

## Scope

<!-- What changed?
     What nearby behaviour deliberately did NOT change? The second half tells a
     reviewer where to stop looking. -->

## Rules conformance

<!-- RULES WORK: the exact source locator(s) and what you actually verified.
       SR6 Core / <section> / printed p. X / PDF p. Y
     "Verified all 11 rows of the printed table" is checkable.
     "Matches the book" is not.

     NON-RULES WORK: write "N/A" and one line saying why.

     This section is prose -- pr-policy checks it is filled in and cites a page, not
     that a review actually happened. If this PR touches src/Deckard.Rules/,
     src/Deckard.Data/, their test projects, or .github/source-manifest.json, the
     rules-conformance-gate required check also demands a RECORDED verdict for this
     exact head commit (tools/record-verdict.sh; docs/agent-team.md, "Recording a
     verdict") before it can merge -- describing the review here does not satisfy it. -->

## Tests and evidence

<!-- Exact commands and their results. "Tests pass" is not evidence.
     Paste a fenced block containing what you ran and what it printed, e.g.

         $ ./scripts/validate.sh full
         validate.sh full: PASS
-->

## Determinism impact

<!-- Does this alter random consumption, ordering, serialisation, replay, or state
     transitions? Consuming one extra random value shifts every subsequent result.
     If nothing changes, say so explicitly. -->

## Decisions and tradeoffs

<!-- Material judgement made during implementation, if any.
     A genuine rules ambiguity does not belong here — it belongs in an ADR. -->

## Known limitations

<!-- What remains unsupported or unresolved? This is information, not an admission.
     An engine honest about its gaps is the whole point. -->

## Agent provenance

<!-- Which agent/model implemented this, and which independently verified it? -->

- Implemented by:
- Verified by:

## Unrelated changes

<!-- Must be none. -->

None.
