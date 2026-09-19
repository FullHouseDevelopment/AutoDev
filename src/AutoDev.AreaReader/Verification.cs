using System.Text.RegularExpressions;

namespace AutoDev.AreaReader;

public static partial class AreaVerification
{
    public static IReadOnlyList<CommandGroup> BuildVerificationCommandGroups(
        RepositoryFacts facts,
        IReadOnlyList<string> areas)
    {
        var areaSet = new HashSet<string>(areas, StringComparer.Ordinal);
        var groups = new List<CommandGroup>();

        groups.Add(
            Group(
                "env",
                "Print local tool versions useful for interpreting benchmark verification.",
                [
                    Command("Show working directory", ".", ["pwd"]),
                    Command("Show Python version", ".", ["python3", "--version"], optional: true),
                    Command("Show dotnet SDK info", ".", ["dotnet", "--info"], optional: true),
                    Command("Show Node version", ".", ["node", "--version"], optional: true),
                    Command("Show npm version", ".", ["npm", "--version"], optional: true),
                ],
                recommended: true,
                reason: "Always useful for local environment diagnostics."));

        var dotnetCommands = new List<VerificationCommand>();
        var dotnetTargets = DotnetSolutionTargets(facts);
        var preferredFilter = PreferredSolutionFilter(facts.SolutionFilters);
        foreach (var solution in dotnetTargets)
        {
            dotnetCommands.Add(
                Command($"Restore {solution}", ".", ["dotnet", "restore", solution]));
            dotnetCommands.Add(
                Command(
                    $"Build {solution}",
                    ".",
                    ["dotnet", "build", solution, "--no-restore", "--verbosity", "minimal"]));
            dotnetCommands.Add(
                Command(
                    $"Test {solution}",
                    ".",
                    ["dotnet", "test", solution, "--no-build", "--verbosity", "minimal"]));
        }

        var dotnetReason = preferredFilter is not null
            ? $"Detected preferred .NET solution filter: {preferredFilter}."
            : dotnetCommands.Count > 0
                ? "Detected .NET solution files or filters."
                : "No .NET solution files or filters detected.";
        groups.Add(
            Group(
                "dotnet-solution",
                "Restore, build, and test the preferred .NET solution/filter verification surface from the repository root.",
                dotnetCommands,
                recommended: dotnetCommands.Count > 0
                    && areaSet.Overlaps(["backend", "maui", "tests"]),
                reason: dotnetReason));

        var nodeCommands = new List<VerificationCommand>();
        foreach (var packageInfo in facts.PackageRoots)
        {
            nodeCommands.Add(
                Command(
                    $"Install dependencies in {packageInfo.Root}",
                    packageInfo.Root,
                    packageInfo.InstallCommand,
                    optional: packageInfo.InstallCommand.SequenceEqual(["npm", "install"])));
        }

        groups.Add(
            Group(
                "node-root",
                "Install dependencies for detected JavaScript package roots.",
                nodeCommands,
                recommended: nodeCommands.Count > 0
                    && areaSet.Overlaps(["web", "api-client", "tests"]),
                reason: nodeCommands.Count > 0
                    ? "Detected package.json files."
                    : "No package.json files detected."));

        var apiCommands = new List<VerificationCommand>();
        foreach (var packageInfo in facts.ApiClientPackageRoots)
        {
            foreach (var scriptName in packageInfo.Scripts)
            {
                if (!scriptName.Contains("generate", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                apiCommands.Add(
                    Command(
                        $"Run {scriptName} in {packageInfo.Root}",
                        packageInfo.Root,
                        ScriptCommandForPackage(packageInfo, scriptName)));
            }
        }

        groups.Add(
            Group(
                "api-client-generate",
                "Run detected API client generation scripts.",
                apiCommands,
                recommended: apiCommands.Count > 0 && areaSet.Overlaps(["api-client", "web"]),
                reason: apiCommands.Count > 0
                    ? "Detected package scripts that look like API client generation."
                    : "No API client generation scripts detected."));

        var webCommands = new List<VerificationCommand>();
        foreach (var packageInfo in facts.WebPackageRoots)
        {
            foreach (var scriptName in new[] { "lint", "test", "build" })
            {
                if (!packageInfo.Scripts.Contains(scriptName, StringComparer.Ordinal))
                {
                    continue;
                }

                webCommands.Add(
                    Command(
                        $"Run {scriptName} in {packageInfo.Root}",
                        packageInfo.Root,
                        ScriptCommandForPackage(packageInfo, scriptName)));
            }
        }

        groups.Add(
            Group(
                "web-app",
                "Run detected web app lint, test, and build scripts.",
                webCommands,
                recommended: webCommands.Count > 0 && areaSet.Overlaps(["web", "tests"]),
                reason: webCommands.Count > 0
                    ? "Detected web package scripts."
                    : "No web lint/test/build scripts detected."));

        var mauiHelper = facts.MauiHelperScripts.FirstOrDefault();
        var mauiDoctorCommands = new List<VerificationCommand>();
        if (facts.MauiProjects.Count > 0)
        {
            if (mauiHelper is not null)
            {
                mauiDoctorCommands.Add(
                    Command(
                        $"Run {mauiHelper} doctor",
                        ".",
                        ["bash", mauiHelper, "doctor"]));
            }
            else
            {
                mauiDoctorCommands.Add(
                    Command("Show dotnet workloads", ".", ["dotnet", "workload", "list"]));
                mauiDoctorCommands.Add(
                    Command("Show dotnet SDK info", ".", ["dotnet", "--info"]));
            }
        }

        groups.Add(
            Group(
                "maui-android-doctor",
                "Inspect .NET MAUI Android workload availability without invoking remote CI.",
                mauiDoctorCommands,
                recommended: facts.MauiProjects.Count > 0 && areaSet.Contains("maui"),
                reason: facts.MauiProjects.Count > 0
                    ? "Detected MAUI project files."
                    : "No MAUI projects detected."));

        var mauiBuildCommands = new List<VerificationCommand>();
        if (mauiHelper is not null && facts.MauiProjects.Count > 0)
        {
            mauiBuildCommands.Add(
                Command(
                    $"Run {mauiHelper} build -c Debug",
                    ".",
                    ["bash", mauiHelper, "build", "-c", "Debug"]));
        }
        else
        {
            foreach (var project in facts.MauiProjects)
            {
                if (project.AndroidTargetFrameworks.Count > 0)
                {
                    foreach (var framework in project.AndroidTargetFrameworks)
                    {
                        mauiBuildCommands.Add(
                            Command(
                                $"Build {project.Path} for {framework}",
                                ".",
                                [
                                    "dotnet", "build", project.Path, "-f", framework,
                                    "--no-restore", "--verbosity", "minimal",
                                ]));
                    }
                }
                else
                {
                    mauiBuildCommands.Add(
                        Command(
                            $"Build {project.Path}",
                            ".",
                            ["dotnet", "build", project.Path, "--verbosity", "minimal"]));
                }
            }
        }

        groups.Add(
            Group(
                "maui-android-build",
                "Build detected MAUI Android target frameworks locally.",
                mauiBuildCommands,
                recommended: mauiBuildCommands.Count > 0 && areaSet.Contains("maui"),
                reason: mauiBuildCommands.Count > 0
                    ? "Detected MAUI Android build targets."
                    : "No MAUI Android targets detected."));

        groups.Add(
            Group(
                "markdown-smoke",
                "Validate tracked Markdown files for tabs and trailing whitespace.",
                facts.MarkdownFileCount > 0
                    ? [Command(
                        "Check tracked Markdown whitespace",
                        ".",
                        ["bash", "-lc", AreaReaderSettings.MarkdownSmokeScript])]
                    : [],
                recommended: facts.MarkdownFileCount > 0 && areaSet.Overlaps(["docs", "ci"]),
                reason: facts.MarkdownFileCount > 0
                    ? "Detected markdown files."
                    : "No markdown files detected."));

        groups.Add(
            Group(
                "ci-manual-reference",
                "Manual reference for detected workflow files; this group intentionally does not run remote CI.",
                [],
                recommended: false,
                reason: facts.WorkflowFiles.Count > 0
                    ? "Detected workflow files: " + string.Join(", ", facts.WorkflowFiles)
                    : "No workflow files detected.",
                manual: true));

        return groups;
    }

    public static string? PreferredSolutionFilter(IEnumerable<string> solutionFilters)
    {
        foreach (var solutionFilter in solutionFilters)
        {
            var normalized = solutionFilter.ToLowerInvariant();
            if (AreaReaderSettings.PreferredSolutionFilterMarkers.Any(
                    marker => normalized.Contains(marker, StringComparison.Ordinal)))
            {
                return solutionFilter;
            }
        }

        return null;
    }

    public static IReadOnlyList<string> DotnetSolutionTargets(RepositoryFacts facts)
    {
        var preferredFilter = PreferredSolutionFilter(facts.SolutionFilters);
        if (preferredFilter is not null)
        {
            return [preferredFilter];
        }

        if (facts.Solutions.Count > 0)
        {
            return [.. facts.Solutions];
        }

        return [.. facts.SolutionFilters];
    }

    public static RecommendationMetadata RecommendedCommandGroups(
        IReadOnlyList<CommandGroup> commandGroups,
        string issueText,
        IEnumerable<string>? changedPaths = null,
        bool? androidSdkAvailable = null)
    {
        var androidAvailable = androidSdkAvailable
            ?? !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("ANDROID_HOME"))
            || !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("ANDROID_SDK_ROOT"));

        return CommandGroupRecommendations.Recommend(
            issueText,
            changedPaths ?? [],
            androidSdkAvailable: androidAvailable,
            availableCommandGroups: commandGroups.Select(group => group.Name));
    }

