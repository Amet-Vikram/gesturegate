using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.Windows;
using System.Windows.Media;
using GestureViewer.Models;
using GestureViewer.Services;

namespace GestureViewer;

public partial class MainWindow : Window
{
    private const int MaxLogLines = 500;

    private readonly AppConfig _config;
    private readonly RelayApiClient _api;
    private readonly RelayClient _relay;
    private readonly SessionStateMachine _session = new();

    private readonly ObservableCollection<GestureRow> _gestures = new();
    private readonly ObservableCollection<string> _log = new();

    private ConnectionStatus _status = ConnectionStatus.Disconnected;

    public MainWindow()
    {
        InitializeComponent();

        _config = AppConfig.Load(out var configProblem);
        _api = new RelayApiClient(_config);
        _relay = new RelayClient(_config);

        _relay.StatusChanged += (status, detail) => Post(() => OnStatusChanged(status, detail));
        _relay.EventReceived += evt => Post(() => OnEventReceived(evt));
        _relay.Logged += line => Post(() => AddLog(line));

        GestureList.ItemsSource = _gestures;
        LogList.ItemsSource = _log;

        RelayUrlText.Text = _config.RelayUrl + "   ·   devices from " + new Uri(_config.HttpBaseUri, "/devices");

        if (configProblem is not null) AddLog(configProblem);
        if (string.IsNullOrWhiteSpace(_config.Token))
            AddLog("No token set. Add one to Token in appsettings.json, then restart.");

        RenderState();
        Loaded += MainWindow_Loaded;
    }

