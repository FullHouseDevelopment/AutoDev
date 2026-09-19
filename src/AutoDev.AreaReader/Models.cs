using System.Text.Json.Serialization;

namespace AutoDev.AreaReader;

public sealed record AreaHint(
    IReadOnlyList<string> Keywords,
    IReadOnlyList<string> PathPatterns);

public sealed record RepoFile(
    string Path,
    long Bytes,
    bool Priority,
    IReadOnlyList<string> Areas);

public sealed record SkippedLargeFile(string Path, long Bytes);

public sealed record SkippedUnreadableFile(string Path, string Reason);

public sealed record RepoFileCollection(
    IReadOnlyList<RepoFile> Files,
    IReadOnlyList<SkippedLargeFile> SkippedLargeFiles,
    IReadOnlyList<SkippedUnreadableFile> SkippedUnreadableFiles);

public sealed record RoutingMetadata(
    string Mode,
    [property: JsonPropertyName("matched_keywords")]
    IReadOnlyDictionary<string, IReadOnlyList<string>> MatchedKeywords,
    bool Defaulted);

public sealed record RoutingResult(
    IReadOnlyList<string> Areas,
    RoutingMetadata Metadata);

public sealed record CsprojFacts(
    [property: JsonPropertyName("use_maui")]
    bool UseMaui,
    [property: JsonPropertyName("target_frameworks")]
    IReadOnlyList<string> TargetFrameworks,
    [property: JsonPropertyName("android_target_frameworks")]
    IReadOnlyList<string> AndroidTargetFrameworks);

public sealed record MauiProjectFacts(
    string Path,
    [property: JsonPropertyName("target_frameworks")]
    IReadOnlyList<string> TargetFrameworks,
    [property: JsonPropertyName("android_target_frameworks")]
    IReadOnlyList<string> AndroidTargetFrameworks);

public sealed record PackageRootFacts(
    string Path,
    string Root,
    [property: JsonPropertyName("package_manager")]
    string PackageManager,
    [property: JsonPropertyName("install_command")]
    IReadOnlyList<string> InstallCommand,
    IReadOnlyList<string> Scripts,
    [property: JsonPropertyName("is_web")]
    bool IsWeb,
    [property: JsonPropertyName("has_api_client_generate")]
    bool HasApiClientGenerate);

public sealed record RepositoryFacts(
    string Repo,
    [property: JsonPropertyName("routed_areas")]
    IReadOnlyList<string> RoutedAreas,
    RoutingMetadata Routing,
    [property: JsonPropertyName("file_count")]
    int FileCount,
    [property: JsonPropertyName("area_file_counts")]
    IReadOnlyDictionary<string, int> AreaFileCounts,
    IReadOnlyList<string> Solutions,
    [property: JsonPropertyName("solution_filters")]
    IReadOnlyList<string> SolutionFilters,
    [property: JsonPropertyName("dotnet_projects")]
    IReadOnlyList<string> DotnetProjects,
    [property: JsonPropertyName("csproj_facts")]
    IReadOnlyDictionary<string, CsprojFacts> CsprojFacts,
    [property: JsonPropertyName("maui_projects")]
    IReadOnlyList<MauiProjectFacts> MauiProjects,
    [property: JsonPropertyName("maui_helper_scripts")]
    IReadOnlyList<string> MauiHelperScripts,
    [property: JsonPropertyName("package_roots")]
    IReadOnlyList<PackageRootFacts> PackageRoots,
    [property: JsonPropertyName("web_package_roots")]
    IReadOnlyList<PackageRootFacts> WebPackageRoots,
    [property: JsonPropertyName("api_client_package_roots")]
    IReadOnlyList<PackageRootFacts> ApiClientPackageRoots,
    [property: JsonPropertyName("api_client_hints")]
    IReadOnlyList<string> ApiClientHints,
    [property: JsonPropertyName("workflow_files")]
    IReadOnlyList<string> WorkflowFiles,
    [property: JsonPropertyName("markdown_file_count")]
    int MarkdownFileCount,
    [property: JsonPropertyName("markdown_files")]
    IReadOnlyList<string> MarkdownFiles);

public sealed record AreaBundleMetadata(
    string Area,
    [property: JsonPropertyName("max_chars")]
    int MaxChars,
    [property: JsonPropertyName("bundle_chars")]
    int BundleChars,
    [property: JsonPropertyName("candidate_file_count")]
    int CandidateFileCount,
    [property: JsonPropertyName("included_file_count")]
    int IncludedFileCount,
    [property: JsonPropertyName("included_files")]
    IReadOnlyList<string> IncludedFiles,
    [property: JsonPropertyName("skipped_unreadable_files")]
    IReadOnlyList<SkippedUnreadableFile> SkippedUnreadableFiles,
    bool Truncated,
    [property: JsonPropertyName("placeholder_or_absent")]
    bool PlaceholderOrAbsent);

public sealed record AreaBundleResult(
    string Bundle,
    AreaBundleMetadata Metadata,
    [property: JsonPropertyName("file_map_text")]
    string FileMapText);

public sealed record VerificationCommand(
    string Label,
    string Cwd,
    IReadOnlyList<string> Argv,
    bool Optional = false);

public sealed record CommandGroup(
    string Name,
    string Description,
    bool Recommended,
    string Reason,
    bool Manual,
    IReadOnlyList<VerificationCommand> Commands);

public sealed record RecommendationMetadata(
    [property: JsonPropertyName("available_command_groups")]
    IReadOnlyList<string> AvailableCommandGroups,
    [property: JsonPropertyName("recommended_command_groups")]
    IReadOnlyList<string> RecommendedCommandGroups,
    [property: JsonPropertyName("conditional_command_groups")]
    IReadOnlyDictionary<string, string> ConditionalCommandGroups);

public sealed record AreaReaderResult(
    string Area,
    AreaBundleMetadata Metadata,
    string Brief);
