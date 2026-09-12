using System.Collections.Generic;
using System.Linq;
using System.Numerics;
using DeckardII.Core.Randomness;

namespace DeckardII.Core.Tests.Randomness;

/// <summary>
/// Pins <see cref="Pcg32"/> against <see cref="Pcg32ReferenceVectors"/> and asserts the
/// deterministic behavior those independently generated vectors establish. The vectors
/// are the evidence; these tests make that evidence load-bearing instead of decorative.
/// </summary>
public sealed class Pcg32Tests
{
    public static IEnumerable<object[]> SeededVectors =>
        Pcg32ReferenceVectors.Seeded.Select(v => new object[] { v });

    public static IEnumerable<object[]> RawStateVectors =>
        Pcg32ReferenceVectors.RawState.Select(v => new object[] { v });

    public static IEnumerable<object[]> ResumeVectors =>
        Pcg32ReferenceVectors.Resume.Select(v => new object[] { v });

    [Theory]
    [MemberData(nameof(SeededVectors))]
    public void FromSeed_matches_reference_post_seed_state_and_outputs(Pcg32ReferenceVectors.SeededVector vector)
    {
        var pcg = Pcg32.FromSeed(vector.InitState, vector.InitSequence);

        // Checked before any output is drawn, so a bug in the output function cannot
        // compensate for a bug in seeding and still pass this half of the assertion.
        Assert.Equal(new Pcg32State(vector.StateAfterSeed, vector.IncrementAfterSeed), pcg.GetState());

        var outputs = new uint[vector.Outputs.Count];
        for (int i = 0; i < outputs.Length; i++)
        {
            outputs[i] = pcg.NextUInt32();
        }

        Assert.Equal(vector.Outputs, outputs);
    }

    [Theory]
    [MemberData(nameof(RawStateVectors))]
    public void FromState_matches_reference_outputs_and_post_draw_state(Pcg32ReferenceVectors.RawStateVector vector)
    {
        var pcg = Pcg32.FromState(new Pcg32State(vector.State, vector.Increment));

        var outputs = new uint[vector.Outputs.Count];
        for (int i = 0; i < outputs.Length; i++)
        {
            outputs[i] = pcg.NextUInt32();
        }

        Assert.Equal(vector.Outputs, outputs);
        Assert.Equal(new Pcg32State(vector.StateAfter, vector.IncrementAfter), pcg.GetState());
    }

    [Fact]
    public void FromState_throws_on_default_Pcg32State()
    {
        // default(Pcg32State) reaches the struct's implicit parameterless constructor,
        // which zero-initializes Increment without ever running Pcg32State's own
        // validating constructor. Pcg32StateTests cannot see this path at all -- it can
        // only exercise the constructor -- so FromState has to be the gate that actually
        // catches it, or this would silently produce a generator that always returns 0.
        Assert.Throws<ArgumentException>(() => Pcg32.FromState(default));
    }

    [Fact]
    public void FromState_throws_on_Pcg32State_constructed_with_no_arguments()
    {
        // `new Pcg32State()` is the same bypass as `default(Pcg32State)` under different
        // syntax -- both reach the implicit parameterless constructor, not the validating
        // one -- but it is the shape a deserializer that forgets to call the real
        // constructor would actually produce, so it is worth pinning separately.
        Assert.Throws<ArgumentException>(() => Pcg32.FromState(new Pcg32State()));
    }

    [Theory]
    [MemberData(nameof(ResumeVectors))]
    public void Capture_mid_sequence_then_restore_resumes_rather_than_restarts(Pcg32ReferenceVectors.ResumeVector vector)
    {
        var pcg = Pcg32.FromSeed(vector.InitState, vector.InitSequence);
        for (int i = 0; i < vector.Skip; i++)
        {
            pcg.NextUInt32();
        }

        var captured = pcg.GetState();
        Assert.Equal(new Pcg32State(vector.StateAtCapture, vector.IncrementAtCapture), captured);

        // A fresh instance from the capture, not the one that produced it: this is what
        // "restore resumes, it does not restart" actually means -- if FromState secretly
        // re-seeded instead of resuming, this would reproduce the sequence's start, not
        // the values recorded after the capture point.
        var resumed = Pcg32.FromState(captured);
        var outputs = new uint[vector.OutputsAfterCapture.Count];
        for (int i = 0; i < outputs.Length; i++)
        {
            outputs[i] = resumed.NextUInt32();
        }

        Assert.Equal(vector.OutputsAfterCapture, outputs);
    }

