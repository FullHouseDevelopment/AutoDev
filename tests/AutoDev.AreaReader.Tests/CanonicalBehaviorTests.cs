using NUnit.Framework;

namespace AutoDev.AreaReader.Tests;

[TestFixture]
public sealed class CanonicalBehaviorTests
{
    [Test]
    public void GenericIssue_UsesConservativeDefaultRecommendations()
    {
        var result = CommandGroupRecommendations.Recommend(
            "Run local verification before issue-to-PR readiness.",
            []);

        Assert.That(
            result.RecommendedCommandGroups,
            Is.EqualTo(["env", "dotnet-solution", "node-root", "markdown-smoke"]));
        Assert.That(result.RecommendedCommandGroups, Does.Not.Contain("maui-android-doctor"));
        Assert.That(result.RecommendedCommandGroups, Does.Not.Contain("maui-android-build"));
        Assert.That(result.RecommendedCommandGroups, Does.Not.Contain("ci-manual-reference"));
    }

    [Test]
    public void DocumentationOnlyIssue_AvoidsPlatformBuilds()
    {
        var result = CommandGroupRecommendations.Recommend(
            "Documentation-only architecture issue. Explain backend behavior.",
            ["docs/architecture.md"],
            androidSdkAvailable: true);

        Assert.That(result.RecommendedCommandGroups, Is.EqualTo(["env", "markdown-smoke"]));
    }

    [Test]
    public void MauiBuild_RequiresScopeAndAndroidAvailability()
    {
        var missing = CommandGroupRecommendations.Recommend(
            "Fix MAUI mobile build.",
            ["phoodab/apps/mobile/PHOODAB.Mobile.csproj"],
            androidSdkAvailable: false);
        var available = CommandGroupRecommendations.Recommend(
            "Fix MAUI mobile build.",
            ["phoodab/apps/mobile/PHOODAB.Mobile.csproj"],
            androidSdkAvailable: true);

        Assert.That(missing.RecommendedCommandGroups, Does.Contain("maui-android-doctor"));
        Assert.That(missing.RecommendedCommandGroups, Does.Not.Contain("maui-android-build"));
        Assert.That(available.RecommendedCommandGroups, Does.Contain("maui-android-build"));
    }

    [Test]
    public void ApiClientArea_DoesNotMatchGenericSourceExtensions()
    {
        Assert.That(
            RepositoryInspector.AreaForFile("apps/web/src/App.tsx", "api-client"),
            Is.False);
        Assert.That(
            RepositoryInspector.AreaForFile("apps/api/Controllers/FooController.cs", "api-client"),
            Is.False);
        Assert.That(
            RepositoryInspector.AreaForFile("packages/api-client/src/generated.ts", "api-client"),
            Is.True);
        Assert.That(
            RepositoryInspector.AreaForFile("apps/api/openapi.json", "api-client"),
            Is.True);
    }

    [Test]
    public void PreferredSolutionFilter_MatchesCurrentVerificationPolicy()
    {
        foreach (var marker in AreaReaderSettings.PreferredSolutionFilterMarkers)
        {
            var path = $"verification/Foo.{marker}.slnf";
            Assert.That(
                AreaVerification.PreferredSolutionFilter([path]),
                Is.EqualTo(path),
                marker);
        }

        Assert.That(AreaVerification.PreferredSolutionFilter(["Foo.desktop.slnf"]), Is.Null);
    }

    [Test]
    public void VerificationGroups_UsePreferredFilterAndRealDotnetTest()
    {
        var facts = EmptyFacts(
            solutions: ["App.sln"],
            solutionFilters: ["App.ci.slnf"]);
        var groups = AreaVerification.BuildVerificationCommandGroups(facts, ["backend"]);

        var dotnet = groups.Single(group => group.Name == "dotnet-solution");
        Assert.That(
            dotnet.Commands.Select(command => command.Argv),
            Is.EqualTo(new[]
            {
                new[] { "dotnet", "restore", "App.ci.slnf" },
                new[] { "dotnet", "build", "App.ci.slnf", "--no-restore", "--verbosity", "minimal" },
                new[] { "dotnet", "test", "App.ci.slnf", "--no-build", "--verbosity", "minimal" },
            }));
    }

    [Test]
    public void VerificationGroups_PreferDetectedMauiHelper()
    {
        const string helper = "apps/mobile/scripts/maui-android-ubuntu.sh";
        var facts = EmptyFacts(
            mauiProjects:
            [
                new(
                    "apps/mobile/Mobile.csproj",
                    ["net10.0-android"],
                    ["net10.0-android"]),
            ],
            mauiHelperScripts: [helper]);

        var groups = AreaVerification.BuildVerificationCommandGroups(facts, ["maui"]);
        var doctor = groups.Single(group => group.Name == "maui-android-doctor");
        var build = groups.Single(group => group.Name == "maui-android-build");

        Assert.That(doctor.Commands[0].Argv, Is.EqualTo(new[] { "bash", helper, "doctor" }));
        Assert.That(
            build.Commands[0].Argv,
            Is.EqualTo(new[] { "bash", helper, "build", "-c", "Debug" }));
    }

    [Test]
    public void VerificationScript_UsesDynamicRepositoryRoot()
    {
        var script = AreaVerification.RenderVerificationScript(
            new DirectoryInfo(Path.GetFullPath("/fallback/repo")),
            []);

        Assert.That(
            script,
            Does.Contain("if git rev-parse --show-toplevel >/dev/null 2>&1; then"));
        Assert.That(script, Does.Contain("REPO_ROOT=\"$(git rev-parse --show-toplevel)\""));
    }

    private static RepositoryFacts EmptyFacts(
        IReadOnlyList<string>? solutions = null,
        IReadOnlyList<string>? solutionFilters = null,
        IReadOnlyList<MauiProjectFacts>? mauiProjects = null,
        IReadOnlyList<string>? mauiHelperScripts = null) =>
        new(
            ".",
            [],
            new("explicit", new Dictionary<string, IReadOnlyList<string>>(), false),
            0,
            AreaReaderSettings.SupportedAreas.ToDictionary(area => area, _ => 0),
            solutions ?? [],
            solutionFilters ?? [],
            [],
            new Dictionary<string, CsprojFacts>(),
            mauiProjects ?? [],
            mauiHelperScripts ?? [],
            [],
            [],
            [],
            [],
            [],
            0,
            []);
}
