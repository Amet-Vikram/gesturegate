using GestureViewer.Models;

namespace GestureViewer.Services;

/// <summary>
/// Single source of truth for the displayed gate. The relay owns the real
/// lock state; this only mirrors the session events it sends. Gesture events
/// carry no gate and never change it.
/// </summary>
public sealed class SessionStateMachine
{
    public SessionGate Gate { get; private set; } = SessionGate.Locked;
    public string? LastReason { get; private set; }
    public DateTimeOffset? ChangedAt { get; private set; }

    public event Action<SessionGate>? GateChanged;

    /// <summary>Applies a session event. Returns a line describing what happened.</summary>
    public string Apply(RelayEvent.Session session)
    {
        var previous = Gate;

        if (session.IsUnlock)
        {
            Gate = session.Gate;
            LastReason = null;
        }
        else
        {
            Gate = SessionGate.Locked;
            LastReason = session.Reason;
        }

        ChangedAt = session.DetectedAt ?? DateTimeOffset.Now;

        if (Gate != previous)
            GateChanged?.Invoke(Gate);

        return session.IsUnlock
            ? $"Gate opened: {session.Gate.ToDisplayName()}"
            : $"Gate closed: {session.Gate.ToDisplayName()} ({session.Reason ?? "no reason given"})";
    }

    /// <summary>Called on disconnect — we no longer know the relay's state.</summary>
    public void Reset()
    {
        if (Gate == SessionGate.Locked && LastReason is null) return;
        Gate = SessionGate.Locked;
        LastReason = null;
        ChangedAt = null;
        GateChanged?.Invoke(Gate);
    }
}
