"""
Unit tests for session.py — no hardware, no ML runtime, injected time.

Run with:  python -m pytest test_session.py -v
      or:  python test_session.py
"""

from session import (
    Session,
    SessionAction,
    SessionConfig,
    SessionStateMachine,
    UnlockTarget,
)

STATIC = UnlockTarget.STATIC
DYNAMIC = UnlockTarget.DYNAMIC


def make_machine(idle_timeout_sec=30.0):
    return SessionStateMachine(SessionConfig(idle_timeout_sec=idle_timeout_sec))


# Toggle behaviour — static target (mirrors original 2-state tests)


def test_starts_locked():
    m = make_machine()
    assert m.state is Session.LOCKED
    assert not m.is_unlocked
    assert m.active_target is None


def test_static_unlock_opens_static_session():
    m = make_machine()
    actions = m.on_unlock_sequence(STATIC, now=10.0)
    assert m.state is Session.STATIC_UNLOCKED
    assert m.active_target is STATIC
    assert actions == [SessionAction.ENABLE_COMMANDS, SessionAction.PUBLISH_UNLOCK]


def test_same_static_sequence_locks_again():
    m = make_machine()
    m.on_unlock_sequence(STATIC, now=10.0)
    actions = m.on_unlock_sequence(STATIC, now=20.0)
    assert m.state is Session.LOCKED
    assert m.active_target is None
    assert actions == [SessionAction.DISABLE_COMMANDS, SessionAction.PUBLISH_LOCK]


def test_dynamic_unlock_opens_dynamic_session():
    m = make_machine()
    actions = m.on_unlock_sequence(DYNAMIC, now=10.0)
    assert m.state is Session.DYNAMIC_UNLOCKED
    assert m.active_target is DYNAMIC
    assert actions == [SessionAction.ENABLE_COMMANDS, SessionAction.PUBLISH_UNLOCK]


def test_same_dynamic_sequence_locks_again():
    m = make_machine()
    m.on_unlock_sequence(DYNAMIC, now=10.0)
    actions = m.on_unlock_sequence(DYNAMIC, now=20.0)
    assert m.state is Session.LOCKED
    assert actions == [SessionAction.DISABLE_COMMANDS, SessionAction.PUBLISH_LOCK]


def test_full_toggle_cycle_static():
    m = make_machine()
    m.on_unlock_sequence(STATIC, now=1.0)
    m.on_unlock_sequence(STATIC, now=2.0)
    m.on_unlock_sequence(STATIC, now=3.0)
    assert m.state is Session.STATIC_UNLOCKED
    causes = [t.cause for t in m.history]
    assert causes == [
        "unlock_sequence:STATIC",
        "lock_sequence:STATIC",
        "unlock_sequence:STATIC",
    ]


def test_dynamic_cannot_open_while_static_unlocked():
    # Simulates a caller bug: target mismatch while a DIFFERENT gate is
    # already open. Must be a no-op, not a state change.
    m = make_machine()
    m.on_unlock_sequence(STATIC, now=1.0)
    assert m.state is Session.STATIC_UNLOCKED
    actions = m.on_unlock_sequence(DYNAMIC, now=2.0)
    assert actions == []
    assert m.state is Session.STATIC_UNLOCKED  # unchanged
    assert m.active_target is STATIC  # unchanged


def test_static_cannot_open_while_dynamic_unlocked():
    m = make_machine()
    m.on_unlock_sequence(DYNAMIC, now=1.0)
    assert m.state is Session.DYNAMIC_UNLOCKED
    actions = m.on_unlock_sequence(STATIC, now=2.0)
    assert actions == []
    assert m.state is Session.DYNAMIC_UNLOCKED
    assert m.active_target is DYNAMIC


def test_only_one_gate_ever_open_across_a_mixed_sequence():
    # A longer, mixed sequence of attempts — at every point, is_unlocked
    # implies exactly one of is_static_unlocked / is_dynamic_unlocked.
    m = make_machine()
    checkpoints = []

    def snapshot():
        checkpoints.append((m.is_static_unlocked, m.is_dynamic_unlocked))

    m.on_unlock_sequence(STATIC, now=1.0)
    snapshot()  # -> static open
    m.on_unlock_sequence(DYNAMIC, now=2.0)
    snapshot()  # ignored, still static
    m.on_unlock_sequence(STATIC, now=3.0)
    snapshot()  # -> locked
    m.on_unlock_sequence(DYNAMIC, now=4.0)
    snapshot()  # -> dynamic open
    m.on_unlock_sequence(STATIC, now=5.0)
    snapshot()  # ignored, still dynamic
    m.on_unlock_sequence(DYNAMIC, now=6.0)
    snapshot()  # -> locked

    for static_on, dynamic_on in checkpoints:
        assert not (static_on and dynamic_on), "both gates open simultaneously"
    assert checkpoints == [
        (True, False),
        (True, False),
        (False, False),
        (False, True),
        (False, True),
        (False, False),
    ]


