using System.Diagnostics;
using System.Text;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace AutoDev.CompatibilityTests;

[TestClass]
public sealed class DifferentialCompatibilityTests
{
    [TestMethod]
    public async Task DifferentialHarness_NormalizesEquivalentProcessObservations()
    {
        var repositoryRoot = RepositoryRoot.Find();
        var reference = new CommandSpec(
            PythonExecutable(),
            ["-c", "print('migration-scaffold-ready')"],
            repositoryRoot);
        var candidate = CandidateHost(repositoryRoot, "migration-probe");

        var result = await DifferentialScenario.ObserveAsync(reference, candidate, repositoryRoot);

        Assert.IsTrue(result.Equivalent, result.DescribeMismatch());
    }

    [TestMethod]
    public async Task DifferentialHarness_CanObservePythonReferenceCliAndCandidateHost()
    {
        var repositoryRoot = RepositoryRoot.Find();
        var reference = new CommandSpec(
            PythonExecutable(),
            ["-m", "automation.autodev_cli", "--version"],
            repositoryRoot);
        var candidate = CandidateHost(repositoryRoot, "migration-probe");

        var result = await DifferentialScenario.ObserveAsync(reference, candidate, repositoryRoot);

        Assert.AreEqual(0, result.Reference.ExitCode);
        Assert.AreEqual(0, result.Candidate.ExitCode);
        StringAssert.Contains(result.Reference.StdOut, "autodev");
        StringAssert.Contains(result.Candidate.StdOut, "migration-scaffold-ready");
    }

    [TestMethod]
    public async Task CandidateHost_DoesNotExposeProductionCommandsYet()
    {
        var repositoryRoot = RepositoryRoot.Find();
        var observation = await ProcessObserver.ObserveAsync(
            CandidateHost(repositoryRoot),
            repositoryRoot);

        Assert.AreEqual(2, observation.ExitCode);
        StringAssert.Contains(observation.StdErr, "no product commands have been migrated");
    }

    private static CommandSpec CandidateHost(string repositoryRoot, params string[] arguments)
    {
        var assemblyPath = Path.Combine(AppContext.BaseDirectory, "AutoDev.Cli.dll");
        Assert.IsTrue(File.Exists(assemblyPath), $"Expected candidate host at {assemblyPath}");
        return new CommandSpec("dotnet", [assemblyPath, .. arguments], repositoryRoot);
    }

    private static string PythonExecutable() =>
        Environment.GetEnvironmentVariable("AUTODEV_COMPAT_PYTHON")
        ?? (OperatingSystem.IsWindows() ? "python" : "python3");
}

internal sealed record CommandSpec(
    string FileName,
    IReadOnlyList<string> Arguments,
    string WorkingDirectory);

internal sealed record CompatibilityObservation(
    int ExitCode,
    string StdOut,
    string StdErr,
    IReadOnlyDictionary<string, string> CapturedFiles);

internal sealed record DifferentialObservation(
    CompatibilityObservation Reference,
    CompatibilityObservation Candidate)
{
    public bool Equivalent =>
        Reference.ExitCode == Candidate.ExitCode
        && StringComparer.Ordinal.Equals(Reference.StdOut, Candidate.StdOut)
        && StringComparer.Ordinal.Equals(Reference.StdErr, Candidate.StdErr)
        && Reference.CapturedFiles.Count == Candidate.CapturedFiles.Count
        && Reference.CapturedFiles.All(pair =>
            Candidate.CapturedFiles.TryGetValue(pair.Key, out var value)
            && StringComparer.Ordinal.Equals(pair.Value, value));

    public string DescribeMismatch() =>
        $"Reference: exit={Reference.ExitCode}, stdout=[{Reference.StdOut}], stderr=[{Reference.StdErr}]; "
        + $"Candidate: exit={Candidate.ExitCode}, stdout=[{Candidate.StdOut}], stderr=[{Candidate.StdErr}]";
}

