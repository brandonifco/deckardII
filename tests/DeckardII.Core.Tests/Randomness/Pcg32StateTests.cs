using DeckardII.Core.Randomness;

namespace DeckardII.Core.Tests.Randomness;

/// <summary>
/// <see cref="Pcg32State"/>'s constructor rejects a corrupt (state, increment) pair --
/// most likely an even increment arriving from a deserialized replay. It is not the only
/// gate against one, though: a struct's implicit parameterless constructor bypasses it
/// entirely, which is why <see cref="Pcg32.FromState"/> repeats the check. These tests
/// cover this constructor only; see Pcg32Tests for the FromState half.
/// </summary>
public sealed class Pcg32StateTests
{
    [Fact]
    public void Even_increment_throws_ArgumentException()
    {
        Assert.Throws<ArgumentException>(() => new Pcg32State(0UL, 2UL));
    }

    [Theory]
    [InlineData(1UL)]
    [InlineData(3UL)]
    [InlineData(ulong.MaxValue)]
    public void Odd_increment_constructs_successfully(ulong increment)
    {
        var state = new Pcg32State(0UL, increment);
        Assert.Equal(increment, state.Increment);
    }
}
