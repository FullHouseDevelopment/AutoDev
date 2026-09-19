using System.Diagnostics;
using System.IO.Enumeration;
using System.Text.Json;
using System.Xml.Linq;

namespace AutoDev.AreaReader;

public static class RepositoryInspector
{
    public static bool IsIncludedFile(FileInfo path) =>
        AreaReaderSettings.IncludedFilenames.Contains(path.Name)
        || AreaReaderSettings.IncludedSuffixes.Contains(path.Extension);

    public static bool IsPriorityFile(string relativePath) =>
        MatchesAny(relativePath, AreaReaderSettings.PriorityPatterns)
        || AreaReaderSettings.IncludedFilenames.Contains(Path.GetFileName(relativePath));

    public static bool AreaForFile(string relativePath, string area)
    {
        var hints = AreaReaderSettings.AreaHints[area];
        var lowered = relativePath.ToLowerInvariant();
        return MatchesAny(relativePath, hints.PathPatterns)
            || hints.Keywords.Any(keyword => lowered.Contains(keyword, StringComparison.Ordinal));
    }

    public static RepoFileCollection CollectRepoFiles(DirectoryInfo repository)
    {
        var files = new List<RepoFile>();
        var skippedLargeFiles = new List<SkippedLargeFile>();
        var skippedUnreadableFiles = new List<SkippedUnreadableFile>();

        foreach (var path in EnumerateCandidateFiles(repository))
        {
            long size;
            try
            {
                size = path.Length;
            }
            catch (IOException exception)
            {
                skippedUnreadableFiles.Add(new(path.FullName, exception.Message));
                continue;
            }
            catch (UnauthorizedAccessException exception)
            {
                skippedUnreadableFiles.Add(new(path.FullName, exception.Message));
                continue;
            }

            var relativePath = NormalizeRelativePath(
                Path.GetRelativePath(repository.FullName, path.FullName));
            if (size > AreaReaderSettings.MaxFileBytes)
            {
                skippedLargeFiles.Add(new(relativePath, size));
                continue;
            }

            var areas = AreaReaderSettings.SupportedAreas
                .Where(area => AreaForFile(relativePath, area))
                .ToArray();
            files.Add(new(relativePath, size, IsPriorityFile(relativePath), areas));
        }

        files.Sort(CompareRepoFiles);
        skippedLargeFiles.Sort((left, right) =>
            StringComparer.Ordinal.Compare(left.Path, right.Path));
        skippedUnreadableFiles.Sort((left, right) =>
            StringComparer.Ordinal.Compare(left.Path, right.Path));

        return new(files, skippedLargeFiles, skippedUnreadableFiles);
    }

    public static string BuildRepoMap(
        DirectoryInfo repository,
        IEnumerable<RepoFile> files,
        IEnumerable<SkippedLargeFile> skippedLargeFiles,
        IEnumerable<SkippedUnreadableFile> skippedUnreadableFiles)
    {
        var lines = new List<string>
        {
            $"Repository: {repository.FullName}",
            string.Empty,
            "Candidate files:",
        };

        foreach (var item in files)
        {
            var flags = new List<string>();
            if (item.Priority)
            {
                flags.Add("priority");
            }

            if (item.Areas.Count > 0)
            {
                flags.Add("areas=" + string.Join(",", item.Areas));
            }

            var suffix = flags.Count == 0 ? string.Empty : $" [{string.Join("; ", flags)}]";
            lines.Add($"- {item.Path} ({item.Bytes} bytes){suffix}");
        }

        var large = skippedLargeFiles.ToArray();
        if (large.Length > 0)
        {
            lines.Add(string.Empty);
            lines.Add("Skipped large files:");
            lines.AddRange(large.Select(item => $"- {item.Path} ({item.Bytes} bytes)"));
        }

        var unreadable = skippedUnreadableFiles.ToArray();
        if (unreadable.Length > 0)
        {
            lines.Add(string.Empty);
            lines.Add("Skipped unreadable files:");
            lines.AddRange(unreadable.Select(item => $"- {item.Path}: {item.Reason}"));
        }

        return string.Join('\n', lines) + "\n";
    }

