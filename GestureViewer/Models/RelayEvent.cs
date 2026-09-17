using System.Text.Json;

namespace GestureViewer.Models;

/// <summary>
/// The schema-v2 events a subscribed <c>client</c> can receive. Parsing is
/// deliberately defensive: anything unrecognised comes back as a failure with
/// a reason rather than throwing into the receive loop.
/// </summary>
public abstract class RelayEvent
{
    public int SchemaVersion { get; init; }
    public string EventName { get; init; } = "";
    public string Source { get; init; } = "";
    public DateTimeOffset? DetectedAt { get; init; }
    public string DeviceId { get; init; } = "";
    public string Raw { get; init; } = "";

    /// <summary>A completed, confirmed gesture sequence. Carries no gate.</summary>
    public sealed class Gesture : RelayEvent
    {
        public IReadOnlyList<string> Sequence { get; init; } = Array.Empty<string>();
        public IReadOnlyList<double> Confidences { get; init; } = Array.Empty<double>();
        public string ModelVocab { get; init; } = "";
    }

    /// <summary><c>session_unlocked</c> or <c>session_locked</c>.</summary>
    public sealed class Session : RelayEvent
    {
        public bool IsUnlock { get; init; }
        public SessionGate Gate { get; init; }
        public string? Reason { get; init; }
    }

    public static bool TryParse(string json, out RelayEvent? evt, out string error)
    {
        evt = null;
        error = "";

        JsonDocument doc;
        try
        {
            doc = JsonDocument.Parse(json);
        }
        catch (JsonException ex)
        {
            error = "not valid JSON: " + ex.Message;
            return false;
        }

        using (doc)
        {
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                error = "top-level value is not an object";
                return false;
            }

            var eventName = ReadString(root, "event");
            if (string.IsNullOrWhiteSpace(eventName))
            {
                error = "missing 'event' field";
                return false;
            }

            var schemaVersion = ReadInt(root, "schema_version") ?? 0;
            var source = ReadString(root, "source") ?? "";
            var deviceId = ReadString(root, "device_id") ?? "";
            DateTimeOffset? detectedAt = null;
            if (root.TryGetProperty("detected_at", out var detected) &&
                detected.ValueKind == JsonValueKind.String &&
                DateTimeOffset.TryParse(detected.GetString(), out var parsed))
            {
                detectedAt = parsed;
            }

            switch (eventName)
            {
                case "gesture_sequence_detected":
                {
                    var sequence = ReadStringArray(root, "sequence");
                    if (sequence.Count == 0)
                    {
                        error = "gesture_sequence_detected with an empty or missing 'sequence'";
                        return false;
                    }

                    evt = new Gesture
                    {
                        SchemaVersion = schemaVersion,
                        EventName = eventName,
                        Source = source,
                        DetectedAt = detectedAt,
                        DeviceId = deviceId,
                        Raw = json,
                        Sequence = sequence,
                        Confidences = ReadDoubleArray(root, "confidences"),
                        ModelVocab = ReadString(root, "model_vocab") ?? ""
                    };
                    return true;
                }

                case "session_unlocked":
                case "session_locked":
                {
                    var isUnlock = eventName == "session_unlocked";
                    if (!SessionGateExtensions.TryParseGate(ReadString(root, "gate"), out var gate))
                    {
                        error = eventName + " with a missing or unrecognised 'gate'";
                        return false;
                    }

                    evt = new Session
                    {
                        SchemaVersion = schemaVersion,
                        EventName = eventName,
                        Source = source,
                        DetectedAt = detectedAt,
                        DeviceId = deviceId,
                        Raw = json,
                        IsUnlock = isUnlock,
                        Gate = gate,
                        Reason = isUnlock ? null : ReadString(root, "reason")
                    };
                    return true;
                }

                default:
                    error = "unrecognised event type '" + eventName + "'";
                    return false;
            }
        }
    }

    private static string? ReadString(JsonElement obj, string name) =>
        obj.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;

    private static int? ReadInt(JsonElement obj, string name) =>
        obj.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out var i)
            ? i
            : null;

    private static IReadOnlyList<string> ReadStringArray(JsonElement obj, string name)
    {
        if (!obj.TryGetProperty(name, out var v) || v.ValueKind != JsonValueKind.Array)
            return Array.Empty<string>();

        var list = new List<string>();
        foreach (var item in v.EnumerateArray())
        {
            if (item.ValueKind == JsonValueKind.String)
            {
                var s = item.GetString();
                if (!string.IsNullOrWhiteSpace(s)) list.Add(s);
            }
        }
        return list;
    }

    private static IReadOnlyList<double> ReadDoubleArray(JsonElement obj, string name)
    {
        if (!obj.TryGetProperty(name, out var v) || v.ValueKind != JsonValueKind.Array)
            return Array.Empty<double>();

        var list = new List<double>();
        foreach (var item in v.EnumerateArray())
        {
            if (item.ValueKind == JsonValueKind.Number && item.TryGetDouble(out var d))
                list.Add(d);
        }
        return list;
    }
}
