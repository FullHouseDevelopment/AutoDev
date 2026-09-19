using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AutoDev.AreaReader;

public static class AreaPrompts
{
    public static string BuildAreaReaderPrompt(
        string issue,
        string area,
        string bundle,
        AreaBundleMetadata metadata) =>
        $"""
        You are the area reader model for area: {area}.

        You are not the coder. Do not edit files. Do not design a patch. Read only the provided repository context and produce a factual handoff brief for a later synthesis reader and coder.

        Your brief must:
        - Include exact file paths for every repository fact you mention.
        - Distinguish visible facts from inference.
        - Stay factual; do not invent shell commands or implementation steps.
        - Name local verification needs for this area conceptually, not as freehand command lines.
        - Identify uncertainties and missing files.
        - If this area is placeholder-only or not actually present, say so clearly.

        Original issue:
        {issue}

        Area bundle metadata:
        {PythonJson.Dumps(metadata)}

        Area input bundle:
        {bundle}
        """ + "\n";

    public static string BuildSynthesisPrompt(
        string issue,
        IReadOnlyList<string> areas,
        IReadOnlyList<AreaReaderResult> areaResults,
        RepositoryFacts detectedFacts,
        IReadOnlyList<CommandGroup> commandGroups)
    {
        var briefBlocks = new StringBuilder();
        foreach (var result in areaResults)
        {
            briefBlocks.Append("## Area: ");
            briefBlocks.Append(result.Area);
            briefBlocks.Append("\n\nReader metadata:\n");
            briefBlocks.Append(PythonJson.Dumps(result.Metadata));
            briefBlocks.Append("\n\nReader brief:\n");
            briefBlocks.Append(result.Brief);
            briefBlocks.Append('\n');
        }

        return $"""
        You are the synthesis reader model in an area-based local LLM benchmark.

        You are not the coder. Combine the area reader briefs into one compact coder handoff.

        Your handoff must:
        - Preserve area-specific details.
        - List routed areas.
        - List repo/application surfaces.
        - List relevant files by area.
        - Use the deterministic facts below as the source of truth for repository structure.
        - Refer to named verification command groups instead of inventing shell commands.
        - Include cross-area risks.
        - Include constraints and uncertainties.
        - Do not invent files or commands.

        Original issue:
        {issue}

        Routed areas:
        {string.Join(", ", areas)}

        Deterministic repository facts:
        {PythonJson.Dumps(detectedFacts)}

        Available verification command groups:
        {PythonJson.Dumps(commandGroups)}

        Area reader briefs:
        {briefBlocks}
        """ + "\n";
    }

    public static string BuildPlannerPrompt(
        string issue,
        string synthesisBrief,
        RepositoryFacts detectedFacts,
        RecommendationMetadata recommendedGroups,
        IReadOnlyList<CommandGroup> commandGroups) =>
        $"""
        You are the coder model in an area-based local LLM benchmark.

        Consume the original issue and the synthesized handoff. Produce a minimal issue-scoped implementation or verification plan.

        Rules:
        - For verification-only issues, list "files to inspect," not "files likely needing changes."
        - Name exact files only when supported by the handoff.
        - Select verification by named command group from the deterministic command group list.
        - Do not write freehand shell commands.
        - Do not use placeholder commands.
        - Do not invent test projects or paths.
        - Do not refactor unrelated code.
        - Be strict about uncertainty.

        Original issue:
        {issue}

        Synthesized handoff:
        {synthesisBrief}

        Deterministic repository facts:
        {PythonJson.Dumps(detectedFacts)}

        Recommended verification command groups:
        {PythonJson.Dumps(recommendedGroups)}

        All available verification command groups:
        {PythonJson.Dumps(commandGroups)}
        """ + "\n";
}

internal static class PythonJson
{
    private static readonly JsonSerializerOptions SerializerOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        Encoder = JavaScriptEncoder.Default,
    };

    public static string Dumps(object value)
    {
        var node = JsonSerializer.SerializeToNode(
            value,
            value.GetType(),
            SerializerOptions)
            ?? throw new InvalidOperationException("Cannot serialize null JSON node.");

        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(
                   stream,
                   new JsonWriterOptions
                   {
                       Indented = true,
                       Encoder = JavaScriptEncoder.Default,
                   }))
        {
            WriteSorted(writer, node);
        }

        return Encoding.UTF8.GetString(stream.ToArray());
    }

    private static void WriteSorted(Utf8JsonWriter writer, JsonNode node)
    {
        switch (node)
        {
            case JsonObject jsonObject:
                writer.WriteStartObject();
                foreach (var property in jsonObject.OrderBy(
                             pair => pair.Key,
                             StringComparer.Ordinal))
                {
                    writer.WritePropertyName(property.Key);
                    if (property.Value is null)
                    {
                        writer.WriteNullValue();
                    }
                    else
                    {
                        WriteSorted(writer, property.Value);
                    }
                }

                writer.WriteEndObject();
                break;

            case JsonArray jsonArray:
                writer.WriteStartArray();
                foreach (var item in jsonArray)
                {
                    if (item is null)
                    {
                        writer.WriteNullValue();
                    }
                    else
                    {
                        WriteSorted(writer, item);
                    }
                }

                writer.WriteEndArray();
                break;

            default:
                node.WriteTo(writer);
                break;
        }
    }
}