    public static CsprojFacts ReadCsprojFacts(FileInfo path)
    {
        try
        {
            var document = XDocument.Load(path.FullName, LoadOptions.None);
            var useMaui = false;
            var frameworks = new HashSet<string>(StringComparer.Ordinal);
            foreach (var element in document.Descendants())
            {
                var text = element.Value.Trim();
                if (element.Name.LocalName == "UseMaui"
                    && text.Equals("true", StringComparison.OrdinalIgnoreCase))
                {
                    useMaui = true;
                }
                else if (element.Name.LocalName == "TargetFramework" && text.Length > 0)
                {
                    frameworks.Add(text);
                }
                else if (element.Name.LocalName == "TargetFrameworks" && text.Length > 0)
                {
                    foreach (var framework in text.Split(';', StringSplitOptions.RemoveEmptyEntries))
                    {
                        var normalized = framework.Trim();
                        if (normalized.Length > 0)
                        {
                            frameworks.Add(normalized);
                        }
                    }
                }
            }

            var targets = frameworks.OrderBy(value => value, StringComparer.Ordinal).ToArray();
            var androidTargets = targets
                .Where(value => value.Contains("android", StringComparison.OrdinalIgnoreCase))
                .ToArray();
            return new(useMaui, targets, androidTargets);
        }
        catch (IOException)
        {
            return EmptyCsprojFacts();
        }
        catch (UnauthorizedAccessException)
        {
            return EmptyCsprojFacts();
        }
        catch (System.Xml.XmlException)
        {
            return EmptyCsprojFacts();
        }
    }

    public static RepositoryFacts DetectRepoFacts(
        DirectoryInfo repository,
        IReadOnlyList<RepoFile> files,
        IReadOnlyList<string> areas,
        RoutingMetadata routing)
    {
        var filePaths = files.Select(item => item.Path).ToArray();
        var filePathSet = new HashSet<string>(filePaths, StringComparer.Ordinal);

        var solutions = filePaths
            .Where(path => path.EndsWith(".sln", StringComparison.Ordinal))
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToArray();
        var solutionFilters = filePaths
            .Where(path => path.EndsWith(".slnf", StringComparison.Ordinal))
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToArray();
        var dotnetProjects = filePaths
            .Where(path => path.EndsWith(".csproj", StringComparison.Ordinal))
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToArray();
        var workflows = filePaths
            .Where(path =>
                path.StartsWith(".github/workflows/", StringComparison.Ordinal)
                && (path.EndsWith(".yml", StringComparison.Ordinal)
                    || path.EndsWith(".yaml", StringComparison.Ordinal)))
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToArray();
        var markdownFiles = filePaths
            .Where(path => path.EndsWith(".md", StringComparison.Ordinal))
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToArray();
        var mauiHelperScripts = filePaths
            .Where(path =>
                path.EndsWith(".sh", StringComparison.Ordinal)
                && path.Contains("maui", StringComparison.OrdinalIgnoreCase)
                && path.Contains("android", StringComparison.OrdinalIgnoreCase))
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToArray();

        var csprojFacts = new Dictionary<string, CsprojFacts>(StringComparer.Ordinal);
        var mauiProjects = new List<MauiProjectFacts>();
        foreach (var relativePath in dotnetProjects)
        {
            var facts = ReadCsprojFacts(new FileInfo(Path.Combine(repository.FullName, relativePath)));
            csprojFacts[relativePath] = facts;
            if (facts.UseMaui || facts.AndroidTargetFrameworks.Count > 0)
            {
                mauiProjects.Add(
                    new(relativePath, facts.TargetFrameworks, facts.AndroidTargetFrameworks));
            }
        }

        var packageRoots = new List<PackageRootFacts>();
        foreach (var relativePath in SourcePackageManifestPaths(repository, filePaths)
                     .OrderBy(path => path, StringComparer.Ordinal))
        {
            var root = PackageRoot(relativePath);
            var packagePath = Path.Combine(repository.FullName, relativePath);
            var package = ReadPackageJson(packagePath);
            var manager = PackageManagerForRoot(filePathSet, root);
            var rootLower = root.ToLowerInvariant();
            var dependencyNames = new HashSet<string>(
                package.Dependencies.Concat(package.DevDependencies),
                StringComparer.Ordinal);
            var scriptNames = package.Scripts
                .OrderBy(name => name, StringComparer.Ordinal)
                .ToArray();

            var isWeb =
                rootLower is "." or "web" or "frontend"
                || rootLower.Contains("web", StringComparison.Ordinal)
                || rootLower.Contains("frontend", StringComparison.Ordinal)
                || dependencyNames.Contains("vite")
                || dependencyNames.Contains("react");

            var hasGenerate = scriptNames.Any(
                name => name.Contains("generate", StringComparison.OrdinalIgnoreCase));
            var hasApiClientGenerate = hasGenerate
                && (rootLower.Contains("client", StringComparison.Ordinal)
                    || rootLower.Contains("api", StringComparison.Ordinal)
                    || dependencyNames.Any(
                        dependency =>
                            dependency.Contains("openapi", StringComparison.OrdinalIgnoreCase)
                            || dependency.Contains("swagger", StringComparison.OrdinalIgnoreCase))
                    || scriptNames.Any(
                        name =>
                            name.Contains("client", StringComparison.OrdinalIgnoreCase)
                            || name.Contains("api", StringComparison.OrdinalIgnoreCase)));

            packageRoots.Add(
                new(
                    relativePath,
                    root,
                    manager.Name,
                    manager.InstallCommand,
                    scriptNames,
                    isWeb,
                    hasApiClientGenerate));
        }

        var apiClientHints = filePaths
            .Where(path =>
                path.Contains("api-client", StringComparison.OrdinalIgnoreCase)
                || path.Contains("apiclient", StringComparison.OrdinalIgnoreCase)
                || path.Contains("openapi", StringComparison.OrdinalIgnoreCase)
                || path.Contains("swagger", StringComparison.OrdinalIgnoreCase))
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToArray();

        var areaFileCounts = AreaReaderSettings.SupportedAreas.ToDictionary(
            area => area,
            area => files.Count(item => item.Areas.Contains(area, StringComparer.Ordinal)),
            StringComparer.Ordinal);

        return new(
            repository.FullName,
            areas,
            routing,
            files.Count,
            areaFileCounts,
            solutions,
            solutionFilters,
            dotnetProjects,
            csprojFacts,
            mauiProjects,
            mauiHelperScripts,
            packageRoots,
            packageRoots.Where(item => item.IsWeb).ToArray(),
            packageRoots.Where(item => item.HasApiClientGenerate).ToArray(),
            apiClientHints,
            workflows,
            markdownFiles.Length,
            markdownFiles);
    }

