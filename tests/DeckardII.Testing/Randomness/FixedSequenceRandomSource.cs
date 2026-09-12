using System.Collections.Generic;
using System.Linq;
using DeckardII.Core.Randomness;

namespace DeckardII.Testing.Randomness;

/// <summary>
/// A scripted <see cref="IRandomSource"/> that replays a fixed sequence of values in
/// order, for unit tests that need to control exactly what a mechanic draws. It lives in
/// the non-packable test-support project under <c>tests/</c>, never in production source,
/// so deterministic test control does not become part of the shipped runtime API.
/// </summary>
public sealed class FixedSequenceRandomSource : IRandomSource
{
    private readonly IReadOnlyList<uint> _values;
    private int _consumed;

    /// <summary>An empty sequence is allowed to construct; it fails on the first draw, like any other exhaustion.</summary>
    /// <exception cref="ArgumentNullException"><paramref name="values"/> is null.</exception>
    public FixedSequenceRandomSource(IEnumerable<uint> values)
    {
        ArgumentNullException.ThrowIfNull(values);
        _values = values.ToArray();
    }

    /// <summary>
    /// How many values have been drawn so far. Exposed because the number of draws a
    /// mechanic makes is part of its observable deterministic contract, so a test needs
    /// to be able to assert on it, not just on the values themselves.
    /// </summary>
    public int Consumed => _consumed;

    /// <exception cref="InvalidOperationException">
    /// The supplied sequence is already exhausted. Never wraps, repeats the last value,
    /// or returns zero -- a caller drawing more values than a test scripted is a
    /// programmer error, and any of those fallbacks would silently hide it instead.
    /// </exception>
    public uint NextUInt32()
    {
        if (_consumed >= _values.Count)
        {
            throw new InvalidOperationException(
                $"FixedSequenceRandomSource was given {_values.Count} value(s) and was asked for one more.");
        }

        return _values[_consumed++];
    }
}
