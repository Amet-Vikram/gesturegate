"""
Unit tests for swipe_detector.py — no camera, no MediaPipe, injected time.

Run with:  python -m pytest test_swipe_detector.py -v
      or:  python test_swipe_detector.py
"""

from swipe_detector import SwipeAxis, SwipeConfig, SwipeDetector, SwipeState


def make_detector(**overrides):
    return SwipeDetector(SwipeConfig(**overrides))


# Tracking lifecycle


def test_starts_in_wait_bookend():
    d = make_detector()
    assert d.state is SwipeState.WAIT_BOOKEND
    assert not d.is_tracking


def test_bookend_confirmation_starts_tracking():
    d = make_detector()
    d.on_bookend_confirmed("palm", x=0.3, y=0.5, now=10.0)
    assert d.state is SwipeState.TRACKING
    assert d.is_tracking


def test_unrecognized_bookend_label_is_ignored():
    d = make_detector()
    d.on_bookend_confirmed("ok", x=0.3, y=0.5, now=10.0)  # not a bookend
    assert d.state is SwipeState.WAIT_BOOKEND


def test_reconfirmation_while_tracking_is_ignored():
    d = make_detector(window_sec=1.0)
    d.on_bookend_confirmed("palm", x=0.3, y=0.5, now=10.0)
    # A second confirmation of the same held pose mid-window must NOT
    # reset the tracking window or its start position.
    d.on_bookend_confirmed("palm", x=0.9, y=0.9, now=10.2)
    assert d._start_pos == (0.3, 0.5)  # unchanged
    assert d._window_start_at == 10.0  # unchanged


def test_position_updates_ignored_while_not_tracking():
    d = make_detector()
    d.on_position_update(0.5, 0.5)  # no crash, no effect
    assert d._last_pos is None


# Timing / on_tick gating


def test_tick_before_window_elapsed_returns_none():
    d = make_detector(window_sec=0.63)
    d.on_bookend_confirmed("palm", 0.3, 0.5, now=0.0)
    assert d.on_tick(now=0.5) is None
    assert d.is_tracking  # still tracking, window not elapsed


def test_tick_while_not_tracking_returns_none():
    d = make_detector()
    assert d.on_tick(now=100.0) is None


def test_tick_at_exact_window_boundary_resolves():
    d = make_detector(window_sec=0.63, min_net_displacement=0.05)
    d.on_bookend_confirmed("palm", 0.3, 0.5, now=0.0)
    d.on_position_update(0.5, 0.5)  # dx=0.2, clears threshold
    result = d.on_tick(now=0.63)
    assert result is not None
    assert d.state is SwipeState.WAIT_BOOKEND  # re-armed


# Direction decisions — horizontal (palm), with mirroring


def test_swipe_right_with_mirroring_default():
    # Physical rightward motion in an unmirrored camera feed, with a
    # subject facing the camera, appears as NEGATIVE dx in raw pixel
    # coordinates (the spike's finding: sign_ok was 0% without correction).
    # mirror_horizontal=True (default) should correct this to "swipe_right".
    d = make_detector(window_sec=0.63, min_net_displacement=0.05)
    d.on_bookend_confirmed("palm", x=0.7, y=0.5, now=0.0)
    d.on_position_update(0.3, 0.5)  # raw dx = -0.4 (moved left in frame)
    result = d.on_tick(now=0.63)
    assert result == "swipe_right"  # mirrored: raw-left reads as "right"


def test_swipe_left_with_mirroring_default():
    d = make_detector(window_sec=0.63, min_net_displacement=0.05)
    d.on_bookend_confirmed("palm", x=0.3, y=0.5, now=0.0)
    d.on_position_update(0.7, 0.5)  # raw dx = +0.4 (moved right in frame)
    result = d.on_tick(now=0.63)
    assert result == "swipe_left"  # mirrored: raw-right reads as "left"


def test_swipe_direction_without_mirroring():
    d = make_detector(
        window_sec=0.63, min_net_displacement=0.05, mirror_horizontal=False
    )
    d.on_bookend_confirmed("palm", x=0.3, y=0.5, now=0.0)
    d.on_position_update(0.7, 0.5)  # raw dx = +0.4
    result = d.on_tick(now=0.63)
    assert result == "swipe_right"  # unmirrored: raw-right reads as "right"


# Direction decisions — vertical (two_up), no mirroring


def test_swipe_down_vertical():
    # Image coords: y increases DOWNWARD.
    d = make_detector(window_sec=0.63, min_net_displacement=0.05)
    d.on_bookend_confirmed("two_up", x=0.5, y=0.3, now=0.0)
    d.on_position_update(0.5, 0.7)  # dy = +0.4 (moved down the frame)
    result = d.on_tick(now=0.63)
    assert result == "swipe_down"


def test_swipe_up_vertical():
    d = make_detector(window_sec=0.63, min_net_displacement=0.05)
    d.on_bookend_confirmed("two_up", x=0.5, y=0.7, now=0.0)
    d.on_position_update(0.5, 0.3)  # dy = -0.4 (moved up the frame)
    result = d.on_tick(now=0.63)
    assert result == "swipe_up"


# Discard paths — the second open question's answer


def test_discarded_too_small_reads_none_and_rearms():
    d = make_detector(window_sec=0.63, min_net_displacement=0.10)
    d.on_bookend_confirmed("palm", x=0.5, y=0.5, now=0.0)
    d.on_position_update(0.52, 0.5)  # dx = 0.02, well under threshold
    result = d.on_tick(now=0.63)
    assert result is None
    assert d.state is SwipeState.WAIT_BOOKEND  # re-armed, not stuck


