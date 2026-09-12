using DeckardII.Core.Replay;

namespace DeckardII.Core.Tests.Replay;

/// <summary>
/// The framework currently provides one <see cref="RandomAlgorithmId"/> value:
/// <see cref="RandomAlgorithmId.Pcg32SetSeq64XshRr32"/>. The type itself is an ordinary
/// comparable value, not a closed enum, so equality has to be proven rather than assumed.
/// </summary>
public sealed class RandomAlgorithmIdTests
{
    [Fact]
    public void Pinned_value_names_the_PCG32_variant()
    {
        Assert.Equal("pcg_setseq_64_xsh_rr_32", RandomAlgorithmId.Pcg32SetSeq64XshRr32.Name);
    }

    [Fact]
    public void Same_name_compares_equal()
    {
        var a = new RandomAlgorithmId("pcg_setseq_64_xsh_rr_32");
        var b = RandomAlgorithmId.Pcg32SetSeq64XshRr32;

        Assert.Equal(a, b);
        Assert.True(a == b);
        Assert.Equal(a.GetHashCode(), b.GetHashCode());
    }

    [Fact]
    public void Different_name_compares_unequal()
    {
        var pcg32 = RandomAlgorithmId.Pcg32SetSeq64XshRr32;
        var other = new RandomAlgorithmId("some-other-generator");

        Assert.NotEqual(pcg32, other);
        Assert.True(pcg32 != other);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Null_empty_or_whitespace_name_throws(string? name)
    {
        // ArgumentException.ThrowIfNullOrWhiteSpace throws ArgumentNullException for null
        // specifically and ArgumentException for empty/whitespace -- both are
        // ArgumentException, so ThrowsAny covers the whole theory without over-specifying
        // which exact subtype a null argument gets.
        Assert.ThrowsAny<ArgumentException>(() => new RandomAlgorithmId(name!));
    }

    [Fact]
    public void Default_bypasses_the_constructor_and_yields_a_null_name()
    {
        // Pins the documented default-value gap: a struct's implicit
        // parameterless constructor bypasses this type's constructor entirely. Nothing
        // consumes RandomAlgorithmId yet, so there is no second entry point to
        // re-validate at (contrast Pcg32.FromState, which re-checks Pcg32State because a
        // real consumer exists for it).
        var value = default(RandomAlgorithmId);

        Assert.Null(value.Name);
        Assert.NotEqual(RandomAlgorithmId.Pcg32SetSeq64XshRr32, value);
    }
}
