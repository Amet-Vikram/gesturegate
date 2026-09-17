using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using GestureViewer.Models;

namespace GestureViewer.Services;

public enum ConnectionStatus
{
    Disconnected,
    Connecting,
    Connected,
    Reconnecting,
    Failed
}

/// <summary>
/// The WebSocket half of the client: connect with the bearer token on the
/// handshake, subscribe to one device topic, then read events until told to
/// stop. Every fresh connection starts with an empty subscription set, so the
/// subscribe frame is re-sent after each reconnect.
/// </summary>
public sealed class RelayClient
{
    private const int ReceiveBufferSize = 8 * 1024;

    private readonly AppConfig _config;
    private CancellationTokenSource? _cts;
    private Task? _worker;

    public RelayClient(AppConfig config) => _config = config;

    /// <summary>Status plus an optional detail line (an error message, usually).</summary>
    public event Action<ConnectionStatus, string?>? StatusChanged;

    /// <summary>A parsed, recognised event from the subscribed topic.</summary>
    public event Action<RelayEvent>? EventReceived;

    /// <summary>Free-text log lines for the activity panel.</summary>
    public event Action<string>? Logged;

    public bool IsRunning => _worker is { IsCompleted: false };

    /// <summary>Starts the connect/subscribe/receive loop for one topic.</summary>
    public void Start(string topic)
    {
        if (IsRunning) return;

        var cts = new CancellationTokenSource();
        _cts = cts;
        _worker = Task.Run(() => RunAsync(topic, cts.Token));
    }

    /// <summary>Stops the loop and waits for it to unwind.</summary>
    public async Task StopAsync()
    {
        if (_cts is null) return;

        _cts.Cancel();
        try
        {
            if (_worker is not null) await _worker.ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // expected
        }
        finally
        {
            _cts.Dispose();
            _cts = null;
            _worker = null;
        }
    }

    private async Task RunAsync(string topic, CancellationToken ct)
    {
        var attempt = 0;

        while (!ct.IsCancellationRequested)
        {
            try
            {
                StatusChanged?.Invoke(attempt == 0 ? ConnectionStatus.Connecting : ConnectionStatus.Reconnecting, null);

                using var socket = new ClientWebSocket();
                socket.Options.SetRequestHeader("Authorization", "Bearer " + _config.Token);
                await socket.ConnectAsync(_config.WebSocketUri, ct).ConfigureAwait(false);

                attempt = 0;
                StatusChanged?.Invoke(ConnectionStatus.Connected, null);
                Logged?.Invoke("Connected to " + _config.WebSocketUri);

                await SendActionAsync(socket, "subscribe", topic, ct).ConfigureAwait(false);
                Logged?.Invoke("Subscribed to topic '" + topic + "'.");

                await ReceiveLoopAsync(socket, ct).ConfigureAwait(false);

                if (!ct.IsCancellationRequested)
                    Logged?.Invoke("The relay closed the connection.");
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                var detail = Describe(ex);
                StatusChanged?.Invoke(ConnectionStatus.Failed, detail);
                Logged?.Invoke("Connection error: " + detail);
            }

            if (ct.IsCancellationRequested) break;

            attempt++;
            var seconds = Math.Min(30, Math.Pow(2, Math.Min(attempt, 5)));
            Logged?.Invoke($"Retrying in {seconds:0}s…");
            try
            {
                await Task.Delay(TimeSpan.FromSeconds(seconds), ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }

        StatusChanged?.Invoke(ConnectionStatus.Disconnected, null);
        Logged?.Invoke("Disconnected.");
    }

    private async Task ReceiveLoopAsync(ClientWebSocket socket, CancellationToken ct)
    {
        var buffer = new byte [ReceiveBufferSize];
        using var message = new MemoryStream();

        while (socket.State == WebSocketState.Open && !ct.IsCancellationRequested)
        {
            WebSocketReceiveResult result;
            try
            {
                result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                await CloseQuietlyAsync(socket).ConfigureAwait(false);
                return;
            }

            if (result.MessageType == WebSocketMessageType.Close)
            {
                await CloseQuietlyAsync(socket).ConfigureAwait(false);
                return;
            }

            message.Write(buffer, 0, result.Count);
            if (!result.EndOfMessage) continue;

            var json = Encoding.UTF8.GetString(message.ToArray());
            message.SetLength(0);

            HandleMessage(json);
        }
    }

    /// <summary>
    /// Parses one frame. A malformed or unknown message is logged and skipped —
    /// it never takes the receive loop down.
    /// </summary>
    private void HandleMessage(string json)
    {
        if (string.IsNullOrWhiteSpace(json)) return;

        if (!RelayEvent.TryParse(json, out var evt, out var error) || evt is null)
        {
            Logged?.Invoke("Ignored a message — " + error);
            return;
        }

        if (evt.SchemaVersion != 2)
            Logged?.Invoke($"Note: '{evt.EventName}' arrived with schema_version {evt.SchemaVersion}, expected 2.");

        EventReceived?.Invoke(evt);
    }

    private static async Task SendActionAsync(ClientWebSocket socket, string action, string topic, CancellationToken ct)
    {
        var payload = JsonSerializer.SerializeToUtf8Bytes(new { action, topic });
        await socket.SendAsync(new ArraySegment<byte>(payload), WebSocketMessageType.Text, true, ct)
            .ConfigureAwait(false);
    }

    private static async Task CloseQuietlyAsync(ClientWebSocket socket)
    {
        try
        {
            if (socket.State is WebSocketState.Open or WebSocketState.CloseReceived)
            {
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
                await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "client closing", timeout.Token)
                    .ConfigureAwait(false);
            }
        }
        catch
        {
            // closing is best-effort
        }
    }

    private static string Describe(Exception ex)
    {
        if (ex is WebSocketException && ex.Message.Contains("401"))
            return "the relay rejected the token (401). Check Token in appsettings.json.";

        return ex.InnerException?.Message ?? ex.Message;
    }
}