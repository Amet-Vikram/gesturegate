"""
Unit tests for detection.py — no camera, no microphone, no ML runtime.

Run with:  python -m pytest test_detection.py -v
      or:  python test_detection.py

These tests exist to demonstrate that every timing and sequencing behaviour of the inference server
is verifiable with synthetic inputs on any machine, in milliseconds.
"""

from detection import (
    Action,
    DebouncerConfig,
    GestureDebouncer,
    SequenceConfig,
    SequenceStateMachine,
    State,
)

PALM = "Open_Palm"
FIST = "Closed_Fist"


def make_debouncer(n_confirm=5, n_release=3, min_confidence=0.7):
    return GestureDebouncer(DebouncerConfig(n_confirm, n_release, min_confidence))


# Debouncer


def test_steady_hold_confirms_once():
    d = make_debouncer()
    results = [d.update(PALM, 0.9) for _ in range(20)]
    assert results.count(PALM) == 1  # confirms exactly once...
    assert results[4] == PALM  # ...on the 5th frame


def test_single_frame_flicker_is_ignored():
    d = make_debouncer()
    # 4 palm frames, one spurious fist frame, 4 more palm frames:
    seq = [PALM] * 4 + [FIST] + [PALM] * 4
    results = [d.update(lbl, 0.9) for lbl in seq]
    assert FIST not in results  # flicker never confirms
    assert PALM not in results  # streak was reset — 4+4 != 5 in a row


def test_dropout_resets_streak():
    d = make_debouncer()
    seq = [PALM, PALM, PALM, None, PALM, PALM, PALM, PALM, PALM]
    results = [d.update(lbl, 0.9) for lbl in seq]
    assert results[:8] == [None] * 8
    assert results[8] == PALM  # 5 consecutive only after the gap


def test_low_confidence_counts_as_no_detection():
    d = make_debouncer()
    confs = [0.9, 0.9, 0.4, 0.9, 0.9, 0.9, 0.9, 0.9]
    results = [d.update(PALM, c) for c in confs]
    assert results[:7] == [None] * 7
    assert results[7] == PALM  # streak restarted after weak frame


def test_hysteresis_requires_release_before_reconfirm():
    d = make_debouncer()
    for _ in range(30):  # long hold: one confirmation
        d.update(PALM, 0.9)
    # Only 2 release frames (< n_release=3): still latched.
    d.update(None, 0.0)
    d.update(None, 0.0)
    results = [d.update(PALM, 0.9) for _ in range(10)]
    assert PALM not in results
    # Full release, then a fresh hold: confirms again.
    d2 = make_debouncer()
    for _ in range(10):
        d2.update(PALM, 0.9)
    for _ in range(3):  # >= n_release frames of nothing
        d2.update(None, 0.0)
    results = [d2.update(PALM, 0.9) for _ in range(5)]
    assert results[4] == PALM


def test_latch_does_not_block_a_different_label():
    d = make_debouncer()
    for _ in range(10):
        d.update(PALM, 0.9)  # palm confirmed and latched
    results = [d.update(FIST, 0.9) for _ in range(5)]
    assert results[4] == FIST  # fist confirms immediately after


# State machine


def make_machine():
    return SequenceStateMachine(
        SequenceConfig(
            gesture_1=PALM,
            gesture_2=FIST,
            gesture_1_timeout_sec=3.0,
            gesture_2_timeout_sec=3.0,
            cooldown_sec=2.0,
        )
    )


def test_happy_path_fires_exactly_the_right_actions():
    m = make_machine()
    assert m.on_clap(now=10.0) == [Action.OPEN_WINDOW]
    assert m.state is State.WAIT_GESTURE_1
    assert m.on_gesture_confirmed(PALM, now=11.0) == []
    assert m.state is State.WAIT_GESTURE_2
    actions = m.on_gesture_confirmed(FIST, now=12.0)
    assert actions == [Action.CLOSE_WINDOW, Action.PUBLISH_EVENT]
    assert m.state is State.COOLDOWN


def test_gestures_in_idle_are_never_evaluated():
    m = make_machine()
    assert m.on_gesture_confirmed(PALM, now=1.0) == []
    assert m.on_gesture_confirmed(FIST, now=2.0) == []
    assert m.state is State.IDLE  # a fist all day triggers nothing


def test_wrong_order_does_not_advance():
    m = make_machine()
    m.on_clap(now=0.0)
    assert m.on_gesture_confirmed(FIST, now=1.0) == []  # fist first: ignored
    assert m.state is State.WAIT_GESTURE_1
    m.on_gesture_confirmed(PALM, now=1.5)
    actions = m.on_gesture_confirmed(FIST, now=2.0)  # correct order works
    assert Action.PUBLISH_EVENT in actions


def test_gesture_1_timeout_resets_to_idle():
    m = make_machine()
    m.on_clap(now=0.0)
    assert m.on_tick(now=2.9) == []  # not yet
    assert m.on_tick(now=3.0) == [Action.CLOSE_WINDOW]  # timeout fires
    assert m.state is State.IDLE
    # The partial sequence is discarded: a fist now does nothing.
    assert m.on_gesture_confirmed(FIST, now=3.1) == []


def test_gesture_2_timeout_resets_to_idle():
    m = make_machine()
    m.on_clap(now=0.0)
    m.on_gesture_confirmed(PALM, now=1.0)
    assert m.on_tick(now=4.0) == [Action.CLOSE_WINDOW]  # 3s after entering
    assert m.state is State.IDLE


def test_cooldown_ignores_everything_then_expires():
    m = make_machine()
    m.on_clap(now=0.0)
    m.on_gesture_confirmed(PALM, now=0.5)
    m.on_gesture_confirmed(FIST, now=1.0)  # → COOLDOWN
    assert m.on_clap(now=1.5) == []  # clap echo: deaf
    assert m.on_gesture_confirmed(PALM, now=1.6) == []  # lingering hand: deaf
    assert m.state is State.COOLDOWN
    assert m.on_tick(now=3.0) == []  # refractory: back to IDLE
    assert m.state is State.IDLE
    assert m.on_clap(now=3.1) == [Action.OPEN_WINDOW]  # and re-armed


def test_clap_during_open_window_is_ignored():
    m = make_machine()
    m.on_clap(now=0.0)
    assert m.on_clap(now=0.5) == []  # echo doesn't restart window
    assert m.state is State.WAIT_GESTURE_1
    # Timeout still measured from the ORIGINAL clap:
    assert m.on_tick(now=3.0) == [Action.CLOSE_WINDOW]


def test_history_is_a_complete_audit_trail():
    m = make_machine()
    m.on_clap(now=0.0)
    m.on_gesture_confirmed(PALM, now=1.0)
    m.on_gesture_confirmed(FIST, now=2.0)
    m.on_tick(now=5.0)
    causes = [t.cause for t in m.history]
    assert causes == [
        "clap",
        f"confirmed:{PALM}",
        f"confirmed:{FIST}",
        "cooldown_expired",
    ]


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