internal static class DifferentialScenario
{
    public static async Task<DifferentialObservation> ObserveAsync(
        CommandSpec reference,
        CommandSpec candidate,
        string workspaceRoot,
        IReadOnlyList<string>? capturedFiles = null,
        CancellationToken cancellationToken = default)
    {
        var referenceObservation = await ProcessObserver.ObserveAsync(
            reference,
            workspaceRoot,
            capturedFiles,
            cancellationToken);
        var candidateObservation = await ProcessObserver.ObserveAsync(
            candidate,
            workspaceRoot,
            capturedFiles,
            cancellationToken);

        return new DifferentialObservation(referenceObservation, candidateObservation);
    }
}

internal static class ProcessObserver
{
    private static readonly TimeSpan DefaultTimeout = TimeSpan.FromSeconds(30);

    public static async Task<CompatibilityObservation> ObserveAsync(
        CommandSpec command,
        string workspaceRoot,
        IReadOnlyList<string>? capturedFiles = null,
        CancellationToken cancellationToken = default)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = command.FileName,
            WorkingDirectory = command.WorkingDirectory,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        foreach (var argument in command.Arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = new Process { StartInfo = startInfo };
        if (!process.Start())
        {
            throw new InvalidOperationException($"Failed to start {command.FileName}.");
        }

        var stdoutTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(DefaultTimeout);
        try
        {
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
            throw new TimeoutException($"Process {command.FileName} exceeded {DefaultTimeout}.");
        }

        var stdout = await stdoutTask;
        var stderr = await stderrTask;
        var files = CaptureFiles(workspaceRoot, capturedFiles ?? []);
        return ObservationNormalizer.Normalize(
            new CompatibilityObservation(process.ExitCode, stdout, stderr, files),
            workspaceRoot);
    }

    private static IReadOnlyDictionary<string, string> CaptureFiles(
        string workspaceRoot,
        IReadOnlyList<string> relativePaths)
    {
        var root = Path.GetFullPath(workspaceRoot);
        var comparison = OperatingSystem.IsWindows()
            ? StringComparison.OrdinalIgnoreCase
            : StringComparison.Ordinal;
        var result = new SortedDictionary<string, string>(StringComparer.Ordinal);

        foreach (var relativePath in relativePaths)
        {
            var fullPath = Path.GetFullPath(Path.Combine(root, relativePath));
            var rootPrefix = root.EndsWith(Path.DirectorySeparatorChar)
                ? root
                : root + Path.DirectorySeparatorChar;
            if (!fullPath.StartsWith(rootPrefix, comparison))
            {
                throw new InvalidOperationException($"Captured path escapes workspace: {relativePath}");
            }

            if (File.Exists(fullPath))
            {
                result[relativePath.Replace('\\', '/')] = File.ReadAllText(fullPath, Encoding.UTF8);
            }
        }

        return result;
    }
}

internal static class ObservationNormalizer
{
    public static CompatibilityObservation Normalize(
        CompatibilityObservation observation,
        string workspaceRoot)
    {
        var files = observation.CapturedFiles.ToDictionary(
            pair => pair.Key.Replace('\\', '/'),
            pair => NormalizeText(pair.Value, workspaceRoot),
            StringComparer.Ordinal);

        return observation with
        {
            StdOut = NormalizeText(observation.StdOut, workspaceRoot),
            StdErr = NormalizeText(observation.StdErr, workspaceRoot),
            CapturedFiles = files,
        };
    }

    private static string NormalizeText(string value, string workspaceRoot)
    {
        var normalized = value.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n');
        var nativeRoot = Path.GetFullPath(workspaceRoot);
        var comparison = OperatingSystem.IsWindows()
            ? StringComparison.OrdinalIgnoreCase
            : StringComparison.Ordinal;
        normalized = normalized.Replace(nativeRoot, "<WORKSPACE>", comparison);

        var slashRoot = nativeRoot.Replace('\\', '/');
        if (!StringComparer.Ordinal.Equals(nativeRoot, slashRoot))
        {
            normalized = normalized.Replace(slashRoot, "<WORKSPACE>", comparison);
        }

        return normalized;
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
