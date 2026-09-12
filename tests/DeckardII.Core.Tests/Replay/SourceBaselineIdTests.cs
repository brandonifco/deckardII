using DeckardII.Core.Replay;

namespace DeckardII.Core.Tests.Replay;

/// <summary>
/// The test that matters here is
/// <see cref="Same_source_id_but_different_hash_compares_unequal"/>: a source-baseline
/// re-pin can change the pinned <c>sha256</c> while leaving <c>sourceId</c> unchanged, so
/// an identifier that ignored <c>sha256</c> would compare equal across exactly the event
/// it exists to detect.
/// </summary>
public sealed class SourceBaselineIdTests
{
    private const string BaselineHash = "7db88be98dbc2a4d1f777b7ff10034af3ea35e20bb3899b2aad95b7232bbac41";
    private const string RepinnedHash = "0000000000000000000000000000000000000000000000000000000000000000";

    [Fact]
    public void Same_source_id_and_hash_compare_equal()
    {
        var a = new SourceBaselineId("fixture-core", BaselineHash);
        var b = new SourceBaselineId("fixture-core", BaselineHash);

        Assert.Equal(a, b);
        Assert.Equal(a.GetHashCode(), b.GetHashCode());
    }

    [Fact]
    public void Different_source_id_compares_unequal()
    {
        var a = new SourceBaselineId("fixture-core", BaselineHash);
        var b = new SourceBaselineId("fixture-alternate-source", BaselineHash);

        Assert.NotEqual(a, b);
    }

    [Fact]
    public void Same_source_id_but_different_hash_compares_unequal()
    {
        // The acceptance test for this type: a re-pin (a different printing, a corrected
        // hash) edits sha256 in place and never touches sourceId. Before this fix,
        // SourceBaselineId carried only sourceId, so this exact pair compared equal --
        // the identity would have silently missed the one event it exists to catch.
        var beforeRepin = new SourceBaselineId("fixture-core", BaselineHash);
        var afterRepin = new SourceBaselineId("fixture-core", RepinnedHash);

        Assert.NotEqual(beforeRepin, afterRepin);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("  ")]
    public void Null_empty_or_whitespace_source_id_throws(string? sourceId)
    {
        // Null specifically throws ArgumentNullException (a subtype); ThrowsAny covers
        // the whole theory without over-specifying which exact subtype null gets.
        Assert.ThrowsAny<ArgumentException>(() => new SourceBaselineId(sourceId!, BaselineHash));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("  ")]
    public void Null_empty_or_whitespace_hash_throws(string? sha256)
    {
        Assert.ThrowsAny<ArgumentException>(() => new SourceBaselineId("fixture-core", sha256!));
    }

    [Fact]
    public void Default_bypasses_the_constructor_and_yields_null_fields()
    {
        // Pins the documented default-value gap: a struct's implicit
        // parameterless constructor -- default(T), new T(), or a deserializer setting
        // fields directly -- bypasses SourceBaselineId's constructor entirely. Nothing
        // consumes this type yet, so there is no second entry point to re-validate at
        // (contrast Pcg32.FromState, which re-checks Pcg32State for the same reason a
        // real consumer exists for it). This test exists so the gap is a recorded fact,
        // not a surprise for whoever writes the first consumer.
        var value = default(SourceBaselineId);

        Assert.Null(value.SourceId);
        Assert.Null(value.Sha256);
    }
}
