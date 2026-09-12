namespace DeckardII.Core.Randomness;

/// <summary>
/// The single deterministic randomness primitive consumed by rules mechanics.
/// Deliberately one member: distributions, bounded sampling, and domain-specific dice
/// mechanics belong in separately testable layers above this primitive rather than being
/// folded into the raw random source.
/// </summary>
public interface IRandomSource
{
    /// <summary>
    /// Draws the next raw 32-bit value. No bound, no distribution, no rejection
    /// sampling -- those decisions belong to whatever layer consumes this value, because
    /// a mixed-in bias-correction rule here would be untestable in isolation from the
    /// generator itself.
    ///
    /// <para>
    /// <b>Contract an implementor owes:</b> each call is expected to produce a value drawn
    /// uniformly from the full range of <c>uint</c> -- every one of the 2^32 possible
    /// values equally likely -- and successive calls are expected to be independent of one
    /// another. A consumer may therefore assume that a value below any given bound
    /// eventually appears; that is precisely what lets rejection sampling terminate rather than loop forever
    /// on a source that only ever yields out-of-range values.
    /// </para>
    ///
    /// <para>
    /// An implementor that cannot honour this -- a scripted test double, or some other
    /// deliberately degenerate source -- must not fake it: it must not wrap its sequence,
    /// repeat a prior value, or invent one just to keep a caller's loop alive. It should
    /// fail loudly instead, so a caller that relies on this contract fails visibly rather
    /// than spinning forever or silently receiving a biased result. <c>FixedSequenceRandomSource</c>
    /// (tests/DeckardII.Testing) is the existing example: it does not pretend to be uniform,
    /// and throws <see cref="InvalidOperationException"/> on exhaustion rather than looping.
    /// </para>
    /// </summary>
    uint NextUInt32();
}
