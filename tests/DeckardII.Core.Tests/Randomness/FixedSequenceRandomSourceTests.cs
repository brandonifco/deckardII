using DeckardII.Testing.Randomness;

namespace DeckardII.Core.Tests.Randomness;

/// <summary>Behavioural contract of the fixed-sequence test double higher layers inject in place of a real PRNG.</summary>
public sealed class FixedSequenceRandomSourceTests
{
    [Fact]
    public void NextUInt32_returns_the_supplied_values_in_order()
    {
        var source = new FixedSequenceRandomSource([10u, 20u, 30u]);

        Assert.Equal(10u, source.NextUInt32());
        Assert.Equal(20u, source.NextUInt32());
        Assert.Equal(30u, source.NextUInt32());
    }

    [Fact]
    public void NextUInt32_throws_after_the_last_value_and_names_the_supplied_count()
    {
        var source = new FixedSequenceRandomSource([1u, 2u]);
        source.NextUInt32();
        source.NextUInt32();

        var ex = Assert.Throws<InvalidOperationException>(() => source.NextUInt32());
        Assert.Contains("given 2 value(s)", ex.Message);
    }

    [Fact]
    public void An_empty_source_throws_on_the_very_first_draw()
    {
        var source = new FixedSequenceRandomSource([]);

        var ex = Assert.Throws<InvalidOperationException>(() => source.NextUInt32());
        Assert.Contains("given 0 value(s)", ex.Message);
    }

    [Fact]
    public void Consumed_counts_exactly_the_draws_made_so_far()
    {
        var source = new FixedSequenceRandomSource([1u, 2u, 3u]);
        Assert.Equal(0, source.Consumed);

        source.NextUInt32();
        Assert.Equal(1, source.Consumed);

        source.NextUInt32();
        Assert.Equal(2, source.Consumed);
    }

    [Fact]
    public void Null_sequence_throws_ArgumentNullException()
    {
        Assert.Throws<ArgumentNullException>(() => new FixedSequenceRandomSource(null!));
    }
}
