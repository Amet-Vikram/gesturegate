from __future__ import annotations

from typing import Optional

_KNOWN_LABEL_PREFIXES: tuple[str, ...] = ("train_val_",)

EXCLUDED_LABELS: frozenset[str] = frozenset(
    {
        "three",  # diffuse sink class — see module docstring
    }
)

ORIENTATION_CANONICAL: dict[str, str] = {
    "stop_inverted": "stop",
    "peace_inverted": "peace",
    "two_up_inverted": "two_up",
    # canonical forms map to themselves for completeness/clarity;
    # omitted labels (e.g. "ok", "like", "call", "one", "four",
    # "three2", "fist", "palm", "none") pass through unchanged.
}


def _strip_known_prefix(label: str) -> str:
    for prefix in _KNOWN_LABEL_PREFIXES:
        if label.startswith(prefix):
            return label[len(prefix) :]
    return label


def normalize_command_label(
    label: Optional[str],
    confidence: float,
) -> tuple[Optional[str], float]:
    """Applies prefix-stripping, then the exclusion and collapse rules,
    to one raw recognizer output. Call this BEFORE
    GestureDebouncer.update(), i.e.:

        raw_label, conf = top_gesture(result)
        label, conf = normalize_command_label(raw_label, conf)
        confirmed = command_debouncer.update(label, conf)

    Returns (label, confidence)
    """
    if label is None:
        return None, confidence

    stripped = _strip_known_prefix(label)

    if stripped in EXCLUDED_LABELS or stripped == "none":
        return None, confidence

    canonical = ORIENTATION_CANONICAL.get(stripped, stripped)
    return canonical, confidence
