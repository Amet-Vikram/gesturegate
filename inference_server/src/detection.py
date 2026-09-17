from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

# LAYER 1 — PER-FRAME SMOOTHING (DEBOUNCER)


@dataclass
class DebouncerConfig:
    """Tuning for per-frame smoothing.

    n_confirm:       consecutive frames of the same label (each at or
                     above min_confidence) required to confirm a gesture.
                     At 30fps, 5 frames ≈ 165ms of steady hold.
    n_release:       consecutive frames of a *different* label (or no
                     detection) required after a confirmation before the
                     same label may confirm again (hysteresis / re-arm).
    min_confidence:  frames below this confidence are treated as "no
                     detection" regardless of their label.
    """

    n_confirm: int = 5
    n_release: int = 3
    min_confidence: float = 0.60


class GestureDebouncer:
    """Converts noisy per-frame labels into discrete confirmation events.

    Feed it one (label, confidence) pair per video frame via `update()`.
    It returns the confirmed label on exactly the frame a gesture becomes
    confirmed, and None on every other frame.

    Rules:
      * A label must appear n_confirm frames in a row, each at
        confidence >= min_confidence, to be confirmed (rising edge).
      * Any different label, a None label, or a low-confidence frame
        resets the streak counter.
      * After a confirmation, that same label is "latched": it cannot
        confirm again until n_release consecutive frames of anything
        else have been seen (falling edge / re-arm). A different label
        is never blocked by the latch.
    """

    def __init__(self, config: DebouncerConfig | None = None):
        self.config = config or DebouncerConfig()
        self._candidate: Optional[str] = None  # label currently being counted
        self._streak: int = 0  # consecutive frames of candidate
        self._latched: Optional[str] = None  # label blocked until released
        self._release_streak: int = 0  # consecutive non-latched frames

    def reset(self) -> None:
        """Forget all streak/latch state (e.g. when the window closes)."""
        self._candidate = None
        self._streak = 0
        self._latched = None
        self._release_streak = 0

    def update(self, label: Optional[str], confidence: float = 0.0) -> Optional[str]:
        """Process one frame. Returns the confirmed label, or None."""
        cfg = self.config

        # Low-confidence frames count as "nothing detected".
        if label is not None and confidence < cfg.min_confidence:
            label = None

        # Falling edge: track release of the latched label ---
        if self._latched is not None:
            if label != self._latched:
                self._release_streak += 1
                if self._release_streak >= cfg.n_release:
                    self._latched = None
                    self._release_streak = 0
            else:
                # Latched gesture still visible — restart release count.
                self._release_streak = 0

        # Rising edge: streak counting toward confirmation ---
        if label is None or label == self._latched:
            self._candidate = None
            self._streak = 0
            return None

        if label == self._candidate:
            self._streak += 1
        else:
            self._candidate = label
            self._streak = 1

        if self._streak >= cfg.n_confirm:
            # Confirm once, then latch until released.
            self._latched = label
            self._release_streak = 0
            self._candidate = None
            self._streak = 0
            return label

        return None


# LAYER 2 — SEQUENCE STATE MACHINE


class State(enum.Enum):
    IDLE = "IDLE"  # only the clap trigger is heard
    WAIT_GESTURE_1 = "WAIT_GESTURE_1"  # window open, expecting gesture 1
    WAIT_GESTURE_2 = "WAIT_GESTURE_2"  # gesture 1 seen, expecting gesture 2
    COOLDOWN = "COOLDOWN"  # refractory period, all input ignored


class Action(enum.Enum):
    """Side effects the orchestrator must perform after a transition."""

    OPEN_WINDOW = "OPEN_WINDOW"  # start the video pipeline (detect_event.set())
    CLOSE_WINDOW = "CLOSE_WINDOW"  # stop the video pipeline (detect_event.clear())
    PUBLISH_EVENT = "PUBLISH_EVENT"  # sequence complete — push to relay


@dataclass
class SequenceConfig:
    gesture_1: str = "Open_Palm"  # MediaPipe canned-vocabulary label
    gesture_2: str = "Closed_Fist"  # MediaPipe canned-vocabulary label
    gesture_1_timeout_sec: float = 3.0  #  clap -> gesture 1 confirmed
    gesture_2_timeout_sec: float = 3.0  # gesture 1 -> gesture 2 confirmed
    cooldown_sec: float = 2.0


@dataclass
class Transition:
    """Record of a single state change, for logging and for tests."""

    at: float
    src: State
    dst: State
    cause: str
    actions: tuple[Action, ...] = field(default_factory=tuple)


class SequenceStateMachine:
    """Owns sequence order, window timeouts, and the refractory period.

    Inputs:
        on_clap(now): from the audio producer
        on_gesture_confirmed(label, now): from the GestureDebouncer
        on_tick(now): called periodically (any rate) so timeouts fire even when no frames/claps arrive

    Every input returns a list of Actions for the orchestrator to execute.
    """

    def __init__(self, config: SequenceConfig | None = None):
        self.config = config or SequenceConfig()
        self.state: State = State.IDLE
        self._entered_at: float = 0.0
        self.history: list[Transition] = []  # inspectable audit trail

    def _go(self, dst: State, now: float, cause: str, *actions: Action) -> list[Action]:
        self.history.append(
            Transition(
                at=now, src=self.state, dst=dst, cause=cause, actions=tuple(actions)
            )
        )
        self.state = dst
        self._entered_at = now
        return list(actions)

    def on_clap(self, now: float) -> list[Action]:
        if self.state is State.IDLE:
            return self._go(State.WAIT_GESTURE_1, now, "clap", Action.OPEN_WINDOW)
        return []

    def on_gesture_confirmed(self, label: str, now: float) -> list[Action]:
        cfg = self.config

        if self.state is State.WAIT_GESTURE_1 and label == cfg.gesture_1:
            return self._go(State.WAIT_GESTURE_2, now, f"confirmed:{label}")

        if self.state is State.WAIT_GESTURE_2 and label == cfg.gesture_2:
            return self._go(
                State.COOLDOWN,
                now,
                f"confirmed:{label}",
                Action.CLOSE_WINDOW,
                Action.PUBLISH_EVENT,
            )

        # Wrong gesture, or gesture outside a window: ignored. Note the
        # order rule needs no comparison logic — being in WAIT_GESTURE_2
        # already *means* gesture 1 happened.
        return []

    def on_tick(self, now: float) -> list[Action]:
        cfg = self.config
        elapsed = now - self._entered_at

        if self.state is State.WAIT_GESTURE_1 and elapsed >= cfg.gesture_1_timeout_sec:
            return self._go(State.IDLE, now, "timeout:gesture_1", Action.CLOSE_WINDOW)

        if self.state is State.WAIT_GESTURE_2 and elapsed >= cfg.gesture_2_timeout_sec:
            return self._go(State.IDLE, now, "timeout:gesture_2", Action.CLOSE_WINDOW)

        if self.state is State.COOLDOWN and elapsed >= cfg.cooldown_sec:
            return self._go(State.IDLE, now, "cooldown_expired")

        return []