def test_command_activity_ignored_while_locked():
    m = make_machine()
    assert m.on_command_activity(now=5.0) == []
    assert m.state is Session.LOCKED


def test_command_activity_keeps_static_session_alive():
    m = make_machine(idle_timeout_sec=10.0)
    m.on_unlock_sequence(STATIC, now=0.0)
    m.on_command_activity(now=8.0)
    assert m.on_tick(now=10.0) == []
    assert m.state is Session.STATIC_UNLOCKED


def test_command_activity_keeps_dynamic_session_alive():
    m = make_machine(idle_timeout_sec=10.0)
    m.on_unlock_sequence(DYNAMIC, now=0.0)
    m.on_command_activity(now=8.0)
    assert m.on_tick(now=10.0) == []
    assert m.state is Session.DYNAMIC_UNLOCKED


def test_idle_timeout_auto_locks_static():
    m = make_machine(idle_timeout_sec=10.0)
    m.on_unlock_sequence(STATIC, now=0.0)
    assert m.on_tick(now=9.9) == []
    actions = m.on_tick(now=10.0)
    assert actions == [SessionAction.DISABLE_COMMANDS, SessionAction.PUBLISH_LOCK]
    assert m.state is Session.LOCKED
    assert m.active_target is None


def test_idle_timeout_auto_locks_dynamic():
    m = make_machine(idle_timeout_sec=10.0)
    m.on_unlock_sequence(DYNAMIC, now=0.0)
    actions = m.on_tick(now=10.0)
    assert actions == [SessionAction.DISABLE_COMMANDS, SessionAction.PUBLISH_LOCK]
    assert m.state is Session.LOCKED


def test_idle_timeout_measured_from_last_activity():
    m = make_machine(idle_timeout_sec=10.0)
    m.on_unlock_sequence(STATIC, now=0.0)
    m.on_command_activity(now=6.0)
    assert m.on_tick(now=15.0) == []
    actions = m.on_tick(now=16.0)
    assert SessionAction.PUBLISH_LOCK in actions
    assert m.state is Session.LOCKED


def test_idle_timeout_disabled_when_zero():
    m = make_machine(idle_timeout_sec=0.0)
    m.on_unlock_sequence(STATIC, now=0.0)
    assert m.on_tick(now=10_000.0) == []
    assert m.state is Session.STATIC_UNLOCKED


def test_tick_does_nothing_while_locked():
    m = make_machine(idle_timeout_sec=10.0)
    assert m.on_tick(now=1000.0) == []
    assert m.state is Session.LOCKED


def test_convenience_properties_static():
    m = make_machine()
    m.on_unlock_sequence(STATIC, now=0.0)
    assert m.is_unlocked
    assert m.is_static_unlocked
    assert not m.is_dynamic_unlocked


def test_convenience_properties_dynamic():
    m = make_machine()
    m.on_unlock_sequence(DYNAMIC, now=0.0)
    assert m.is_unlocked
    assert m.is_dynamic_unlocked
    assert not m.is_static_unlocked


def test_convenience_properties_locked():
    m = make_machine()
    assert not m.is_unlocked
    assert not m.is_static_unlocked
    assert not m.is_dynamic_unlocked


def test_history_records_every_transition():
    m = make_machine(idle_timeout_sec=5.0)
    m.on_unlock_sequence(STATIC, now=0.0)
    m.on_tick(now=5.0)
    assert len(m.history) == 2
    assert m.history[0].cause == "unlock_sequence:STATIC"
    assert m.history[0].dst is Session.STATIC_UNLOCKED
    assert m.history[1].cause == "idle_timeout"
    assert m.history[1].dst is Session.LOCKED


def test_history_does_not_record_ignored_mismatched_target():
    m = make_machine()
    m.on_unlock_sequence(STATIC, now=0.0)
    m.on_unlock_sequence(DYNAMIC, now=1.0)  # ignored — must not appear in history
    assert len(m.history) == 1
    assert m.history[0].cause == "unlock_sequence:STATIC"


if __name__ == "__main__":
    import sys
    import traceback

    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError:
            failed += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