    public static IReadOnlyList<CommandGroup> ApplyRecommendedCommandGroups(
        IReadOnlyList<CommandGroup> commandGroups,
        RecommendationMetadata recommendationMetadata)
    {
        var recommended = new HashSet<string>(
            recommendationMetadata.RecommendedCommandGroups,
            StringComparer.Ordinal);

        return commandGroups
            .Select(group => group with { Recommended = recommended.Contains(group.Name) })
            .ToArray();
    }

    public static string RenderVerificationScript(
        DirectoryInfo repository,
        IReadOnlyList<CommandGroup> commandGroups)
    {
        var lines = new List<string>
        {
            "#!/usr/bin/env bash",
            "set -Eeuo pipefail",
            string.Empty,
            "if git rev-parse --show-toplevel >/dev/null 2>&1; then",
            "  REPO_ROOT=\"$(git rev-parse --show-toplevel)\"",
            "else",
            $"  REPO_ROOT={ShellQuote(repository.FullName.Replace('\\', '/'))}",
            "fi",
            "cd \"$REPO_ROOT\"",
            string.Empty,
            "run_in() {",
            "  local dir=\"$1\"",
            "  shift",
            "  echo \"+ ($dir) $*\"",
            "  (cd \"$REPO_ROOT/$dir\" && \"$@\")",
            "}",
            string.Empty,
            "run_optional_in() {",
            "  local dir=\"$1\"",
            "  shift",
            "  if ! run_in \"$dir\" \"$@\"; then",
            "    echo \"optional command failed: $*\" >&2",
            "  fi",
            "}",
            string.Empty,
        };

        var groupNames = new List<string>();
        foreach (var group in commandGroups)
        {
            groupNames.Add(group.Name);
            lines.Add($"{ShellFunctionName(group.Name)}() {{");
            lines.Add($"  echo {ShellQuote("== " + group.Name + " ==")}");
            if (group.Manual)
            {
                lines.Add(
                    "  echo "
                    + ShellQuote(
                        "Manual reference only. Remote CI is not executed by this generated script."));
                lines.Add("  echo " + ShellQuote(group.Reason));
            }
            else if (group.Commands.Count == 0)
            {
                lines.Add("  echo " + ShellQuote(group.Reason));
            }
            else
            {
                foreach (var item in group.Commands)
                {
                    var runner = item.Optional ? "run_optional_in" : "run_in";
                    lines.Add(
                        $"  {runner} {ShellQuote(item.Cwd)} "
                        + string.Join(" ", item.Argv.Select(ShellQuote)));
                }
            }

            lines.Add("}");
            lines.Add(string.Empty);
        }

        lines.Add("usage() {");
        lines.Add("  echo \"Usage: $0 <group|recommended|all>\"");
        lines.Add("  echo");
        lines.Add("  echo \"Groups:\"");
        lines.AddRange(groupNames.Select(name => $"  echo {ShellQuote("  " + name)}"));
        lines.Add("}");
        lines.Add(string.Empty);
        lines.Add("run_group() {");
        lines.Add("  case \"$1\" in");

        foreach (var group in commandGroups)
        {
            lines.Add(
                $"    {ShellQuote(group.Name)}) {ShellFunctionName(group.Name)} ;;");
        }

        lines.Add("    recommended)");
        foreach (var group in commandGroups.Where(group => group.Recommended))
        {
            lines.Add($"      {ShellFunctionName(group.Name)}");
        }

        lines.Add("      ;;");
        lines.Add("    all)");
        foreach (var group in commandGroups.Where(group => !group.Manual))
        {
            lines.Add($"      {ShellFunctionName(group.Name)}");
        }

        lines.Add("      ;;");
        lines.Add("    \"\"|-h|--help|help)");
        lines.Add("      usage");
        lines.Add("      ;;");
        lines.Add("    *)");
        lines.Add("      echo \"Unknown command group: $1\" >&2");
        lines.Add("      usage >&2");
        lines.Add("      return 2");
        lines.Add("      ;;");
        lines.Add("  esac");
        lines.Add("}");
        lines.Add(string.Empty);
        lines.Add("run_group \"${1:-help}\"");
        lines.Add(string.Empty);

        return string.Join('\n', lines);
    }

