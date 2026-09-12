namespace DeckardII.Core.Replay;

/// <summary>
/// Identifies which ruleset -- and which revision of its mechanics -- was in force when a
/// sequence of decisions was recorded. Distinct from <see cref="SourceBaselineId"/>: the
/// source baseline identifies pinned authoritative source material, while this identifies
/// the implemented ruleset revision, which can change independently as mechanics are added,
/// corrected, or reinterpreted.
///
/// <c>default(RulesetVersion)</c> bypasses the constructor below entirely and yields a
/// <see langword="null"/> <see cref="Id"/> paired with a valid-looking <c>Version</c> of
/// <c>0</c> rather than throwing. Consumers must account for that default-value
/// behavior.
/// </summary>
public readonly record struct RulesetVersion
{
    /// <summary>
    /// Which ruleset this is. Kept distinct from <see cref="Version"/> so different
    /// rulesets or variants can be distinguished from revisions of the same ruleset.
    /// </summary>
    public string Id { get; }

    /// <summary>
    /// This ruleset's revision. Bumping it is routine engineering, expected whenever a
    /// change could alter how a recorded decision resolves, so replay compatibility can
    /// be evaluated explicitly.
    /// </summary>
    public int Version { get; }

    /// <exception cref="ArgumentException">
    /// <paramref name="id"/> is null, empty, or whitespace.
    /// </exception>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="version"/> is negative.</exception>
    public RulesetVersion(string id, int version)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(id);
        ArgumentOutOfRangeException.ThrowIfNegative(version);
        Id = id;
        Version = version;
    }
}
