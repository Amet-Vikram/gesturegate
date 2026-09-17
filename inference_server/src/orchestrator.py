"""
orchestrator.py — the main loop.
===================================

Wires together everything else: starts the audio and video producer
threads, feeds their output through the detection layers (detection.py,
session.py, command_label_normalizer.py, swipe_detector.py — all pure
logic, untouched or independently tested), and executes the resulting
Actions/SessionActions (opening/closing detection windows, publishing to
the relay).
"""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timezone

from config import AppConfig
from detection import Action, GestureDebouncer, SequenceStateMachine, State
from session import SessionAction, SessionStateMachine, UnlockTarget, Session
from command_label_normalizer import normalize_command_label
from swipe_detector import SwipeDetector

from audio import audio_producer
from video import download_mediapipe_model, video_producer
from relay import RelayPublisher
from preview import PreviewState

# Maps the internal Session enum to the "gate" string sent on the wire in
# session_unlocked/session_locked payloads.
_SESSION_TO_GATE: dict[Session, str] = {
    Session.STATIC_UNLOCKED: "static",
    Session.DYNAMIC_UNLOCKED: "dynamic",
}


def run(
    cfg: AppConfig,
    relay: RelayPublisher,
    preview: PreviewState | None,
) -> None:
    audio_queue: queue.Queue = queue.Queue()
    video_queue: queue.Queue = queue.Queue()
    detect_event = threading.Event()
    stop_event = threading.Event()

    command_video_queue: queue.Queue | None = None
    command_event: threading.Event | None = None
    command_debouncer: GestureDebouncer | None = None
    session: SessionStateMachine | None = None
    dynamic_fsm: SequenceStateMachine | None = None
    swipe_detector: SwipeDetector | None = None

    if cfg.command_model_path is not None:
        command_video_queue = queue.Queue()
        command_event = threading.Event()
        command_debouncer = GestureDebouncer(cfg.command_debounce)
        session = SessionStateMachine(cfg.session)
        dynamic_fsm = SequenceStateMachine(cfg.dynamic_sequence)
        swipe_detector = SwipeDetector(cfg.swipe)

    debouncer = GestureDebouncer(cfg.debounce)
    static_fsm = SequenceStateMachine(cfg.sequence)

    # Tracks the confidence at which each unlock gesture was last
    # confirmed.
    _last_unlock_conf: dict[str, float] = {}

    # Monotonic deadline before which command/bookend frames are read and
    # discarded without being fed to command_debouncer at all.
    command_accept_after = [0.0]

    # Tracks whether the derived unlock-gate window is currently "open"
    # (detect_event set), so sync_unlock_window() only acts on actual
    # transitions rather than re-opening/re-resetting every iteration.
    _window_open = [False]

    def drain(q: queue.Queue) -> None:
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    def active_unlock_fsms() -> list[SequenceStateMachine]:
        if session is None:
            return [static_fsm]
        if session.state is Session.LOCKED:
            return [static_fsm, dynamic_fsm]
        if session.state is Session.STATIC_UNLOCKED:
            return [static_fsm]
        return [dynamic_fsm]

    def sync_unlock_window() -> None:
        """Derive detect_event/debouncer state from whichever FSM(s) are
        currently eligible, rather than reacting to individual FSMs'
        OPEN_WINDOW/CLOSE_WINDOW actions."""
        eligible = active_unlock_fsms()
        should_be_open = any(
            f.state not in (State.IDLE, State.COOLDOWN) for f in eligible
        )
        if should_be_open and not _window_open[0]:
            debouncer.reset()
            drain(video_queue)
            detect_event.set()
            _window_open[0] = True
        elif not should_be_open and _window_open[0]:
            detect_event.clear()
            debouncer.reset()
            _window_open[0] = False

    # session action execution

    def execute_session_actions(actions: list[SessionAction]) -> None:
        for action in actions:
            if action is SessionAction.ENABLE_COMMANDS:
                command_debouncer.reset()
                drain(command_video_queue)
                # The hand is almost certainly STILL holding the unlock
                # sequence's final gesture at the instant the session
                # opens (both sequences end on Closed_Fist -> "fist").
                # Pre-latch so it must be released before it can confirm
                # as a command OR as a swipe bookend.
                command_debouncer._latched = cfg.unlock_tail_as_command
                command_accept_after[0] = (
                    time.monotonic() + cfg.command_transition_cooldown_sec
                )
                command_event.set()
                gate_name = (
                    "STATIC"
                    if session.active_target is UnlockTarget.STATIC
                    else "DYNAMIC"
                )
                print(
                    f"  UNLOCKED [{gate_name}] "
                    f"(settling for {cfg.command_transition_cooldown_sec:.1f}s)"
                )
            elif action is SessionAction.DISABLE_COMMANDS:
                command_event.clear()
                command_debouncer.reset()
                # Abandon any in-progress swipe attempt rather than
                # leaving SwipeDetector stranded in TRACKING with a
                # start position from a session that no longer exists.
                if swipe_detector is not None:
                    swipe_detector.reset()
                print("  LOCKED -- commands inactive")
            elif action is SessionAction.PUBLISH_UNLOCK:
                gate = _SESSION_TO_GATE[session.history[-1].dst]
                relay.publish(
                    {
                        "schema_version": 2,
                        "event": "session_unlocked",
                        "source": "sequence_state_machine",
                        "gate": gate,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                print(f"  [session] UNLOCKED ({gate}) -> relay")
            elif action is SessionAction.PUBLISH_LOCK:
                last_transition = session.history[-1] if session.history else None
                gate = (
                    _SESSION_TO_GATE.get(last_transition.src, "static")
                    if last_transition
                    else "static"
                )
                last_cause = (
                    last_transition.cause if last_transition else "lock_sequence"
                )
                reason = (
                    "idle_timeout" if last_cause == "idle_timeout" else "user_sequence"
                )
                relay.publish(
                    {
                        "schema_version": 2,
                        "event": "session_locked",
                        "source": "sequence_state_machine",
                        "gate": gate,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "reason": reason,
                    }
                )
                print(f"  [session] LOCKED ({gate}, {reason}) -> relay")

    # unlock FSM action execution

    def handle_unlock_actions(actions: list[Action], target: UnlockTarget) -> None:
        """Only PUBLISH_EVENT is handled here -- OPEN_WINDOW/CLOSE_WINDOW
        are intentionally ignored in favour of sync_unlock_window()'s
        derived approach (see module docstring)."""
        for action in actions:
            if action is Action.PUBLISH_EVENT:
                if session is not None:
                    execute_session_actions(
                        session.on_unlock_sequence(target, time.monotonic())
                    )
                elif target is UnlockTarget.STATIC:
                    payload = {
                        "schema_version": 2,
                        "event": "gesture_sequence_detected",
                        "source": "mediapipe_gesture_recognizer_v1",
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "sequence": [cfg.sequence.gesture_1, cfg.sequence.gesture_2],
                        "confidences": [
                            _last_unlock_conf.get(
                                cfg.sequence.gesture_1, cfg.debounce.min_confidence
                            ),
                            _last_unlock_conf.get(
                                cfg.sequence.gesture_2, cfg.debounce.min_confidence
                            ),
                        ],
                        "model_vocab": "mediapipe_canned",
                    }
                    print(f"\n{'-' * 50}\n  SEQUENCE COMPLETE -> {payload}\n{'-' * 50}")
                    relay.publish(payload)

    threads = [
        threading.Thread(
            target=audio_producer,
            args=(cfg.audio, audio_queue, stop_event, preview),
            daemon=True,
        ),
        threading.Thread(
            target=video_producer,
            args=(
                cfg.video,
                detect_event,
                video_queue,
                command_event,
                command_video_queue,
                stop_event,
                cfg.unlock_model_path,
                cfg.command_model_path,
                preview,
            ),
            daemon=True,
        ),
    ]
    for t in threads:
        t.start()

    print(f"\n{'=' * 50}\nLIVE INFERENCE ACTIVE (v2)\n{'=' * 50}")
    print(
        f"Static unlock:  CLAP -> {cfg.sequence.gesture_1} -> {cfg.sequence.gesture_2}"
    )
    print(
        f"Clap threshold: RMS > {cfg.audio.rms_threshold}   Debounce: "
        f"{cfg.debounce.n_confirm} frames @ >={cfg.debounce.min_confidence}"
    )
    if session is not None:
        print(
            f"Dynamic unlock: CLAP -> {cfg.dynamic_sequence.gesture_1} -> "
            f"{cfg.dynamic_sequence.gesture_2}"
        )
        print(f"Command layer: ACTIVE ({cfg.command_model_path})")
        print(
            f"  Debounce: {cfg.command_debounce.n_confirm} frames @ "
            f">={cfg.command_debounce.min_confidence}   "
            f"Idle auto-lock: {cfg.session.idle_timeout_sec}s   "
            f"Transition cooldown: {cfg.command_transition_cooldown_sec}s"
        )
        print(
            f"  SwipeDetector: window={cfg.swipe.window_sec}s  "
            f"min_displacement={cfg.swipe.min_net_displacement}"
        )
        print(
            "  Static and dynamic gates are mutually exclusive; only one "
            "can be open at a time."
        )
    else:
        print("Command layer: inactive (no --command-model supplied)")
    if preview is not None:
        print("Preview window open -- press Q inside it to quit.")
    print("Press Ctrl+C to stop.\n")

    relay.connect_eager()

    last_static_state = static_fsm.state
    last_dynamic_state = dynamic_fsm.state if dynamic_fsm is not None else None
    last_session_state = session.state if session is not None else None

    try:
        while not stop_event.is_set():
            now_mono = time.monotonic()
            eligible_fsms = active_unlock_fsms()

            # 1. Clap events -> whichever unlock FSM(s) are eligible
            try:
                _buffer, clap_ts = audio_queue.get_nowait()
                for fsm in eligible_fsms:
                    target = (
                        UnlockTarget.STATIC
                        if fsm is static_fsm
                        else UnlockTarget.DYNAMIC
                    )
                    handle_unlock_actions(fsm.on_clap(clap_ts), target)
            except queue.Empty:
                pass

            # 2. Unlock-gate frames -> shared debouncer -> whichever
            #    unlock FSM(s) are eligible. static_fsm is checked BEFORE
            #    dynamic_fsm on every confirmed label -- this ordering is
            #    what makes the same-iteration double-completion race
            #    resolve correctly.
            try:
                label, conf, frame_ts = video_queue.get(timeout=0.02)
                confirmed = debouncer.update(label, conf)

                if preview is not None:
                    preview.label = label
                    preview.conf = conf
                    preview.streak = debouncer._streak
                    preview.state = static_fsm.state.value
                    if confirmed is not None:
                        preview.flash = time.monotonic()
                        preview.last_unlock_label = confirmed

                if confirmed is not None:
                    _last_unlock_conf[confirmed] = conf
                    print(f"  [confirmed] {confirmed}")
                    for fsm in eligible_fsms:
                        target = (
                            UnlockTarget.STATIC
                            if fsm is static_fsm
                            else UnlockTarget.DYNAMIC
                        )
                        handle_unlock_actions(
                            fsm.on_gesture_confirmed(confirmed, frame_ts), target
                        )

            except queue.Empty:
                if preview is not None:
                    preview.label = None
                    preview.conf = 0.0
                    preview.streak = 0
                    preview.state = static_fsm.state.value

            # 3. Command/bookend-vocabulary frames (only when session
            #    layer active). Branches on which gate is open.
            if session is not None:
                try:
                    raw_label, raw_conf, cmd_ts, wrist_x, wrist_y = (
                        command_video_queue.get_nowait()
                    )
                    in_transition = time.monotonic() < command_accept_after[0]

                    if session.is_static_unlocked and not in_transition:
                        # ---- STATIC: single-gesture HaGRID commands ----
                        norm_label, norm_conf = normalize_command_label(
                            raw_label, raw_conf
                        )
                        cmd_confirmed = command_debouncer.update(norm_label, norm_conf)

                        if preview is not None:
                            preview.cmd_raw_label = raw_label
                            preview.cmd_raw_conf = raw_conf
                            preview.cmd_norm_label = norm_label
                            preview.cmd_streak = command_debouncer._streak

                        if cmd_confirmed is not None:
                            session.on_command_activity(cmd_ts)
                            payload = {
                                "schema_version": 2,
                                "event": "gesture_sequence_detected",
                                "source": "hagrid_custom_v1",
                                "detected_at": datetime.now(timezone.utc).isoformat(),
                                "sequence": [cmd_confirmed],
                                "confidences": [norm_conf],
                                "model_vocab": "hagrid_30k_v1",
                            }
                            print(
                                f"  [command] {cmd_confirmed}, time = {now_mono:.2f}, confidence = {norm_conf:.2f}"
                            )
                            relay.publish(payload)
                            if preview is not None:
                                preview.cmd_flash = time.monotonic()
                                preview.last_command_label = cmd_confirmed

                    elif session.is_dynamic_unlocked and not in_transition:
                        # ---- DYNAMIC: bookend confirm + swipe tracking ----
                        norm_label, norm_conf = normalize_command_label(
                            raw_label, raw_conf
                        )
                        confirmed_bookend = command_debouncer.update(
                            norm_label, norm_conf
                        )

                        if (
                            confirmed_bookend is not None
                            and wrist_x is not None
                            and wrist_y is not None
                        ):
                            swipe_detector.on_bookend_confirmed(
                                confirmed_bookend, wrist_x, wrist_y, cmd_ts
                            )

                        if (
                            swipe_detector.is_tracking
                            and wrist_x is not None
                            and wrist_y is not None
                        ):
                            swipe_detector.on_position_update(wrist_x, wrist_y)

                        if preview is not None:
                            preview.cmd_raw_label = raw_label
                            preview.cmd_raw_conf = raw_conf
                            preview.cmd_norm_label = (
                                "tracking..."
                                if swipe_detector.is_tracking
                                else norm_label
                            )
                            preview.cmd_streak = command_debouncer._streak

                    else:
                        # LOCKED (shouldn't happen -- no queue producer)
                        # or within the post-unlock transition cooldown.
                        if preview is not None:
                            preview.cmd_raw_label = raw_label
                            preview.cmd_norm_label = (
                                "settling..." if session.is_unlocked else None
                            )
                            preview.cmd_raw_conf = raw_conf

                except queue.Empty:
                    pass

                # SwipeDetector's window resolves purely by elapsed time,
                # so on_tick() must run every iteration regardless of
                # whether a command_video_queue item was available.
                if session.is_dynamic_unlocked:
                    swipe_direction = swipe_detector.on_tick(time.monotonic())
                    if swipe_direction is not None:
                        session.on_command_activity(time.monotonic())
                        payload = {
                            "schema_version": 2,
                            "event": "gesture_sequence_detected",
                            "source": "swipe_detector_v1",
                            "detected_at": datetime.now(timezone.utc).isoformat(),
                            "sequence": [swipe_direction],
                            "confidences": [1.0],  # threshold detector; no
                            # smooth confidence score
                            "model_vocab": "swipe_v1",
                        }
                        print(f"  [swipe] {swipe_direction}")
                        relay.publish(payload)
                        if preview is not None:
                            preview.cmd_flash = time.monotonic()
                            preview.last_command_label = swipe_direction

                # Session idle-timeout auto-lock. Uses monotonic time to
                # stay consistent with on_unlock_sequence() and
                # on_command_activity() above.
                execute_session_actions(session.on_tick(time.monotonic()))

                if preview is not None:
                    preview.session_state = session.state.value
                    if (
                        session.state is not Session.LOCKED
                        and cfg.session.idle_timeout_sec > 0
                    ):
                        # Reaches into SessionStateMachine's private
                        # _last_activity_at for display purposes only —
                        # same established pattern as reading
                        # command_debouncer._streak/_latched elsewhere in
                        # this file.
                        elapsed = time.monotonic() - session._last_activity_at
                        preview.idle_remaining = max(
                            0.0, cfg.session.idle_timeout_sec - elapsed
                        )
                    else:
                        preview.idle_remaining = None

                if session.state is not last_session_state:
                    last_session_state = session.state

            # 4. Timeouts / cooldown expiry for whichever FSM(s) are
            #    eligible, then derive the unlock-gate window from the
            #    resulting states.
            for fsm in active_unlock_fsms():
                target = (
                    UnlockTarget.STATIC if fsm is static_fsm else UnlockTarget.DYNAMIC
                )
                handle_unlock_actions(fsm.on_tick(now_mono), target)
            sync_unlock_window()

            if static_fsm.state is not last_static_state:
                print(f"[static:{static_fsm.state.value}] time: {now_mono:.2f}")
                last_static_state = static_fsm.state
            if dynamic_fsm is not None and dynamic_fsm.state is not last_dynamic_state:
                print(f"[dynamic:{dynamic_fsm.state.value}] time: {now_mono:.2f}")
                last_dynamic_state = dynamic_fsm.state

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        stop_event.set()
        detect_event.clear()
        if command_event is not None:
            command_event.clear()
        for t in threads:
            t.join(timeout=2.0)
        relay.close()
        print("Live inference stopped.")
