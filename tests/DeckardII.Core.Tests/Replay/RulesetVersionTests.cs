using DeckardII.Core.Replay;

namespace DeckardII.Core.Tests.Replay;

/// <summary>Tests ruleset version value semantics and validation.</summary>
public sealed class RulesetVersionTests
{
    [Fact]
    public void Same_id_and_version_compare_equal()
    {
        var a = new RulesetVersion("fixture-core-ruleset", 1);
        var b = new RulesetVersion("fixture-core-ruleset", 1);

        Assert.Equal(a, b);
        Assert.Equal(a.GetHashCode(), b.GetHashCode());
    }

    [Fact]
    public void Different_id_compares_unequal()
    {
        var a = new RulesetVersion("fixture-core-ruleset", 1);
        var b = new RulesetVersion("some-house-ruleset", 1);

        Assert.NotEqual(a, b);
    }

    [Fact]
    public void Different_version_compares_unequal()
    {
        var a = new RulesetVersion("fixture-core-ruleset", 1);
        var b = new RulesetVersion("fixture-core-ruleset", 2);

        Assert.NotEqual(a, b);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData(" ")]
    public void Null_empty_or_whitespace_id_throws(string? id)
    {
        // Null specifically throws ArgumentNullException (a subtype); ThrowsAny covers
        // the whole theory without over-specifying which exact subtype null gets.
        Assert.ThrowsAny<ArgumentException>(() => new RulesetVersion(id!, 1));
    }

    [Fact]
    public void Negative_version_throws()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new RulesetVersion("fixture-core-ruleset", -1));
    }

    [Fact]
    public void Default_bypasses_the_constructor_and_yields_a_null_id_with_zero_version()
    {
        // Pins the documented default-value gap: a struct's implicit
        // parameterless constructor bypasses this type's constructor entirely, yielding a
        // null Id even though the paired Version (0) alone happens to satisfy the
        // constructor's own non-negative check. Nothing consumes RulesetVersion yet, so
        // there is no second entry point to re-validate at.
        var value = default(RulesetVersion);

        Assert.Null(value.Id);
        Assert.Equal(0, value.Version);
    }
}
