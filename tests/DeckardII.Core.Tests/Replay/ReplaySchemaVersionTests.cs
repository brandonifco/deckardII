using DeckardII.Core.Replay;

namespace DeckardII.Core.Tests.Replay;

/// <summary>Tests replay-schema version value semantics and validation.</summary>
public sealed class ReplaySchemaVersionTests
{
    [Fact]
    public void Same_version_compares_equal()
    {
        var a = new ReplaySchemaVersion(1);
        var b = new ReplaySchemaVersion(1);

        Assert.Equal(a, b);
        Assert.Equal(a.GetHashCode(), b.GetHashCode());
    }

    [Fact]
    public void Different_version_compares_unequal()
    {
        var a = new ReplaySchemaVersion(1);
        var b = new ReplaySchemaVersion(2);

        Assert.NotEqual(a, b);
    }

    [Fact]
    public void Negative_version_throws()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new ReplaySchemaVersion(-1));
    }

    [Fact]
    public void Default_is_not_a_validation_gap_unlike_its_siblings()
    {
        // Unlike RandomAlgorithmId, RulesetVersion, and SourceBaselineId -- whose
        // default(T) bypasses validation and yields a null string field -- this type's
        // only field is an int, and 0 passes the constructor's own non-negative check.
        // So default(ReplaySchemaVersion) is not a hidden bypass; it is indistinguishable
        // from an explicitly constructed schema version 0. Pinned here so that fact is
        // recorded rather than assumed.
        Assert.Equal(new ReplaySchemaVersion(0), default(ReplaySchemaVersion));
    }
}
