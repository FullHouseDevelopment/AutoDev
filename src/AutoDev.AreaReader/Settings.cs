using System.Collections.ObjectModel;

namespace AutoDev.AreaReader;

public static class AreaReaderSettings
{
    public const int DefaultMaxCharsPerArea = 50_000;
    public const int MaxFileBytes = 250_000;

    public static readonly IReadOnlyList<string> PreferredSolutionFilterMarkers =
        ["no-gui", "nogui", "headless", "backend", "server", "api", "ci", "test"];

    public static readonly string MarkdownSmokeScript = NormalizeNewlines("""
mapfile -t markdown_files < <(git ls-files '*.md')
if ((${#markdown_files[@]} == 0)); then
  echo "No markdown files tracked; skipping markdown smoke check."
  exit 0
fi

if grep -nE $'	|[ 	]+$' "${markdown_files[@]}"; then
  echo "Markdown smoke check failed: tabs or trailing whitespace found." >&2
  exit 1
fi
""");

    public static readonly IReadOnlyList<string> SupportedAreas =
        ["backend", "web", "maui", "ci", "tests", "docs", "api-client"];

    public static readonly IReadOnlyList<string> DefaultAutoAreas =
        ["backend", "web", "maui", "ci"];

    public static readonly IReadOnlySet<string> IncludedSuffixes = new HashSet<string>(
        [
            ".cs", ".csproj", ".sln", ".slnf", ".xaml", ".xml", ".json", ".md",
            ".yml", ".yaml", ".props", ".targets", ".ts", ".tsx", ".js", ".jsx",
            ".css", ".html", ".sh", ".ps1", ".py", ".toml", ".lock",
        ],
        StringComparer.Ordinal);

    public static readonly IReadOnlySet<string> IncludedFilenames = new HashSet<string>(
        [
            "AGENTS.md", "README.md", "CONTRIBUTING.md", "Dockerfile", "Makefile",
            "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb",
            "Pipfile.lock", "poetry.lock", "Directory.Build.props",
            "Directory.Packages.props", "MauiProgram.cs", "App.xaml", "Program.cs",
        ],
        StringComparer.Ordinal);

    public static readonly IReadOnlySet<string> GeneratedDirectories = new HashSet<string>(
        [".next", ".nuxt", ".svelte-kit", ".turbo", "out"],
        StringComparer.Ordinal);

    public static readonly IReadOnlySet<string> ExcludedDirectories = new HashSet<string>(
        [
            ".git", ".vs", ".vscode", "bin", "obj", "node_modules", ".autodev-run",
            ".idea", ".cache", ".benchmark-results", "__pycache__", "TestResults",
            "dist", "build", "coverage", ".next", ".nuxt", ".svelte-kit", ".turbo", "out",
        ],
        StringComparer.Ordinal);

    public static readonly IReadOnlyList<string> PriorityPatterns =
        [
            "AGENTS.md", "README.md", ".github/workflows/*.yml", ".github/workflows/*.yaml",
            "*.sln", "*.slnf", "*.csproj", "package.json", "package-lock.json",
            "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "tsconfig*", "vite.config*",
            "MauiProgram.cs", "App.xaml", "Program.cs", "Directory.Build.props",
            "Directory.Packages.props", "docs/*", "doc/*", "adr/*", "ADRs/*",
        ];

    private static string NormalizeNewlines(string value) =>
        value.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n');

    public static readonly IReadOnlyDictionary<string, AreaHint> AreaHints =
        new ReadOnlyDictionary<string, AreaHint>(
            new Dictionary<string, AreaHint>(StringComparer.Ordinal)
            {
                ["backend"] = new(
                    ["backend", "server", "api", "database", "db", "ef", "migration"],
                    [
                        "*backend*", "*server*", "*api*", "*.sln", "*.slnf", "*.csproj",
                        "Program.cs", "Directory.Build.props", "Directory.Packages.props",
                    ]),
                ["web"] = new(
                    ["web", "frontend", "react", "vite", "typescript", "browser", "ui"],
                    [
                        "*web*", "*frontend*", "*react*", "package.json", "package-lock.json",
                        "pnpm-lock.yaml", "yarn.lock", "tsconfig*", "vite.config*", "*.ts",
                        "*.tsx", "*.js", "*.jsx", "*.css", "*.html",
                    ]),
                ["maui"] = new(
                    ["maui", "mobile", "desktop", "android", "ios", "xaml"],
                    ["*maui*", "*mobile*", "*android*", "*ios*", "*.xaml", "MauiProgram.cs", "App.xaml"]),
                ["ci"] = new(
                    ["ci", "workflow", "github actions", "build", "verify", "pipeline"],
                    [
                        ".github/workflows/*.yml", ".github/workflows/*.yaml", "*workflow*",
                        "*ci*", "*.sh", "*.ps1", "codex-profiles.json",
                    ]),
                ["tests"] = new(
                    ["test", "tests", "verification", "xunit", "pytest", "playwright"],
                    ["*test*", "*tests*", "*.Tests/*", "*.Test/*", "pytest.ini", "playwright.config*"]),
                ["docs"] = new(
                    ["docs", "documentation", "readme", "adr", "guide"],
                    ["README.md", "CONTRIBUTING.md", "docs/*", "doc/*", "adr/*", "ADRs/*", "*.md"]),
                ["api-client"] = new(
                    ["api client", "client", "sdk", "http client", "openapi"],
                    ["*api-client*", "*apiclient*", "*client*", "*sdk*", "*openapi*", "*swagger*", "*generated*"]),
            });
}