    private async void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        AddLog("Config file: " + AppConfig.FilePath);
        await RefreshDevicesAsync();
    }

    // ---------- devices ----------

    private async void RefreshButton_Click(object sender, RoutedEventArgs e) => await RefreshDevicesAsync();

    private async Task RefreshDevicesAsync()
    {
        RefreshButton.IsEnabled = false;
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            var devices = await _api.GetDevicesAsync(timeout.Token);

            var previous = (DeviceCombo.SelectedItem as DeviceInfo)?.DeviceId ?? _config.DeviceId;

            DeviceCombo.ItemsSource = devices;
            DeviceCombo.SelectedItem = devices.FirstOrDefault(d => d.DeviceId == previous) ?? devices.FirstOrDefault();

            AddLog(devices.Count == 0
                ? "No edge devices are online for this token."
                : $"Found {devices.Count} online device(s): {string.Join(", ", devices.Select(d => d.DeviceId))}");
        }
        catch (RelayAuthException ex)
        {
            AddLog(ex.Message);
        }
        catch (Exception ex)
        {
            AddLog("Could not reach the relay: " + (ex.InnerException?.Message ?? ex.Message));
        }
        finally
        {
            RefreshButton.IsEnabled = !_relay.IsRunning;
            UpdateButtons();
        }
    }

    // ---------- connect / disconnect ----------

    private async void ConnectButton_Click(object sender, RoutedEventArgs e)
    {
        if (_relay.IsRunning)
        {
            ConnectButton.IsEnabled = false;
            await _relay.StopAsync();
            _session.Reset();
            RenderState();
            SetControlsEnabled(true);
            return;
        }

        if (DeviceCombo.SelectedItem is not DeviceInfo device)
        {
            AddLog("Pick a device first — refresh if the list is empty.");
            return;
        }

        // Remember the choice for next launch.
        _config.DeviceId = device.DeviceId;
        if (!_config.TrySave(out var saveProblem))
            AddLog("Could not update appsettings.json: " + saveProblem);

        SetControlsEnabled(false);
        _relay.Start(device.DeviceId);
        UpdateButtons();
    }

    private void SetControlsEnabled(bool enabled)
    {
        DeviceCombo.IsEnabled = enabled;
        RefreshButton.IsEnabled = enabled;
        UpdateButtons();
    }

    private void UpdateButtons()
    {
        ConnectButton.IsEnabled = true;
        ConnectButton.Content = _relay.IsRunning ? "Disconnect" : "Connect";
    }

    // ---------- relay events ----------

    private void OnStatusChanged(ConnectionStatus status, string? detail)
    {
        _status = status;

        var (text, brushKey) = status switch
        {
            ConnectionStatus.Connected => ("Connected", "OkBrush"),
            ConnectionStatus.Connecting => ("Connecting…", "WarnBrush"),
            ConnectionStatus.Reconnecting => ("Reconnecting…", "WarnBrush"),
            ConnectionStatus.Failed => ("Connection failed", "ErrorBrush"),
            _ => ("Disconnected", "MutedBrush")
        };

        StatusPillText.Text = text;
        StatusPill.Background = (Brush)FindResource(brushKey);

        if (status is ConnectionStatus.Disconnected or ConnectionStatus.Failed)
            _session.Reset();

        if (detail is not null) AddLog(detail);

        if (status == ConnectionStatus.Disconnected) SetControlsEnabled(true);

        RenderState();
        UpdateButtons();
    }

    private void OnEventReceived(RelayEvent evt)
    {
        switch (evt)
        {
            case RelayEvent.Session session:
                AddLog(_session.Apply(session));
                RenderState();
                break;

            case RelayEvent.Gesture gesture:
                AddGesture(gesture);
                break;
        }
    }

    private void AddGesture(RelayEvent.Gesture gesture)
    {
        var locked = _session.Gate == SessionGate.Locked;
        var arrived = (gesture.DetectedAt ?? DateTimeOffset.Now).ToLocalTime();

        _gestures.Insert(0, new GestureRow
        {
            Time = arrived.ToString("HH:mm:ss", CultureInfo.InvariantCulture),
            Sequence = string.Join(" → ", gesture.Sequence),
            Confidence = gesture.Confidences.Count == 0
                ? "—"
                : string.Join(", ", gesture.Confidences.Select(c => c.ToString("0.00", CultureInfo.InvariantCulture))),
            Gate = locked ? "LOCKED — ignored" : _session.Gate.ToDisplayName(),
            Vocab = string.IsNullOrWhiteSpace(gesture.ModelVocab) ? "—" : gesture.ModelVocab,
            Device = string.IsNullOrWhiteSpace(gesture.DeviceId) ? "—" : gesture.DeviceId,
            IsIgnored = locked
        });

        var max = Math.Max(10, _config.MaxGestureRows);
        while (_gestures.Count > max) _gestures.RemoveAt(_gestures.Count - 1);

        RenderGestureCount();
    }

    private void ClearButton_Click(object sender, RoutedEventArgs e)
    {
        _gestures.Clear();
        RenderGestureCount();
    }

    // ---------- rendering ----------

    private void RenderState()
    {
        var gate = _session.Gate;

        StateText.Text = gate.ToDisplayName();
        StateBanner.Background = (Brush)FindResource(gate switch
        {
            SessionGate.Static => "StaticBrush",
            SessionGate.Dynamic => "DynamicBrush",
            _ => "LockedBrush"
        });

        StateDetailText.Text = gate switch
        {
            SessionGate.Static =>
                "Static gate open — fist, four, like, ok, one, palm, peace, stop, three2, two_up",
            SessionGate.Dynamic =>
                "Dynamic gate open — swipe_up, swipe_down, swipe_left, swipe_right",
            _ when _status != ConnectionStatus.Connected =>
                "Not connected to a relay.",
            _ when _session.LastReason is not null =>
                $"No gate open — gestures are ignored. Last close: {_session.LastReason}.",
            _ => "No gate open — gestures are ignored."
        };

        StateSinceText.Text = _session.ChangedAt is null
            ? ""
            : "since " + _session.ChangedAt.Value.ToLocalTime().ToString("HH:mm:ss", CultureInfo.InvariantCulture);

        RenderGestureCount();
    }

    private void RenderGestureCount()
    {
        GestureCountText.Text = _gestures.Count == 0
            ? "No gestures received yet"
            : $"{_gestures.Count} gesture event(s), newest first";

        EmptyStateText.Visibility = _gestures.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    /// <summary>Marshals a background-thread callback onto the UI thread, safely at shutdown.</summary>
    private void Post(Action action)
    {
        if (Dispatcher.HasShutdownStarted || Dispatcher.HasShutdownFinished) return;
        _ = Dispatcher.InvokeAsync(action);
    }

    private void AddLog(string line)
    {
        _log.Add(DateTime.Now.ToString("HH:mm:ss", CultureInfo.InvariantCulture) + "  " + line);
        while (_log.Count > MaxLogLines) _log.RemoveAt(0);
        LogList.ScrollIntoView(_log[^1]);
    }

    protected override void OnClosing(CancelEventArgs e)
    {
        base.OnClosing(e);
        _ = _relay.StopAsync();
        _api.Dispose();
    }
}
