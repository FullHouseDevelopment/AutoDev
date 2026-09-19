namespace AutoDev.AreaReader;

public static class AreaRouting
{
    public static RoutingResult RouteAreas(string issue, string areasArgument)
    {
        var requested = areasArgument.Trim();
        if (requested == "all")
        {
            return new(
                [.. AreaReaderSettings.SupportedAreas],
                new("all", EmptyKeywordMap(), false));
        }

        if (requested != "auto")
        {
            var areas = new List<string>();
            foreach (var rawArea in requested.Split(','))
            {
                var area = rawArea.Trim();
                if (area.Length == 0)
                {
                    continue;
                }

                if (!AreaReaderSettings.SupportedAreas.Contains(area, StringComparer.Ordinal))
                {
                    throw new ArgumentException($"Unsupported area: {area}", nameof(areasArgument));
                }

                if (!areas.Contains(area, StringComparer.Ordinal))
                {
                    areas.Add(area);
                }
            }

            if (areas.Count == 0)
            {
                throw new ArgumentException(
                    "--areas explicit list did not include any supported areas",
                    nameof(areasArgument));
            }

            return new(areas, new("explicit", EmptyKeywordMap(), false));
        }

        var issueLower = issue.ToLowerInvariant();
        var matched = new Dictionary<string, IReadOnlyList<string>>(StringComparer.Ordinal);
        var routedAreas = new List<string>();
        foreach (var area in AreaReaderSettings.SupportedAreas)
        {
            var keywords = AreaReaderSettings.AreaHints[area].Keywords
                .Where(keyword => issueLower.Contains(keyword, StringComparison.Ordinal))
                .ToArray();
            if (keywords.Length == 0)
            {
                continue;
            }

            routedAreas.Add(area);
            matched[area] = keywords;
        }

        var defaulted = routedAreas.Count == 0;
        if (defaulted)
        {
            routedAreas.AddRange(AreaReaderSettings.DefaultAutoAreas);
        }

        return new(routedAreas, new("auto", matched, defaulted));
    }

    public static IReadOnlyList<RepoFile> AreaFileMap(IEnumerable<RepoFile> files, string area) =>
        files
            .Where(item => item.Areas.Contains(area, StringComparer.Ordinal) || item.Priority)
            .OrderBy(item => item.Areas.Contains(area, StringComparer.Ordinal) ? 0 : 1)
            .ThenBy(item => item.Priority ? 0 : 1)
            .ThenBy(item => item.Path, StringComparer.Ordinal)
            .ToArray();

    public static string FormatAreaFileMap(string area, IEnumerable<RepoFile> files)
    {
        var selected = files.ToArray();
        var lines = new List<string> { $"Area file map: {area}" };
        if (selected.Length == 0)
        {
            lines.Add("- No candidate files matched this area.");
        }

        foreach (var item in selected)
        {
            var flags = new List<string>();
            if (item.Areas.Contains(area, StringComparer.Ordinal))
            {
                flags.Add("area-match");
            }

            if (item.Priority)
            {
                flags.Add("priority");
            }

            var suffix = flags.Count == 0 ? string.Empty : $" [{string.Join("; ", flags)}]";
            lines.Add($"- {item.Path} ({item.Bytes} bytes){suffix}");
        }

        return string.Join('\n', lines) + "\n";
    }

    private static IReadOnlyDictionary<string, IReadOnlyList<string>> EmptyKeywordMap() =>
        new Dictionary<string, IReadOnlyList<string>>(StringComparer.Ordinal);
}
