using System.Text;

namespace AutoDev.AreaReader;

public static class AreaContextBuilder
{
    public static AreaBundleResult BuildAreaBundle(
        DirectoryInfo repository,
        string area,
        string issue,
        string repositoryMap,
        IReadOnlyList<RepoFile> files,
        int maxChars)
    {
        if (maxChars <= 0)
        {
            throw new ArgumentOutOfRangeException(
                nameof(maxChars),
                "--max-chars-per-area must be greater than zero");
        }

        var fileMapText = AreaRouting.FormatAreaFileMap(area, files);
        var header =
            $"Issue:\n{issue}\n\n"
            + $"Routed area: {area}\n\n"
            + "Area hint keywords:\n"
            + $"{string.Join(", ", AreaReaderSettings.AreaHints[area].Keywords)}\n\n"
            + fileMapText
            + "\nRepository map:\n"
            + repositoryMap
            + "\nFile excerpts:\n";

        var parts = new List<string> { header };
        var remaining = maxChars - PythonLength(header);
        var includedFiles = new List<string>();
        var skippedUnreadableFiles = new List<SkippedUnreadableFile>();
        var truncated = remaining < 0;

        if (remaining > 0)
        {
            foreach (var item in files)
            {
                string content;
                try
                {
                    content = File.ReadAllText(
                        Path.Combine(repository.FullName, item.Path),
                        Encoding.UTF8);
                }
                catch (IOException exception)
                {
                    skippedUnreadableFiles.Add(new(item.Path, exception.Message));
                    continue;
                }
                catch (UnauthorizedAccessException exception)
                {
                    skippedUnreadableFiles.Add(new(item.Path, exception.Message));
                    continue;
                }

                var marker = $"\n\n===== FILE: {item.Path} =====\n";
                var entry = marker + content.TrimEnd() + "\n";
                var entryLength = PythonLength(entry);
                if (entryLength > remaining)
                {
                    if (remaining > PythonLength(marker))
                    {
                        parts.Add(TakePythonCharacters(entry, remaining));
                        includedFiles.Add(item.Path);
                    }

                    truncated = true;
                    break;
                }

                parts.Add(entry);
                includedFiles.Add(item.Path);
                remaining -= entryLength;
                if (remaining <= 0)
                {
                    truncated = true;
                    break;
                }
            }
        }

        var bundle = string.Concat(parts);
        var metadata = new AreaBundleMetadata(
            area,
            maxChars,
            PythonLength(bundle),
            files.Count,
            includedFiles.Count,
            includedFiles,
            skippedUnreadableFiles,
            truncated,
            !files.Any(item => item.Areas.Contains(area, StringComparer.Ordinal)));

        return new(bundle, metadata, fileMapText);
    }

    private static int PythonLength(string value) => value.EnumerateRunes().Count();

    private static string TakePythonCharacters(string value, int count)
    {
        if (count <= 0)
        {
            return string.Empty;
        }

        var builder = new StringBuilder();
        var remaining = count;
        foreach (var rune in value.EnumerateRunes())
        {
            if (remaining-- == 0)
            {
                break;
            }

            builder.Append(rune.ToString());
        }

        return builder.ToString();
    }
}
