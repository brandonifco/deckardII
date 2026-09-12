namespace DeckardII.Core.Randomness;

/// <summary>
/// PCG32 (<c>pcg_setseq_64_xsh_rr_32</c>), the framework's replay-stable randomness
/// primitive. Its algorithm and state are explicit rather than ambient.
///
/// <see cref="FromSeed"/> and <see cref="NextUInt32"/> are deliberately written as
/// line-for-line ports of the reference's <c>pcg32_srandom_r</c> and
/// <c>pcg32_random_r</c> (imneme/pcg-c-basic, pinned commit recorded in
/// tests/DeckardII.Core.Tests/Randomness/Pcg32ReferenceVectors.cs) so a reviewer can diff
/// this type against the C source directly rather than trust a paraphrase. That fixture
/// is what pins this port as correct -- its vectors came from running the reference
/// implementation, not from anyone's memory of the algorithm.
/// </summary>
public sealed class Pcg32 : IRandomSource
{
    // The reference's PCG_DEFAULT_MULTIPLIER_64.
    private const ulong Multiplier = 6364136223846793005UL;

    private ulong _state;
    private readonly ulong _increment;

    private Pcg32(ulong state, ulong increment)
    {
        _state = state;
        _increment = increment;
    }

    /// <summary>
    /// Mirrors <c>pcg32_srandom_r(rng, initstate, initseq)</c> exactly, including its two
    /// discarded draws. Both matter: skipping either one changes every value the
    /// generator goes on to produce, so this is not a place to "simplify" the port.
    /// </summary>
    public static Pcg32 FromSeed(ulong seed, ulong stream)
    {
        var rng = new Pcg32(0UL, (stream << 1) | 1UL);
        rng.NextUInt32();
        unchecked
        {
            rng._state += seed;
        }

        rng.NextUInt32();
        return rng;
    }

    /// <summary>Resumes from a captured <see cref="Pcg32State"/> instead of seeding.</summary>
    /// <exception cref="ArgumentException">
    /// <paramref name="state"/>'s <see cref="Pcg32State.Increment"/> is even. This repeats
    /// the check <see cref="Pcg32State"/>'s own constructor already makes, because that
    /// constructor is not the only way to produce a <see cref="Pcg32State"/> value: a
    /// struct always has an implicit parameterless constructor that zero-initializes its
    /// fields -- reachable as <c>default(Pcg32State)</c>, <c>new Pcg32State()</c>, or a
    /// deserializer that sets fields directly -- and it cannot be suppressed or
    /// overridden. Validating only at construction would let an even increment reach here
    /// unchecked, forfeiting PCG's full-period guarantee: the underlying LCG then visits
    /// fewer than the full 2^64 states, and how much of the period is lost depends on how
    /// many factors of two the increment carries -- an increment of 2 merely halves it,
    /// and it degrades further from there. The implicit constructor's specific zeroed
    /// state is the reason the check is repeated here rather than trusted: with state and
    /// increment both zero, 0 is a fixed point of the LCG step, so that particular bypass
    /// would silently return the same value forever rather than merely losing period.
    /// </exception>
    public static Pcg32 FromState(Pcg32State state)
    {
        if ((state.Increment & 1UL) == 0UL)
        {
            throw new ArgumentException(
                "PCG increment must be odd; an even increment forfeits its full-period "
                + "guarantee.",
                nameof(state));
        }

        return new(state.State, state.Increment);
    }

    /// <summary>Captures the current internal state so a replay can resume from exactly this point.</summary>
    public Pcg32State GetState() => new(_state, _increment);

    /// <summary>Faithful port of the reference's <c>pcg32_random_r</c>; see the type doc.</summary>
    public uint NextUInt32()
    {
        ulong oldstate = _state;
        unchecked
        {
            _state = oldstate * Multiplier + _increment;
        }

        uint xorshifted = (uint)(((oldstate >> 18) ^ oldstate) >> 27);
        int rot = (int)(oldstate >> 59);
        return (xorshifted >> rot) | (xorshifted << ((-rot) & 31));
    }
}
