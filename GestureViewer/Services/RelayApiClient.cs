using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.Json;
using GestureViewer.Models;

namespace GestureViewer.Services;

/// <summary>Talks to the relay's plain HTTP endpoints.</summary>
public sealed class RelayApiClient : IDisposable
{
    private readonly AppConfig _config;
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(10) };

    public RelayApiClient(AppConfig config) => _config = config;

    /// <summary>
    /// <c>GET /devices</c> — online edge devices this token is authorised to see.
    /// An empty list is a valid answer, not an error.
    /// </summary>
    public async Task<IReadOnlyList<DeviceInfo>> GetDevicesAsync(CancellationToken ct)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(_config.HttpBaseUri, "/devices"));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _config.Token);

        using var response = await _http.SendAsync(request, ct).ConfigureAwait(false);

        if (response.StatusCode == HttpStatusCode.Unauthorized)
            throw new RelayAuthException("The relay rejected the token (401). Check Token in appsettings.json.");

        response.EnsureSuccessStatusCode();

        var body = await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
        var devices = JsonSerializer.Deserialize<List<DeviceInfo>>(body);
        return devices ?? new List<DeviceInfo>();
    }

    public void Dispose() => _http.Dispose();
}

public sealed class RelayAuthException : Exception
{
    public RelayAuthException(string message) : base(message) { }
}
