namespace DeckardII.Core.Randomness;

/// <summary>
/// The complete internal state of a <see cref="Pcg32"/> instance, captured so a replay
/// can resume mid-sequence rather than only from a fresh seed. Plain public members
/// instead of a serialization-library type, so any serializer a future save format
/// chooses can round-trip this without Core taking a dependency on a serialization
/// library or persistence mechanism.
/// </summary>
public readonly record struct Pcg32State
{
    /// <summary>The generator's raw 64-bit LCG state.</summary>
    public ulong State { get; }

    /// <summary>
    /// The stream increment. PCG's period guarantee requires this to be odd; an even
    /// increment does not throw inside the generator itself, it silently forfeits the
    /// full-period guarantee -- how much period is lost depends on how many factors of
    /// two the increment carries, from merely halved upward. This constructor is one of
    /// two gates against that, not the only one: a struct's implicit parameterless
    /// constructor (<c>default(Pcg32State)</c>, <c>new Pcg32State()</c>) bypasses it
    /// entirely and cannot be suppressed, so <see cref="Pcg32.FromState"/> repeats the
    /// same check for whatever reaches it that way.
    /// </summary>
    public ulong Increment { get; }

    /// <exception cref="ArgumentException"><paramref name="increment"/> is even.</exception>
    public Pcg32State(ulong state, ulong increment)
    {
        if ((increment & 1UL) == 0UL)
        {
            throw new ArgumentException(
                "PCG increment must be odd; an even increment forfeits its full-period guarantee.",
                nameof(increment));
        }

        State = state;
        Increment = increment;
    }
}
