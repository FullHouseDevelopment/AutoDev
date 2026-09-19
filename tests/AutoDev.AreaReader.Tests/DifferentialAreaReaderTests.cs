using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using NUnit.Framework;

namespace AutoDev.AreaReader.Tests;

[TestFixture]
public sealed class DifferentialAreaReaderTests
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    [Test]
    public async Task RoutingAndAreaClassification_MatchPythonReference()
    {
        const string issue = "Fix React UI and verify MAUI Android build.";
        var route = AreaRouting.RouteAreas(issue, "auto");
        var candidate = ToNode(
            new
            {
                areas = route.Areas,
                metadata = route.Metadata,
                classification = new Dictionary<string, bool>
                {
                    ["generic_ts"] = RepositoryInspector.AreaForFile(
                        "apps/web/src/App.tsx",
                        "api-client"),
                    ["generic_cs"] = RepositoryInspector.AreaForFile(
                        "apps/api/Controllers/FooController.cs",
                        "api-client"),
                    ["api_client"] = RepositoryInspector.AreaForFile(
                        "packages/api-client/src/generated.ts",
                        "api-client"),
                    ["openapi"] = RepositoryInspector.AreaForFile(
                        "apps/api/openapi.json",
                        "api-client"),
                },
            });

        var reference = await RunPythonJsonAsync(
            """
import json
from area_reader.routing import route_areas
from area_reader.repository import area_for_file
issue = "Fix React UI and verify MAUI Android build."
areas, metadata = route_areas(issue, "auto")
print(json.dumps({
    "areas": areas,
    "metadata": metadata,
    "classification": {
        "generic_ts": area_for_file("apps/web/src/App.tsx", "api-client"),
        "generic_cs": area_for_file("apps/api/Controllers/FooController.cs", "api-client"),
        "api_client": area_for_file("packages/api-client/src/generated.ts", "api-client"),
        "openapi": area_for_file("apps/api/openapi.json", "api-client"),
    },
}, sort_keys=True))
""");

        AssertJsonEqual(reference, candidate);
    }

    [Test]
    public async Task RepositoryDiscoveryAndFacts_MatchPythonReference()
    {
        using var fixture = new AreaReaderFixture();
        var collection = RepositoryInspector.CollectRepoFiles(fixture.Root);
        var route = AreaRouting.RouteAreas("Fix React UI and MAUI Android build.", "auto");
        var facts = RepositoryInspector.DetectRepoFacts(
            fixture.Root,
            collection.Files,
            route.Areas,
            route.Metadata);
        var candidate = ToNode(
            new
            {
                files = collection.Files,
                skipped_large_files = collection.SkippedLargeFiles,
                skipped_unreadable_files = collection.SkippedUnreadableFiles,
                facts,
            });

        var reference = await RunPythonJsonAsync(
            """
import json, sys
from pathlib import Path
from area_reader.repository import collect_repo_files, detect_repo_facts
from area_reader.routing import route_areas
repo = Path(sys.argv[1])
files, skipped_large_files, skipped_unreadable_files = collect_repo_files(repo)
areas, routing = route_areas("Fix React UI and MAUI Android build.", "auto")
facts = detect_repo_facts(repo, files, areas, routing)
print(json.dumps({
    "files": files,
    "skipped_large_files": skipped_large_files,
    "skipped_unreadable_files": skipped_unreadable_files,
    "facts": facts,
}, sort_keys=True))
""",
            fixture.Root.FullName);

        AssertJsonEqual(reference, candidate);
    }

    [Test]
    public async Task RecommendationMetadata_MatchesPythonReference()
    {
        var changedPaths = new[]
        {
            "phoodab/apps/mobile/MainPage.xaml",
            "phoodab/packages/api-client/openapi.json",
        };
        var candidate = ToNode(
            CommandGroupRecommendations.Recommend(
                "Regenerate the OpenAPI client and verify the MAUI mobile build.",
                changedPaths,
                androidSdkAvailable: true));

        var reference = await RunPythonJsonAsync(
            """
import json
from area_reader.recommendations import recommend_command_groups
result = recommend_command_groups(
    issue_text="Regenerate the OpenAPI client and verify the MAUI mobile build.",
    changed_paths=[
        "phoodab/apps/mobile/MainPage.xaml",
        "phoodab/packages/api-client/openapi.json",
    ],
    android_sdk_available=True,
)
print(json.dumps(result, sort_keys=True))
""");

        AssertJsonEqual(reference, candidate);
    }

    [Test]
    public async Task VerificationGroups_MatchPythonReference()
    {
        using var fixture = new AreaReaderFixture();
        var (facts, areas) = CandidateFacts(fixture);
        var candidate = ToNode(AreaVerification.BuildVerificationCommandGroups(facts, areas));

        var reference = await RunPythonJsonAsync(
            """
import json, sys
from pathlib import Path
from area_reader.repository import collect_repo_files, detect_repo_facts
from area_reader.routing import route_areas
from area_reader.verification import build_verification_command_groups
repo = Path(sys.argv[1])
files, _, _ = collect_repo_files(repo)
areas, routing = route_areas("Fix React UI and MAUI Android build.", "auto")
facts = detect_repo_facts(repo, files, areas, routing)
groups = build_verification_command_groups(facts, areas)
print(json.dumps(groups, sort_keys=True))
""",
            fixture.Root.FullName);

        AssertJsonEqual(reference, candidate);
    }

    [Test]
    public async Task VerificationScript_MatchesPythonReference()
    {
        using var fixture = new AreaReaderFixture();
        var (facts, areas) = CandidateFacts(fixture);
        var groups = AreaVerification.BuildVerificationCommandGroups(facts, areas);
        var candidate = AreaVerification.RenderVerificationScript(fixture.Root, groups);

        var reference = await RunPythonTextAsync(
            """
import sys
from pathlib import Path
from area_reader.repository import collect_repo_files, detect_repo_facts
from area_reader.routing import route_areas
from area_reader.verification import build_verification_command_groups, render_verification_script
repo = Path(sys.argv[1])
files, _, _ = collect_repo_files(repo)
areas, routing = route_areas("Fix React UI and MAUI Android build.", "auto")
facts = detect_repo_facts(repo, files, areas, routing)
groups = build_verification_command_groups(facts, areas)
print(render_verification_script(repo, groups), end="")
""",
            fixture.Root.FullName);

        Assert.That(NormalizeNewlines(candidate), Is.EqualTo(NormalizeNewlines(reference)));
    }

    [Test]
    public async Task BoundedContextBundle_MatchesPythonReference()
    {
        using var fixture = new AreaReaderFixture();
        var collection = RepositoryInspector.CollectRepoFiles(fixture.Root);
        var selected = AreaRouting.AreaFileMap(collection.Files, "web");
        var repoMap = RepositoryInspector.BuildRepoMap(
            fixture.Root,
            collection.Files,
            collection.SkippedLargeFiles,
            collection.SkippedUnreadableFiles);
        var candidate = ToNode(
            AreaContextBuilder.BuildAreaBundle(
                fixture.Root,
                "web",
                "Fix React UI.",
                repoMap,
                selected,
                2_000));

        var reference = await RunPythonJsonAsync(
            """
import json, sys
from pathlib import Path
from area_reader.context import build_area_bundle
from area_reader.repository import build_repo_map, collect_repo_files
from area_reader.routing import area_file_map
repo = Path(sys.argv[1])
files, large, unreadable = collect_repo_files(repo)
selected = area_file_map(files, "web")
repo_map = build_repo_map(repo, files, large, unreadable)
bundle, metadata, file_map_text = build_area_bundle(
    repo, "web", "Fix React UI.", repo_map, selected, 2000)
print(json.dumps({
    "bundle": bundle,
    "metadata": metadata,
    "file_map_text": file_map_text,
}, sort_keys=True))
""",
            fixture.Root.FullName);

        AssertJsonEqual(reference, candidate);
    }

    [Test]
    public async Task ReaderSynthesisAndPlannerPrompts_MatchPythonReference()
    {
        using var fixture = new AreaReaderFixture();
        var collection = RepositoryInspector.CollectRepoFiles(fixture.Root);
        var route = AreaRouting.RouteAreas("Fix React UI.", "auto");
        var facts = RepositoryInspector.DetectRepoFacts(
            fixture.Root,
            collection.Files,
            route.Areas,
            route.Metadata);
        var groups = AreaVerification.BuildVerificationCommandGroups(facts, route.Areas);
        var recommendations = AreaVerification.RecommendedCommandGroups(
            groups,
            "Fix React UI.",
            androidSdkAvailable: false);
        var selected = AreaRouting.AreaFileMap(collection.Files, "web");
        var repoMap = RepositoryInspector.BuildRepoMap(
            fixture.Root,
            collection.Files,
            collection.SkippedLargeFiles,
            collection.SkippedUnreadableFiles);
        var context = AreaContextBuilder.BuildAreaBundle(
            fixture.Root,
            "web",
            "Fix React UI.",
            repoMap,
            selected,
            2_000);
        const string brief = "Web reader factual brief.";

        var candidate = ToNode(
            new
            {
                reader = AreaPrompts.BuildAreaReaderPrompt(
                    "Fix React UI.",
                    "web",
                    context.Bundle,
                    context.Metadata),
                synthesis = AreaPrompts.BuildSynthesisPrompt(
                    "Fix React UI.",
                    route.Areas,
                    [new("web", context.Metadata, brief)],
                    facts,
                    groups),
                planner = AreaPrompts.BuildPlannerPrompt(
                    "Fix React UI.",
                    "Synthesized handoff.",
                    facts,
                    recommendations,
                    groups),
            });

        var reference = await RunPythonJsonAsync(
            """
import json, sys
from pathlib import Path
from area_reader.context import build_area_bundle
from area_reader.prompts import build_area_reader_prompt, build_synthesis_prompt, build_coder_prompt
from area_reader.repository import build_repo_map, collect_repo_files, detect_repo_facts
from area_reader.routing import area_file_map, route_areas
from area_reader.verification import build_verification_command_groups, recommended_command_groups
repo = Path(sys.argv[1])
issue = "Fix React UI."
files, large, unreadable = collect_repo_files(repo)
areas, routing = route_areas(issue, "auto")
facts = detect_repo_facts(repo, files, areas, routing)
groups = build_verification_command_groups(facts, areas)
recommendations = recommended_command_groups(
    groups, issue_text=issue, changed_paths=(), android_sdk_available=False)
selected = area_file_map(files, "web")
repo_map = build_repo_map(repo, files, large, unreadable)
bundle, metadata, _ = build_area_bundle(repo, "web", issue, repo_map, selected, 2000)
brief = "Web reader factual brief."
print(json.dumps({
    "reader": build_area_reader_prompt(issue, "web", bundle, metadata),
    "synthesis": build_synthesis_prompt(
        issue,
        areas,
        [{"area": "web", "metadata": metadata, "brief": brief}],
        facts,
        groups,
    ),
    "planner": build_coder_prompt(
        issue,
        "Synthesized handoff.",
        facts,
        recommendations,
        groups,
    ),
}, sort_keys=True))
""",
            fixture.Root.FullName);

        AssertJsonEqual(reference, candidate);
    }

    private static (RepositoryFacts Facts, IReadOnlyList<string> Areas) CandidateFacts(
        AreaReaderFixture fixture)
    {
        var collection = RepositoryInspector.CollectRepoFiles(fixture.Root);
        var route = AreaRouting.RouteAreas("Fix React UI and MAUI Android build.", "auto");
        var facts = RepositoryInspector.DetectRepoFacts(
            fixture.Root,
            collection.Files,
            route.Areas,
            route.Metadata);
        return (facts, route.Areas);
    }

    private static JsonNode ToNode(object value) =>
        JsonSerializer.SerializeToNode(value, value.GetType(), JsonOptions)
        ?? throw new InvalidOperationException("Failed to serialize candidate observation.");

    private static void AssertJsonEqual(JsonNode reference, JsonNode candidate)
    {
        Assert.That(
            JsonNode.DeepEquals(reference, candidate),
            Is.True,
            $"Reference:\n{reference.ToJsonString(new JsonSerializerOptions { WriteIndented = true })}"
            + $"\nCandidate:\n{candidate.ToJsonString(new JsonSerializerOptions { WriteIndented = true })}");
    }

    private static async Task<JsonNode> RunPythonJsonAsync(
        string script,
        params string[] arguments)
    {
        var output = await RunPythonTextAsync(script, arguments);
        return JsonNode.Parse(output)
            ?? throw new InvalidOperationException("Python reference returned null JSON.");
    }

    private static async Task<string> RunPythonTextAsync(
        string script,
        params string[] arguments)
    {
        var executable = Environment.GetEnvironmentVariable("AUTODEV_COMPAT_PYTHON")
            ?? (OperatingSystem.IsWindows() ? "python" : "python3");
        var startInfo = new ProcessStartInfo
        {
            FileName = executable,
            WorkingDirectory = RepositoryRoot.Find(),
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        startInfo.ArgumentList.Add("-c");
        startInfo.ArgumentList.Add(script);
        foreach (var argument in arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = Process.Start(startInfo)
            ?? throw new InvalidOperationException($"Failed to start {executable}.");
        var stdoutTask = process.StandardOutput.ReadToEndAsync();
        var stderrTask = process.StandardError.ReadToEndAsync();
        await process.WaitForExitAsync();
        var stdout = await stdoutTask;
        var stderr = await stderrTask;

        Assert.That(
            process.ExitCode,
            Is.Zero,
            $"Python reference failed with exit {process.ExitCode}:\n{stderr}");
        return NormalizeNewlines(stdout);
    }

    private static string NormalizeNewlines(string value) =>
        value.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n');
}

internal sealed class AreaReaderFixture : IDisposable
{
    public AreaReaderFixture()
    {
        Root = new DirectoryInfo(
            Path.Combine(
                Path.GetTempPath(),
                "autodev-area-reader-" + Guid.NewGuid().ToString("N")));
        Root.Create();

        Write("README.md", "# Fixture\n");
        Write("App.sln", string.Empty);
        Write("App.ci.slnf", "{}\n");
        Write(
            "apps/mobile/Mobile.csproj",
            "<Project><PropertyGroup><UseMaui>true</UseMaui>"
            + "<TargetFrameworks>net10.0;net10.0-android</TargetFrameworks>"
            + "</PropertyGroup></Project>\n");
        Write(
            "apps/mobile/scripts/maui-android-ubuntu.sh",
            "#!/usr/bin/env bash\n");
        Write(
            "apps/web/package.json",
            "{\"scripts\":{\"lint\":\"lint\",\"test\":\"test\",\"build\":\"build\"},"
            + "\"dependencies\":{\"react\":\"1.0.0\"}}\n");
        Write("apps/web/package-lock.json", "{}\n");
        Write(
            "packages/api-client/package.json",
            "{\"scripts\":{\"generate-client\":\"generate\"},"
            + "\"devDependencies\":{\"openapi-typescript\":\"1.0.0\"}}\n");
        Write("packages/api-client/package-lock.json", "{}\n");
        Write(".github/workflows/ci.yml", "name: CI\n");
        Write("docs/guide.md", "# Guide\n");
        Write("apps/web/src/App.tsx", "export const App = () => null;\n");
        Write("apps/api/Controllers/FooController.cs", "class FooController {}\n");
        Write("apps/api/openapi.json", "{}\n");
    }

    public DirectoryInfo Root { get; }

    public void Dispose()
    {
        if (Root.Exists)
        {
            Root.Delete(recursive: true);
        }
    }

    private void Write(string relativePath, string content)
    {
        var path = Path.Combine(Root.FullName, relativePath.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, content);
    }
}

internal static class RepositoryRoot
{
    public static string Find()
    {
        var current = new DirectoryInfo(AppContext.BaseDirectory);
        while (current is not null)
        {
            if (File.Exists(Path.Combine(current.FullName, "pyproject.toml")))
            {
                return current.FullName;
            }

            current = current.Parent;
        }

        throw new DirectoryNotFoundException("Could not locate the AutoDev repository root.");
    }
}