    public static string ShellFunctionName(string groupName) =>
        "group_" + groupName.Replace('-', '_');

    private static VerificationCommand Command(
        string label,
        string cwd,
        IReadOnlyList<string> argv,
        bool optional = false) =>
        new(label, cwd, argv, optional);

    private static CommandGroup Group(
        string name,
        string description,
        IReadOnlyList<VerificationCommand> commands,
        bool recommended = false,
        string reason = "",
        bool manual = false) =>
        new(name, description, recommended, reason, manual, commands);

    private static IReadOnlyList<string> ScriptCommandForPackage(
        PackageRootFacts packageInfo,
        string scriptName) =>
        packageInfo.PackageManager switch
        {
            "yarn" => ["yarn", scriptName],
            "bun" => ["bun", "run", scriptName],
            _ => [packageInfo.PackageManager, "run", scriptName],
        };

    private static string ShellQuote(string value)
    {
        if (value.Length > 0 && ShellSafePattern().IsMatch(value))
        {
            return value;
        }

        return "'" + value.Replace("'", "'\"'\"'", StringComparison.Ordinal) + "'";
    }

    [GeneratedRegex("^[A-Za-z0-9_@%+=:,./-]+$", RegexOptions.CultureInvariant)]
    private static partial Regex ShellSafePattern();
}
