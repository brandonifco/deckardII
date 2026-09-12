namespace DeckardII.Core.Replay;

/// <summary>
/// Versions the shape of a recorded replay -- its field layout, ordering, and contents --
/// independently of <see cref="RulesetVersion"/>, because the two change for unrelated
/// reasons: a schema revision (e.g. adding a diagnostic field to a recorded replay) can
/// happen with no mechanics change, and a mechanics change can happen with no schema
/// revision. Nothing in Core reads or writes a replay in this shape; serialization
/// remains outside this value type's responsibility. This exists so replay schema shape can
/// be named and compared independently.
///
/// Unlike its siblings, <c>default(ReplaySchemaVersion)</c> is not a validation gap: its
/// only field is an <see langword="int"/>, <c>0</c> passes the constructor's own check,
/// so <c>default(ReplaySchemaVersion) == new ReplaySchemaVersion(0)</c>.
/// </summary>
public readonly record struct ReplaySchemaVersion
{
    /// <summary>The recorded-replay schema's revision number.</summary>
    public int Version { get; }

    /// <exception cref="ArgumentOutOfRangeException"><paramref name="version"/> is negative.</exception>
    public ReplaySchemaVersion(int version)
    {
        ArgumentOutOfRangeException.ThrowIfNegative(version);
        Version = version;
    }
}
