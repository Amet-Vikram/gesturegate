namespace GestureViewer.Models;

/// <summary>One row in the gesture table. Immutable; newest is inserted at the top.</summary>
public sealed class GestureRow
{
    public string Time { get; init; } = "";
    public string Sequence { get; init; } = "";
    public string Confidence { get; init; } = "";
    public string Gate { get; init; } = "";
    public string Vocab { get; init; } = "";
    public string Device { get; init; } = "";

    /// <summary>True when this arrived while Locked, so it carries no command meaning.</summary>
    public bool IsIgnored { get; init; }
}