    public static bool IsGeneratedRelativePath(string relativePath)
    {
        var generated = new HashSet<string>(
            AreaReaderSettings.GeneratedDirectories,
            StringComparer.OrdinalIgnoreCase);
        return NormalizeRelativePath(relativePath)
            .Split('/', StringSplitOptions.RemoveEmptyEntries)
            .Any(generated.Contains);
    }

    private static IEnumerable<FileInfo> EnumerateCandidateFiles(DirectoryInfo repository)
    {
        foreach (var file in EnumerateCandidateFilesRecursive(repository))
        {
            yield return file;
        }
    }

    private static IEnumerable<FileInfo> EnumerateCandidateFilesRecursive(DirectoryInfo directory)
    {
        foreach (var file in directory.EnumerateFiles()
                     .OrderBy(file => file.Name, StringComparer.Ordinal))
        {
            if (IsIncludedFile(file))
            {
                yield return file;
            }
        }

        foreach (var child in directory.EnumerateDirectories()
                     .Where(child => !AreaReaderSettings.ExcludedDirectories.Contains(child.Name))
                     .OrderBy(child => child.Name, StringComparer.Ordinal))
        {
            if ((child.Attributes & FileAttributes.ReparsePoint) != 0)
            {
                continue;
            }

            foreach (var file in EnumerateCandidateFilesRecursive(child))
            {
                yield return file;
            }
        }
    }

    private static IReadOnlySet<string> SourcePackageManifestPaths(
        DirectoryInfo repository,
        IEnumerable<string> filePaths)
    {
        var candidates = filePaths
            .Where(path =>
                path.EndsWith("package.json", StringComparison.Ordinal)
                && !IsGeneratedRelativePath(path))
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToArray();
        if (candidates.Length == 0)
        {
            return new HashSet<string>(StringComparer.Ordinal);
        }

        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = "git",
                WorkingDirectory = repository.FullName,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
            };
            foreach (var argument in new[] { "ls-files", "--cached", "--others", "--exclude-standard", "--" })
            {
                startInfo.ArgumentList.Add(argument);
            }

