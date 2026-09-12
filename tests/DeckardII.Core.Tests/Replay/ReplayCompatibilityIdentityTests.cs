using DeckardII.Core.Replay;

namespace DeckardII.Core.Tests.Replay;

/// <summary>
/// The acceptance test is deliberately not "two identical identities are equal" alone:
/// each of the four components must independently break equality when it alone differs,
/// holding the other three fixed.
/// </summary>
public sealed class ReplayCompatibilityIdentityTests
{
    private const string BaselineHash = "7db88be98dbc2a4d1f777b7ff10034af3ea35e20bb3899b2aad95b7232bbac41";
    private const string RepinnedHash = "0000000000000000000000000000000000000000000000000000000000000000";

    private static ReplayCompatibilityIdentity Baseline() => new(
        RandomAlgorithmId.Pcg32SetSeq64XshRr32,
        new RulesetVersion("fixture-core-ruleset", 1),
        new ReplaySchemaVersion(1),
        new SourceBaselineId("fixture-core", BaselineHash));

    [Fact]
    public void Identical_components_compare_equal()
    {
        ReplayCompatibilityIdentity a = Baseline();
        ReplayCompatibilityIdentity b = Baseline();

        Assert.Equal(a, b);
        Assert.True(a == b);
        Assert.Equal(a.GetHashCode(), b.GetHashCode());
    }

    [Fact]
    public void Differing_only_in_random_algorithm_is_unequal()
    {
        ReplayCompatibilityIdentity a = Baseline();
        ReplayCompatibilityIdentity b = a with { RandomAlgorithm = new RandomAlgorithmId("some-other-generator") };

        Assert.NotEqual(a, b);
    }

    [Fact]
    public void Differing_only_in_ruleset_is_unequal()
    {
        ReplayCompatibilityIdentity a = Baseline();
        ReplayCompatibilityIdentity b = a with { Ruleset = new RulesetVersion("fixture-core-ruleset", 2) };

        Assert.NotEqual(a, b);
    }

    [Fact]
    public void Differing_only_in_replay_schema_is_unequal()
    {
        ReplayCompatibilityIdentity a = Baseline();
        ReplayCompatibilityIdentity b = a with { ReplaySchema = new ReplaySchemaVersion(2) };

        Assert.NotEqual(a, b);
    }

    [Fact]
    public void Differing_only_in_source_baseline_id_is_unequal()
    {
        ReplayCompatibilityIdentity a = Baseline();
        ReplayCompatibilityIdentity b = a with
        {
            SourceBaseline = new SourceBaselineId("fixture-alternate-source", BaselineHash),
        };

        Assert.NotEqual(a, b);
    }

    [Fact]
    public void Differing_only_in_source_baseline_hash_is_unequal()
    {
        // The specific gap this Issue's review found: a source-baseline re-pin edits
        // sha256 in place and leaves sourceId untouched. An identity built from a
        // SourceBaselineId that ignored the hash would miss this exact event.
        ReplayCompatibilityIdentity a = Baseline();
        ReplayCompatibilityIdentity b = a with
        {
            SourceBaseline = new SourceBaselineId("fixture-core", RepinnedHash),
        };

        Assert.NotEqual(a, b);
    }

    [Fact]
    public void An_identity_differing_in_ruleset_does_not_equal_one_differing_in_schema()
    {
        // The acceptance criterion names this pair explicitly: two identities that each
        // differ from the baseline in a different single component must not collide with
        // each other either.
        ReplayCompatibilityIdentity rulesetChanged = Baseline() with
        {
            Ruleset = new RulesetVersion("fixture-core-ruleset", 2),
        };
        ReplayCompatibilityIdentity schemaChanged = Baseline() with
        {
            ReplaySchema = new ReplaySchemaVersion(2),
        };

        Assert.NotEqual(rulesetChanged, schemaChanged);
    }
}
