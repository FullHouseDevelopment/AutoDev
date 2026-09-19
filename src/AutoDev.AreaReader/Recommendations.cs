namespace AutoDev.AreaReader;

public static class CommandGroupRecommendations
{
    public static readonly IReadOnlyList<string> DefaultRecommendedCommandGroups =
        ["env", "dotnet-solution", "node-root", "markdown-smoke"];

    public static readonly IReadOnlyDictionary<string, string> ConditionalCommandGroups =
        new Dictionary<string, string>(StringComparer.Ordinal)
        {
            ["api-client-generate"] = "Run if API/OpenAPI/client generation is touched.",
            ["web-app"] = "Run for web-specific issues or if root scripts are insufficient.",
            ["maui-android-doctor"] = "Run for MAUI/mobile issues or explicit mobile verification.",
            ["maui-android-build"] = "Run for MAUI/mobile issues when Android SDK is available.",
            ["ci-manual-reference"] = "Reference only; do not run by default.",
        };

    public static readonly IReadOnlyList<string> AllCommandGroups =
        [.. DefaultRecommendedCommandGroups, .. ConditionalCommandGroups.Keys];

    private static readonly string[] ApiClientTerms =
    [
        "api client generation", "openapi", "swagger", "generated client",
        "api contract", "api contracts", "typescript client",
    ];

    private static readonly string[] WebTerms =
        ["react", "vite", "frontend", "browser", "component", "web shell"];

    private static readonly string[] MauiContextTerms = ["maui", "mobile", "android"];

    private static readonly string[] MauiWorkIntentTerms =
    [
        "build", "run", "debug", "fix", "modify", "update", "verify", "validate",
        "test", "tooling", "device", "emulator", "xaml",
    ];

    private static readonly string[] MauiExplicitWorkTerms =
    [
        "android device", "android emulator", "emulator", "mobile build", "mobile run",
        "mobile tooling", "mobile ui", "mobile verification", "mobile-specific behavior", "xaml",
    ];

    private static readonly string[] MauiInventoryPhrases =
    [
        "including backend, web, maui/mobile/desktop if present",
        "backend, web, maui/mobile/desktop if present",
        "maui/mobile/desktop if present",
    ];

    private static readonly string[] ApiContractPathMarkers =
        ["api-client", "apiclient", "openapi", "swagger", "generated"];

    private static readonly string[] DocsOnlyTerms =
        ["documentation-only", "docs-only", "documentation only", "docs only"];

    private static readonly string[] DocsScopeTerms =
        ["document ", "documentation", "readme", "architecture", "architectural boundary"];

    private static readonly string[] DocPathSuffixes =
        [".md", ".mdx", ".rst", ".adoc", ".txt"];

    private static readonly string[] BackendDocMarkers =
        ["/api/", "apps/api/", "backend", "contract", "contracts"];

    public static RecommendationMetadata Recommend(
        string issueText,
        IEnumerable<string> changedPaths,
        string webBuildErrors = "",
        bool rootNodeScriptsSufficient = true,
        bool androidSdkAvailable = false,
        bool userRequestedMobileVerification = false,
        IEnumerable<string>? availableCommandGroups = null)
    {
        var normalizedIssue = issueText.ToLowerInvariant();
        var normalizedWebErrors = webBuildErrors.ToLowerInvariant();
        var normalizedPaths = changedPaths
            .Select(path => NormalizePath(path).ToLowerInvariant())
            .ToArray();
        var available = availableCommandGroups?.ToArray() ?? [.. AllCommandGroups];
        var availableSet = new HashSet<string>(available, StringComparer.Ordinal);

        if (IsDocumentationOnlyScope(normalizedIssue, normalizedPaths))
        {
            var documentationRecommended = DocumentationOnlyCommandGroups(
                normalizedPaths,
                availableSet);
            return BuildMetadata(available, documentationRecommended, availableSet);
        }

        var recommended = DefaultRecommendedCommandGroups
            .Where(availableSet.Contains)
            .ToList();

        if (ApiClientRelevant(normalizedIssue, normalizedWebErrors, normalizedPaths))
        {
            recommended.Add("api-client-generate");
        }

        if (WebRelevant(normalizedIssue, normalizedPaths, rootNodeScriptsSufficient))
        {
            recommended.Add("web-app");
        }

        var mauiRelevant = MauiRelevant(
            normalizedIssue,
            normalizedPaths,
            userRequestedMobileVerification);
        if (mauiRelevant)
        {
            recommended.Add("maui-android-doctor");
        }

        if (mauiRelevant && androidSdkAvailable)
        {
            recommended.Add("maui-android-build");
        }

        return BuildMetadata(available, recommended, availableSet);
    }

