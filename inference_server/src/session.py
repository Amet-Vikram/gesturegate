"""
Session gating — LOCKED / STATIC_UNLOCKED / DYNAMIC_UNLOCKED
=====================================================================

Outer layer of control on top of detection.py's SequenceStateMachine.
Implements the "cascading control" design, extended to TWO parallel
command vocabularies that are mutually exclusive by construction:

    * STATIC gate — opened by clap  ->  Open_Palm  ->  Closed_Fist. Routes the
      HaGRID command vocabulary (single confirmed gestures) to the relay.

    * DYNAMIC gate — opened by clap  ->  Victory  ->  Closed_Fist (the stock
      MediaPipe name for a "peace" sign). Routes SwipeDetector's motion-
      based direction events to the relay instead.

    * Only ONE gate can be open at a time.

    * The SAME unlock sequence performed again while its own gate is
      open locks the session back down (unchanged from the two-state
      design).

    * An idle timeout auto-locks a session left open too long,
      regardless of which gate was open.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class Session(enum.Enum):
    LOCKED = "LOCKED"  # neither vocabulary active
    STATIC_UNLOCKED = "STATIC_UNLOCKED"  # HaGRID single-gesture commands active
    DYNAMIC_UNLOCKED = "DYNAMIC_UNLOCKED"  # SwipeDetector motion tracking active


class UnlockTarget(enum.Enum):
    """Which gate an unlock-sequence completion is trying to open/close."""

    STATIC = "STATIC"
    DYNAMIC = "DYNAMIC"


class SessionAction(enum.Enum):
    """Side effects the orchestrator must perform after a session change."""

    ENABLE_COMMANDS = "ENABLE_COMMANDS"  # start routing the now-active vocabulary
    DISABLE_COMMANDS = "DISABLE_COMMANDS"  # stop routing; discard in-flight state
    PUBLISH_UNLOCK = "PUBLISH_UNLOCK"  # notify clients the session opened
    PUBLISH_LOCK = "PUBLISH_LOCK"  # notify clients the session closed


@dataclass
class SessionConfig:
    # Auto-lock after this many seconds with no activity while unlocked
    # (either gate).
    idle_timeout_sec: float = 30.0


@dataclass
class SessionTransition:
    """Record of a single session change, for logging and for tests."""

    at: float
    src: Session
    dst: Session
    cause: str
    actions: tuple[SessionAction, ...] = field(default_factory=tuple)


_TARGET_TO_UNLOCKED_STATE = {
    UnlockTarget.STATIC: Session.STATIC_UNLOCKED,
    UnlockTarget.DYNAMIC: Session.DYNAMIC_UNLOCKED,
}


class SessionStateMachine:
    """Outer gate toggled by completion of either unlock sequence.

    Inputs (all explicit, all timestamped by the caller):
        on_unlock_sequence(target, now)
            An inner SequenceStateMachine (static's or dynamic's) just
            completed its clap -> gesture1 -> gesture2 sequence. Toggles the
            session according to `target` and the current state.
        on_command_activity(now)
            Activity was confirmed in whichever gate is currently open
            (a HaGRID command, or SwipeDetector tracking progress).
            Resets the idle-timeout clock. Ignored while LOCKED.
        on_tick(now)
            Called periodically so the idle timeout fires even when
            nothing else arrives.

    Every input returns a list of SessionActions for the orchestrator.
    Inputs that don't apply in the current state are ignored by design.
    """

    def __init__(self, config: SessionConfig | None = None):
        self.config = config or SessionConfig()
        self.state: Session = Session.LOCKED
        self.active_target: Optional[UnlockTarget] = None
        self._entered_at: float = 0.0
        self._last_activity_at: float = 0.0
        self.history: list[SessionTransition] = []

    # internal

    def _go(
        self,
        dst: Session,
        target: Optional[UnlockTarget],
        now: float,
        cause: str,
        *actions: SessionAction,
    ) -> list[SessionAction]:
        self.history.append(
            SessionTransition(
                at=now, src=self.state, dst=dst, cause=cause, actions=tuple(actions)
            )
        )
        self.state = dst
        self.active_target = target
        self._entered_at = now
        self._last_activity_at = now
        return list(actions)

    # inputs

    def on_unlock_sequence(
        self, target: UnlockTarget, now: float
    ) -> list[SessionAction]:
        if self.state is Session.LOCKED:
            return self._go(
                _TARGET_TO_UNLOCKED_STATE[target],
                target,
                now,
                f"unlock_sequence:{target.value}",
                SessionAction.ENABLE_COMMANDS,
                SessionAction.PUBLISH_UNLOCK,
            )

        if self.active_target is target:
            # The SAME gate's sequence completing again locks it back down.
            return self._go(
                Session.LOCKED,
                None,
                now,
                f"lock_sequence:{target.value}",
                SessionAction.DISABLE_COMMANDS,
                SessionAction.PUBLISH_LOCK,
            )

        # A mismatched target while a DIFFERENT gate is already open.
        return []

    def on_command_activity(self, now: float) -> list[SessionAction]:
        """Activity was confirmed in whichever gate is open. Ignored
        while LOCKED — commands should never even reach here when
        locked, but guarding makes the machine safe against caller
        mistakes."""
        if self.state is not Session.LOCKED:
            self._last_activity_at = now
        return []

    def on_tick(self, now: float) -> list[SessionAction]:
        """Fire the idle auto-lock if enabled and the session has been
        quiet too long, regardless of which gate is open."""
        cfg = self.config
        if (
            self.state is not Session.LOCKED
            and cfg.idle_timeout_sec > 0
            and (now - self._last_activity_at) >= cfg.idle_timeout_sec
        ):
            return self._go(
                Session.LOCKED,
                None,
                now,
                "idle_timeout",
                SessionAction.DISABLE_COMMANDS,
                SessionAction.PUBLISH_LOCK,
            )
        return []

    # helpers

    @property
    def is_unlocked(self) -> bool:
        return self.state is not Session.LOCKED

    @property
    def is_static_unlocked(self) -> bool:
        return self.state is Session.STATIC_UNLOCKED

    @property
    def is_dynamic_unlocked(self) -> bool:
        return self.state is Session.DYNAMIC_UNLOCKED
