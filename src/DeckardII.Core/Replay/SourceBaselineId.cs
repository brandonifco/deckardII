namespace DeckardII.Core.Replay;

/// <summary>
/// Carries an authoritative source identifier and content hash into a replay identity.
/// Both values are supplied by the caller; Core performs no filesystem access and has no
/// knowledge of source-manifest storage or shape.
///
/// Both fields are required. A source may retain the same logical identifier while its
/// pinned content hash changes, and replay compatibility must distinguish those baselines.
/// An identifier containing only <c>sourceId</c> would miss exactly that change.
/// </summary>
public readonly record struct SourceBaselineId
{
    /// <summary>The authoritative source's stable logical identifier.</summary>
    public string SourceId { get; }

    /// <summary>
    /// The manifest's <c>sha256</c> for this source, verbatim. This is the field a
    /// source-baseline re-pin actually changes -- see the type doc.
    /// </summary>
    public string Sha256 { get; }

    /// <exception cref="ArgumentException">
    /// <paramref name="sourceId"/> or <paramref name="sha256"/> is null, empty, or
    /// whitespace.
    /// </exception>
    public SourceBaselineId(string sourceId, string sha256)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(sourceId);
        ArgumentException.ThrowIfNullOrWhiteSpace(sha256);
        SourceId = sourceId;
        Sha256 = sha256;
    }
}
