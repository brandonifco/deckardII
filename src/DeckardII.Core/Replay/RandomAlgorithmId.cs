namespace DeckardII.Core.Replay;

/// <summary>
/// Names the pseudorandom algorithm a sequence of draws was produced by -- not its seed,
/// not its captured state, its algorithm. Two <see cref="Randomness.IRandomSource"/>
/// implementations that happen to produce identical output today are still a
/// replay-compatibility break the moment either one is replaced, because nothing about a
/// sequence of <c>uint</c> values proves which generator produced it. The identifier is
/// explicit so replay compatibility is checkable as data rather than reviewer knowledge.
///
/// <c>default(RandomAlgorithmId)</c> bypasses the constructor below entirely and yields a
/// <see langword="null"/> <see cref="Name"/> rather than throwing. Consumers must account
/// for that default-value behavior.
/// </summary>
public readonly record struct RandomAlgorithmId
{
    /// <summary>
    /// The algorithm's canonical name, exactly as the PCG reference implementation names
    /// its variant.
    /// </summary>
    public string Name { get; }

    /// <exception cref="ArgumentException">
    /// <paramref name="name"/> is null, empty, or whitespace.
    /// </exception>
    public RandomAlgorithmId(string name)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(name);
        Name = name;
    }

    /// <summary>
    /// The algorithm currently provided by the framework: PCG32, variant
    /// <c>pcg_setseq_64_xsh_rr_32</c>, backing <see cref="Randomness.Pcg32"/>.
    /// Replacing it or adding another algorithm changes replay compatibility, so the
    /// algorithm identity is represented explicitly and compared as data.
    /// </summary>
    public static readonly RandomAlgorithmId Pcg32SetSeq64XshRr32 = new("pcg_setseq_64_xsh_rr_32");
}
