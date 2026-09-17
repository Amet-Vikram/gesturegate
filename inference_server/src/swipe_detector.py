"""
swipe_detector.py — time-boxed, net-displacement swipe direction detector.
===========================================================================

Axis assignment: which two directions are even considered is decided by
which bookend pose was confirmed — "palm" arms the horizontal candidates
(swipe_right / swipe_left), "two_up" arms the vertical candidates
(swipe_up / swipe_down). This is a direct implementation of the bookend
pairs from the original design:
    Swipe Right = palm -> motion -> palm   (net dx, mirrored)
    Swipe Left  = palm -> motion -> palm   (net dx, mirrored)
    Swipe Up    = two_up -> motion -> two_up  (net dy)
    Swipe Down  = two_up -> motion -> two_up  (net dy)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class SwipeAxis(enum.Enum):
    HORIZONTAL = "HORIZONTAL"  # armed by the "palm" bookend
    VERTICAL = "VERTICAL"  # armed by the "two_up" bookend


class SwipeState(enum.Enum):
    WAIT_BOOKEND = "WAIT_BOOKEND"  # idle, listening for a bookend confirmation
    TRACKING = "TRACKING"  # window open, accumulating position


# Which bookend label arms which axis.
BOOKEND_AXIS: dict[str, SwipeAxis] = {
    "palm": SwipeAxis.HORIZONTAL,
    "two_up": SwipeAxis.VERTICAL,
}


@dataclass
class SwipeConfig:
    # Time horizon from bookend confirmation to direction decision.
    # 0.63s matches the mean RECORDING window duration actually measured
    # across the guided-capture spike's 12 trials.
    window_sec: float = 0.63

    # Minimum net displacement (normalized image units) along the
    # dominant axis required to accept a direction at all, rather than
    # treating it as "held still" / noise.
    min_net_displacement: float = 0.06

    # Minimum ratio of the dominant axis's displacement to the
    # off-axis (perpendicular) displacement, required to accept a
    # direction as unambiguous. A diagonal motion that doesn't clearly
    # favour one axis is discarded rather than guessed at.
    axis_dominance_ratio: float = 1.5

    # Correct for the mirrored-camera-vs-facing-subject effect found in
    # the guided-capture spike (horizontal sign_ok was 0%, vertical was
    # 100%). Only affects horizontal (palm-armed) direction naming — the
    # magnitude/threshold checks are unaffected by this flag either way.
    mirror_horizontal: bool = True


@dataclass
class SwipeTransition:
    """Record of one detector event, for logging and for tests."""

    at: float
    event: str  # "tracking_started:<bookend>" / "swipe_confirmed:<direction>" / "discarded:<reason>"


class SwipeDetector:
    """Time-boxed swipe direction detector.

    Inputs:
        on_bookend_confirmed(label, x, y, now)
            A bookend gesture (e.g. "palm" or "two_up") was just
            confirmed by the caller's debouncer..

        on_position_update(x, y)
            Called once per frame while TRACKING, with the current
            wrist (or chosen landmark) position.

        on_tick(now)
            Call every loop iteration. Once `window_sec` has elapsed
            since the tracking window opened, this computes the net
            displacement from the confirmed bookend position to the
            most recently reported position, decides a direction (or
            discards), resets to WAIT_BOOKEND, and returns the decided
            direction string (one of "swipe_right", "swipe_left",
            "swipe_up", "swipe_down") or None if discarded. Returns
            None on every call where the window hasn't elapsed yet, or
            when not currently tracking.

    A discarded attempt (timeout with no clear direction) is not
    reported as an error — on_tick just returns None and the detector
    re-arms for the next bookend confirmation.
    """

    def __init__(self, config: SwipeConfig | None = None):
        self.config = config or SwipeConfig()
        self.state: SwipeState = SwipeState.WAIT_BOOKEND
        self._axis: Optional[SwipeAxis] = None
        self._start_pos: Optional[tuple[float, float]] = None
        self._last_pos: Optional[tuple[float, float]] = None
        self._window_start_at: float = 0.0
        self.history: list[SwipeTransition] = []

    # helpers

    @property
    def is_tracking(self) -> bool:
        return self.state is SwipeState.TRACKING

    # inputs

    def on_bookend_confirmed(self, label: str, x: float, y: float, now: float) -> None:
        if self.state is SwipeState.TRACKING:
            return  # already tracking an attempt — ignore re-confirmation
        axis = BOOKEND_AXIS.get(label)
        if axis is None:
            return  # not a recognized bookend label — ignore
        self.state = SwipeState.TRACKING
        self._axis = axis
        self._start_pos = (x, y)
        self._last_pos = (x, y)
        self._window_start_at = now
        self.history.append(SwipeTransition(now, f"tracking_started:{label}"))

    def on_position_update(self, x: float, y: float) -> None:
        if self.state is not SwipeState.TRACKING:
            return
        self._last_pos = (x, y)

    def on_tick(self, now: float) -> Optional[str]:
        if self.state is not SwipeState.TRACKING:
            return None
        if (now - self._window_start_at) < self.config.window_sec:
            return None

        direction, reason = self._decide_direction()

        # Reset regardless of outcome
        self.state = SwipeState.WAIT_BOOKEND
        self._axis = None
        self._start_pos = None
        self._last_pos = None

        if direction is not None:
            self.history.append(SwipeTransition(now, f"swipe_confirmed:{direction}"))
        else:
            self.history.append(SwipeTransition(now, f"discarded:{reason}"))
        return direction

    def reset(self) -> None:
        """Force the detector back to WAIT_BOOKEND, abandoning any
        in-progress tracking attempt without resolving a direction.
        Intended for the orchestrator to call when the dynamic gate is
        being closed (session locked) while a swipe attempt might be
        mid-window"""
        self.state = SwipeState.WAIT_BOOKEND
        self._axis = None
        self._start_pos = None
        self._last_pos = None

    # internal

    def _decide_direction(self) -> tuple[Optional[str], str]:
        """Returns (direction_or_None, reason). reason is only meaningful
        when direction is None, for the audit trail / debug overlay."""
        cfg = self.config
        dx = self._last_pos[0] - self._start_pos[0]
        dy = self._last_pos[1] - self._start_pos[1]

        if self._axis is SwipeAxis.HORIZONTAL:
            if abs(dx) < cfg.min_net_displacement:
                return None, "too_small"
            if abs(dx) < cfg.axis_dominance_ratio * abs(dy):
                return None, "ambiguous_axis"
            signed_dx = -dx if cfg.mirror_horizontal else dx
            return ("swipe_right" if signed_dx > 0 else "swipe_left"), ""

        else:  # SwipeAxis.VERTICAL
            if abs(dy) < cfg.min_net_displacement:
                return None, "too_small"
            if abs(dy) < cfg.axis_dominance_ratio * abs(dx):
                return None, "ambiguous_axis"
            # Image coordinates: y increases DOWNWARD. dy > 0 means the
            # hand moved down the frame.
            return ("swipe_down" if dy > 0 else "swipe_up"), ""
