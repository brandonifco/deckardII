namespace DeckardII.Core.Replay;

/// <summary>
/// The complete replay compatibility identity. The engine invariant is "same rules
/// version + same initial state + same random seed/state + same ordered decisions = same
/// outcomes and the same ordered event/roll history"; this type makes the rules/replay
/// portion of that identity explicitly comparable.
///
/// Two identities are equal only when every component matches.
/// <see cref="RandomAlgorithm"/>, <see cref="Ruleset"/>, <see cref="ReplaySchema"/>, and
/// <see cref="SourceBaseline"/> each change for a different, independent reason, so a
/// mismatch in any single one is a genuine incompatibility -- not something to average
/// away or ignore.
///
/// This type has no serialization, persistence, or replay-execution behaviour by design:
/// it exists only to represent and compare compatibility identity.
/// </summary>
public readonly record struct ReplayCompatibilityIdentity(
    RandomAlgorithmId RandomAlgorithm,
    RulesetVersion Ruleset,
    ReplaySchemaVersion ReplaySchema,
    SourceBaselineId SourceBaseline);