    public static bool IsDocumentationOnlyScope(
        string normalizedIssueText,
        IEnumerable<string> changedPaths)
    {
        var paths = changedPaths.Select(path => NormalizePath(path).ToLowerInvariant()).ToArray();
        if (paths.Length > 0)
        {
            return paths.All(IsDocumentationPath);
        }

        if (ContainsAny(normalizedIssueText, DocsOnlyTerms))
        {
            return true;
        }

        if (normalizedIssueText.Contains("do not implement", StringComparison.Ordinal)
            && ContainsAny(normalizedIssueText, DocsScopeTerms))
        {
            return true;
        }

        return normalizedIssueText.Contains("document ", StringComparison.Ordinal)
            && !ContainsAny(normalizedIssueText, ["implement ", "fix ", "build ", "test "]);
    }

    private static RecommendationMetadata BuildMetadata(
        IReadOnlyList<string> available,
        IEnumerable<string> recommended,
        HashSet<string> availableSet)
    {
        var conditional = ConditionalCommandGroups
            .Where(pair => availableSet.Contains(pair.Key))
            .ToDictionary(pair => pair.Key, pair => pair.Value, StringComparer.Ordinal);

        return new(
            available,
            FilterUnique(recommended, availableSet),
            conditional);
    }

    private static string[] DocumentationOnlyCommandGroups(
        IEnumerable<string> changedPaths,
        HashSet<string> availableSet)
    {
        var paths = changedPaths.Select(path => NormalizePath(path).ToLowerInvariant()).ToArray();
        var groups = new List<string> { "env" };
        if (paths.Length > 0 && paths.Any(IsBackendOwnedDocPath))
        {
            groups.Add("dotnet-solution");
        }

        groups.Add("markdown-smoke");
        return groups.Where(availableSet.Contains).ToArray();
    }

    private static bool ApiClientRelevant(
        string issueText,
        string webBuildErrors,
        IEnumerable<string> changedPaths) =>
        ContainsAny(issueText, ApiClientTerms)
        || ContainsAny(webBuildErrors, ["generated api client", "stale generated", "missing generated"])
        || changedPaths.Any(IsApiContractPath);

    private static bool WebRelevant(
        string issueText,
        IEnumerable<string> changedPaths,
        bool rootNodeScriptsSufficient) =>
        changedPaths.Any(path => path.StartsWith("phoodab/apps/web/", StringComparison.Ordinal))
        || ContainsAny(issueText, WebTerms)
        || !rootNodeScriptsSufficient;

    private static bool MauiRelevant(
        string issueText,
        IEnumerable<string> changedPaths,
        bool userRequestedMobileVerification)
    {
        if (userRequestedMobileVerification)
        {
            return true;
        }

        if (changedPaths.Any(
                path => path.StartsWith("phoodab/apps/mobile/", StringComparison.Ordinal)
                    || path.StartsWith("phoodab/apps/mobile-shared/", StringComparison.Ordinal)))
        {
            return true;
        }

        var scopedIssueText = RemoveMauiInventoryMentions(issueText);
        if (scopedIssueText.Contains("phoodab/apps/mobile", StringComparison.Ordinal)
            || scopedIssueText.Contains("phoodab/apps/mobile-shared", StringComparison.Ordinal))
        {
            return true;
        }

        return MauiTextRelevant(scopedIssueText);
    }

    private static bool IsDocumentationPath(string path) =>
        path.StartsWith("docs/", StringComparison.Ordinal)
        || path.Contains("/docs/", StringComparison.Ordinal)
        || DocPathSuffixes.Any(suffix => path.EndsWith(suffix, StringComparison.Ordinal));

    private static bool IsBackendOwnedDocPath(string path) =>
        IsDocumentationPath(path) && ContainsAny(path, BackendDocMarkers);

    private static bool IsApiContractPath(string path) =>
        path.StartsWith("phoodab/packages/api-client/", StringComparison.Ordinal)
        || ContainsAny(path, ApiContractPathMarkers);

    private static string RemoveMauiInventoryMentions(string value)
    {
        var result = value;
        foreach (var phrase in MauiInventoryPhrases)
        {
            result = result.Replace(phrase, string.Empty, StringComparison.Ordinal);
        }

        return result;
    }

    private static bool MauiTextRelevant(string issueText) =>
        ContainsAny(issueText, MauiExplicitWorkTerms)
        || (ContainsAny(issueText, MauiContextTerms) && ContainsAny(issueText, MauiWorkIntentTerms));

    private static bool ContainsAny(string value, IEnumerable<string> terms) =>
        terms.Any(term => value.Contains(term, StringComparison.Ordinal));

    private static List<string> FilterUnique(
        IEnumerable<string> values,
        IReadOnlySet<string> allowed)
    {
        var filtered = new List<string>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var value in values)
        {
            if (allowed.Contains(value) && seen.Add(value))
            {
                filtered.Add(value);
            }
        }

        return filtered;
    }

    private static string NormalizePath(string path) => path.Replace('\\', '/');
}