            foreach (var candidate in candidates)
            {
                startInfo.ArgumentList.Add(candidate);
            }

            using var process = Process.Start(startInfo);
            if (process is null)
            {
                return new HashSet<string>(candidates, StringComparer.Ordinal);
            }

            var output = process.StandardOutput.ReadToEnd();
            process.WaitForExit();
            if (process.ExitCode != 0)
            {
                return new HashSet<string>(candidates, StringComparer.Ordinal);
            }

            return new HashSet<string>(
                output.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries)
                    .Select(line => NormalizeRelativePath(line.Trim()))
                    .Where(line => line.Length > 0),
                StringComparer.Ordinal);
        }
        catch (InvalidOperationException)
        {
            return new HashSet<string>(candidates, StringComparer.Ordinal);
        }
        catch (System.ComponentModel.Win32Exception)
        {
            return new HashSet<string>(candidates, StringComparer.Ordinal);
        }
    }

    private static string PackageRoot(string packageJsonPath)
    {
        var normalized = NormalizeRelativePath(packageJsonPath);
        var separator = normalized.LastIndexOf('/');
        return separator < 0 ? "." : normalized[..separator];
    }

    private static PackageManager PackageManagerForRoot(
        IReadOnlySet<string> filePaths,
        string root)
    {
        var prefix = root == "." ? string.Empty : root + "/";
        var lockfiles = new (string File, string Name, string[] Install)[]
        {
            ("package-lock.json", "npm", ["npm", "ci"]),
            ("pnpm-lock.yaml", "pnpm", ["pnpm", "install", "--frozen-lockfile"]),
            ("yarn.lock", "yarn", ["yarn", "install", "--frozen-lockfile"]),
            ("bun.lockb", "bun", ["bun", "install", "--frozen-lockfile"]),
            ("bun.lock", "bun", ["bun", "install", "--frozen-lockfile"]),
        };

        foreach (var item in lockfiles)
        {
            if (filePaths.Contains(prefix + item.File))
            {
                return new(item.Name, item.Install);
            }
        }

        return new("npm", ["npm", "install"]);
    }

    private static PackageJsonFacts ReadPackageJson(string path)
    {
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            if (document.RootElement.ValueKind != JsonValueKind.Object)
            {
                return PackageJsonFacts.Empty;
            }

            return new(
                ObjectPropertyNames(document.RootElement, "scripts"),
                ObjectPropertyNames(document.RootElement, "dependencies"),
                ObjectPropertyNames(document.RootElement, "devDependencies"));
        }
        catch (IOException)
        {
            return PackageJsonFacts.Empty;
        }
        catch (UnauthorizedAccessException)
        {
            return PackageJsonFacts.Empty;
        }
        catch (JsonException)
        {
            return PackageJsonFacts.Empty;
        }
    }

    private static IReadOnlyList<string> ObjectPropertyNames(JsonElement root, string name)
    {
        if (!root.TryGetProperty(name, out var property)
            || property.ValueKind != JsonValueKind.Object)
        {
            return [];
        }

        return property.EnumerateObject().Select(item => item.Name).ToArray();
    }

    private static bool MatchesAny(string pathText, IEnumerable<string> patterns) =>
        patterns.Any(
            pattern => FileSystemName.MatchesSimpleExpression(
                pattern,
                pathText,
                ignoreCase: false));

    private static int CompareRepoFiles(RepoFile left, RepoFile right)
    {
        var priority = (left.Priority ? 0 : 1).CompareTo(right.Priority ? 0 : 1);
        return priority != 0
            ? priority
            : StringComparer.Ordinal.Compare(left.Path, right.Path);
    }

    private static CsprojFacts EmptyCsprojFacts() => new(false, [], []);

    private static string NormalizeRelativePath(string path) => path.Replace('\\', '/');

    private sealed record PackageManager(string Name, IReadOnlyList<string> InstallCommand);

    private sealed record PackageJsonFacts(
        IReadOnlyList<string> Scripts,
        IReadOnlyList<string> Dependencies,
        IReadOnlyList<string> DevDependencies)
    {
        public static readonly PackageJsonFacts Empty = new([], [], []);
    }
}
