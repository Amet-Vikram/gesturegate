using System.Text.Json.Serialization;

namespace GestureViewer.Models;

/// <summary>One entry from <c>GET /devices</c>.</summary>
public sealed class DeviceInfo
{
    [JsonPropertyName("device_id")]
    public string DeviceId { get; set; } = "";

    [JsonPropertyName("connected_at")]
    public DateTimeOffset? ConnectedAt { get; set; }

    /// <summary>Shown in the device dropdown.</summary>
    public override string ToString() =>
        ConnectedAt is null
            ? DeviceId
            : $"{DeviceId}  (online since {ConnectedAt.Value.ToLocalTime():HH:mm:ss})";
}
