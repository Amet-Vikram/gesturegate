using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace GestureViewer.Services;

/// <summary>
/// Plain-text <c>appsettings.json</c> next to the exe. This is a demo client,
/// so the token lives here too — no DPAPI, no credential store.
/// </summary>
public sealed class AppConfig
{
    public string RelayUrl { get; set; } = "ws://localhost:8080/ws/gesture";
    public string Token { get; set; } = "";
    public string DeviceId { get; set; } = "";
    public int MaxGestureRows { get; set; } = 200;

    [JsonIgnore]
    public static string FilePath => Path.Combine(AppContext.BaseDirectory, "appsettings.json");

    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNameCaseInsensitive = true,
        WriteIndented = true
    };

    /// <summary>Loads the config, falling back to defaults if it is missing or unreadable.</summary>
    public static AppConfig Load(out string? problem)
    {
        problem = null;
        try
        {
            if (!File.Exists(FilePath))
            {
                problem = "appsettings.json not found next to the exe — using defaults.";
                return new AppConfig();
            }

            var config = JsonSerializer.Deserialize<AppConfig>(File.ReadAllText(FilePath), Options);
            if (config is null)
            {
                problem = "appsettings.json is empty — using defaults.";
                return new AppConfig();
            }

            if (!IsUsableRelayUrl(config.RelayUrl))
            {
                var defaults = new AppConfig();
                problem = $"RelayUrl '{config.RelayUrl}' is not a ws:// or wss:// URL — falling back to {defaults.RelayUrl}.";
                config.RelayUrl = defaults.RelayUrl;
            }

            return config;
        }
        catch (Exception ex)
        {
            problem = "Could not read appsettings.json (" + ex.Message + ") — using defaults.";
            return new AppConfig();
        }
    }

    /// <summary>Best-effort save. Used to remember the last picked device.</summary>
    public bool TrySave(out string? problem)
    {
        problem = null;
        try
        {
            File.WriteAllText(FilePath, JsonSerializer.Serialize(this, Options));
            return true;
        }
        catch (Exception ex)
        {
            problem = ex.Message;
            return false;
        }
    }

    private static bool IsUsableRelayUrl(string? url) =>
        Uri.TryCreate(url, UriKind.Absolute, out var uri) &&
        (uri.Scheme.Equals("ws", StringComparison.OrdinalIgnoreCase) ||
         uri.Scheme.Equals("wss", StringComparison.OrdinalIgnoreCase));

    /// <summary>The relay WebSocket endpoint.</summary>
    public Uri WebSocketUri => new(RelayUrl);

    /// <summary>
    /// The HTTP base for <c>GET /devices</c>, derived from <see cref="RelayUrl"/>
    /// (<c>ws→http</c>, <c>wss→https</c>) so there is only one URL to configure.
    /// </summary>
    public Uri HttpBaseUri
    {
        get
        {
            var ws = WebSocketUri;
            var scheme = ws.Scheme.ToLowerInvariant() switch
            {
                "wss" => "https",
                "ws" => "http",
                var other => other
            };
            return new UriBuilder(scheme, ws.Host, ws.Port).Uri;
        }
    }
}