    [Fact]
    public void Same_seed_and_stream_produce_identical_sequences()
    {
        var a = Pcg32.FromSeed(12345UL, 6789UL);
        var b = Pcg32.FromSeed(12345UL, 6789UL);

        for (int i = 0; i < 1000; i++)
        {
            Assert.Equal(a.NextUInt32(), b.NextUInt32());
        }
    }

    [Fact]
    public void Different_streams_with_the_same_seed_diverge_at_the_first_value()
    {
        var streamA = Pcg32ReferenceVectors.Seeded.Single(v => v.Name == "stream-a");
        var streamB = Pcg32ReferenceVectors.Seeded.Single(v => v.Name == "stream-b");
        var streamC = Pcg32ReferenceVectors.Seeded.Single(v => v.Name == "stream-c");

        // The fixture rows exist to compare same-seed/different-stream; confirm that
        // premise before relying on it, so a fixture edit that broke it would be caught
        // here rather than silently weakening this test.
        Assert.Equal(streamA.InitState, streamB.InitState);
        Assert.Equal(streamB.InitState, streamC.InitState);

        uint a = Pcg32.FromSeed(streamA.InitState, streamA.InitSequence).NextUInt32();
        uint b = Pcg32.FromSeed(streamB.InitState, streamB.InitSequence).NextUInt32();
        uint c = Pcg32.FromSeed(streamC.InitState, streamC.InitSequence).NextUInt32();

        Assert.NotEqual(a, b);
        Assert.NotEqual(a, c);
        Assert.NotEqual(b, c);
    }

    [Fact]
    public void Restoring_a_captured_state_does_not_perturb_the_original_instance()
    {
        var original = Pcg32.FromSeed(999UL, 1UL);
        for (int i = 0; i < 7; i++)
        {
            original.NextUInt32();
        }

        var captured = original.GetState();
        var restored = Pcg32.FromState(captured);

        // Drive the restored copy well past where the original will be asked to go. If
        // GetState/FromState aliased any mutable state instead of copying it, advancing
        // `restored` here would shift what `original` produces next.
        for (int i = 0; i < 50; i++)
        {
            restored.NextUInt32();
        }

        uint expectedNext = Pcg32.FromState(captured).NextUInt32();
        Assert.Equal(expectedNext, original.NextUInt32());
    }

    [Theory]
    [MemberData(nameof(RotationCases))]
    public void Rotation_step_agrees_with_BitOperations_RotateRight(ulong oldState)
    {
        // Increment only has to be odd to construct; it plays no role in this single
        // draw's xorshifted/rot computation, both of which depend solely on oldState.
        var pcg = Pcg32.FromState(new Pcg32State(oldState, 1UL));
        uint actual = pcg.NextUInt32();

        // Recomputes the pre-rotation intermediate value the same way the reference
        // does (that part is not in question), then checks the ROTATION specifically
        // against an independent implementation, rather than against a second copy of
        // the production rotate expression -- which would prove nothing.
        uint xorshifted = (uint)(((oldState >> 18) ^ oldState) >> 27);
        int rot = (int)(oldState >> 59);
        uint expected = BitOperations.RotateRight(xorshifted, rot);

        Assert.Equal(expected, actual);
    }

    public static IEnumerable<object[]> RotationCases()
    {
        // The reference rotates by the state's top 5 bits (oldstate >> 59), so bits
        // 63..59 fix rot and the remaining 59 bits are free. Masking a handful of
        // low-bit patterns into that free range exercises every rot value against
        // several different xorshifted operands rather than just one.
        ulong lowBitsMask = (1UL << 59) - 1UL;
        ulong[] lowBitPatterns =
        [
            0x0000000000000000UL,
            0xFFFFFFFFFFFFFFFFUL,
            0xDEADBEEFCAFEBABEUL,
            0x5555555555555555UL,
            0xAAAAAAAAAAAAAAAAUL,
        ];

        for (int rot = 0; rot < 32; rot++)
        {
            foreach (ulong lowBits in lowBitPatterns)
            {
                ulong oldState = ((ulong)rot << 59) | (lowBits & lowBitsMask);
                yield return new object[] { oldState };
            }
        }
    }
}