def test_discarded_ambiguous_diagonal_rearms():
    d = make_detector(
        window_sec=0.63, min_net_displacement=0.05, axis_dominance_ratio=1.5
    )
    d.on_bookend_confirmed("palm", x=0.5, y=0.5, now=0.0)
    # dx and dy nearly equal -> not clearly horizontal-dominant
    d.on_position_update(0.65, 0.62)  # dx=0.15, dy=0.12, ratio ~1.25 < 1.5
    result = d.on_tick(now=0.63)
    assert result is None
    assert d.state is SwipeState.WAIT_BOOKEND


def test_no_position_update_before_timeout_discards():
    # Bookend confirmed, but the hand was never seen again before the
    # window elapsed (e.g. immediate occlusion). last_pos stays at the
    # start position, net displacement is zero -> discarded, not a crash.
    d = make_detector(window_sec=0.63, min_net_displacement=0.05)
    d.on_bookend_confirmed("palm", x=0.5, y=0.5, now=0.0)
    result = d.on_tick(now=0.63)
    assert result is None
    assert d.state is SwipeState.WAIT_BOOKEND


def test_detector_rearms_immediately_after_discard_for_next_attempt():
    # Explicit test of the design decision: a failed/ambiguous swipe
    # times out and is discarded, then the detector waits for the NEXT
    # confirmed bookend to start a fresh attempt -- no special state,
    # no retry of the same attempt.
    d = make_detector(window_sec=0.5, min_net_displacement=0.10)
    d.on_bookend_confirmed("palm", x=0.5, y=0.5, now=0.0)
    d.on_position_update(0.51, 0.5)  # too small, will be discarded
    assert d.on_tick(now=0.5) is None
    assert d.state is SwipeState.WAIT_BOOKEND

    # A fresh bookend confirmation immediately starts a NEW, independent
    # attempt with its own start position and window.
    d.on_bookend_confirmed("palm", x=0.2, y=0.5, now=1.0)
    assert d.is_tracking
    d.on_position_update(0.6, 0.5)  # dx = 0.4, clears threshold this time
    result = d.on_tick(now=1.5)
    assert result is not None


# Axis isolation — horizontal bookend never yields a vertical result


def test_horizontal_bookend_never_yields_vertical_direction():
    d = make_detector(
        window_sec=0.63, min_net_displacement=0.05, axis_dominance_ratio=1.5
    )
    d.on_bookend_confirmed("palm", x=0.5, y=0.5, now=0.0)
    d.on_position_update(0.5, 0.9)  # pure vertical motion under a HORIZONTAL bookend
    result = d.on_tick(now=0.63)
    # Pure vertical motion under a palm (horizontal-armed) bookend must
    # be evaluated against the horizontal axis only -- dx is ~0, so this
    # should discard, never return "swipe_up"/"swipe_down".
    assert result is None
    assert result not in ("swipe_up", "swipe_down")


def test_vertical_bookend_never_yields_horizontal_direction():
    d = make_detector(
        window_sec=0.63, min_net_displacement=0.05, axis_dominance_ratio=1.5
    )
    d.on_bookend_confirmed("two_up", x=0.5, y=0.5, now=0.0)
    d.on_position_update(0.9, 0.5)  # pure horizontal motion under a VERTICAL bookend
    result = d.on_tick(now=0.63)
    assert result is None
    assert result not in ("swipe_right", "swipe_left")


# Audit trail
def test_history_records_start_and_outcome():
    d = make_detector(window_sec=0.63, min_net_displacement=0.05)
    d.on_bookend_confirmed("palm", x=0.3, y=0.5, now=0.0)
    d.on_position_update(0.7, 0.5)
    d.on_tick(now=0.63)
    assert len(d.history) == 2
    assert d.history[0].event == "tracking_started:palm"
    assert d.history[1].event.startswith("swipe_confirmed:")


def test_history_records_discard_reason():
    d = make_detector(window_sec=0.63, min_net_displacement=0.10)
    d.on_bookend_confirmed("palm", x=0.5, y=0.5, now=0.0)
    d.on_position_update(0.51, 0.5)  # too small
    d.on_tick(now=0.63)
    assert d.history[-1].event == "discarded:too_small"


# External reset() — orchestrator abandons a mid-flight attempt


def test_reset_forces_wait_bookend_from_tracking():
    d = make_detector()
    d.on_bookend_confirmed("palm", x=0.5, y=0.5, now=0.0)
    assert d.is_tracking
    d.reset()
    assert d.state is SwipeState.WAIT_BOOKEND
    assert not d.is_tracking
    assert d._start_pos is None
    assert d._last_pos is None


def test_reset_is_a_noop_when_already_wait_bookend():
    d = make_detector()
    assert d.state is SwipeState.WAIT_BOOKEND
    d.reset()  # must not raise or corrupt anything
    assert d.state is SwipeState.WAIT_BOOKEND


def test_fresh_attempt_after_reset_works_normally():
    # A reset mid-tracking must not leave any stale state that corrupts
    # a subsequent, unrelated attempt.
    d = make_detector(window_sec=0.5, min_net_displacement=0.05)
    d.on_bookend_confirmed("palm", x=0.1, y=0.1, now=0.0)
    d.on_position_update(0.9, 0.9)  # would have been a valid (if diagonal) attempt
    d.reset()  # abandoned before on_tick ever resolves it

    d.on_bookend_confirmed("two_up", x=0.5, y=0.3, now=10.0)
    d.on_position_update(0.5, 0.7)
    result = d.on_tick(now=10.5)
    assert result == "swipe_down"


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
