namespace GestureViewer.Models;

/// <summary>
/// The three states this app displays. The relay owns the real state; this
/// client is a read-only observer of it.
/// </summary>
public enum SessionGate
{
    Locked,
    Static,
    Dynamic
}

public static class SessionGateExtensions
{
    /// <summary>Parses the wire value of a session event's <c>gate</c> field.</summary>
    public static bool TryParseGate(string? value, out SessionGate gate)
    {
        switch (value?.Trim().ToLowerInvariant())
        {
            case "static":
                gate = SessionGate.Static;
                return true;
            case "dynamic":
                gate = SessionGate.Dynamic;
                return true;
            default:
                gate = SessionGate.Locked;
                return false;
        }
    }

    public static string ToDisplayName(this SessionGate gate) => gate switch
    {
        SessionGate.Static => "STATIC",
        SessionGate.Dynamic => "DYNAMIC",
        _ => "LOCKED"
    };
}
