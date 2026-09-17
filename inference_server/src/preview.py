"""
preview.py — Live debug overlay (--preview flag).
====================================================

PreviewState is written by the orchestrator every frame and read by
preview_loop, which must run on the main thread (cv2.imshow is not
thread-safe with the Win32 backend).
"""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np


class PreviewState:
    """Container for values the orchestrator writes each frame and the
    preview loop reads for rendering. A threading.Lock protects the
    frame buffer; scalar fields are GIL-safe in CPython."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None

        # Unlock-gate recognizer
        self.label: str | None = None
        self.conf: float = 0.0
        self.streak: int = 0
        self.state: str = "IDLE"
        self.rms: float = 0.0
        self.flash: float = 0.0

        # Command layer (only meaningful when session_state != "N/A")
        self.session_state: str = "N/A"
        self.cmd_raw_label: str | None = None
        self.cmd_raw_conf: float = 0.0
        self.cmd_norm_label: str | None = None
        self.cmd_streak: int = 0
        self.cmd_flash: float = 0.0

        # HUD-only fields (--hud).
        self.last_unlock_label: str | None = None  # e.g. "Open_Palm"
        self.last_command_label: str | None = None  # e.g. "ok" or "swipe_right"
        # Seconds remaining until idle auto-lock, or None when locked /
        # legacy mode / idle auto-lock disabled. Computed by the
        # orchestrator from SessionConfig.idle_timeout_sec.
        self.idle_remaining: float | None = None

    def put_frame(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame.copy()

    def get_frame(self) -> np.ndarray | None:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None


_COL = {
    "idle": (120, 120, 120),
    "wait": (200, 160, 0),
    "confirm": (50, 200, 80),
    "locked": (100, 100, 100),
    "unlocked": (50, 200, 80),
    "bar_bg": (40, 40, 40),
    "bar_fg": (50, 200, 80),
    "cmd_bar": (60, 180, 220),
    "rms_fg": (60, 180, 220),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
}


def _draw_overlay(
    canvas: np.ndarray,
    ps: PreviewState,
    n_confirm: int,
    cmd_n_confirm: int,
    unlock_min_confidence: float,
    rms_threshold: float,
) -> None:
    """Mutates canvas in-place. All rendering logic lives here so the
    rest of the code stays uncluttered."""
    h, w = canvas.shape[:2]
    now = time.monotonic()
    session_active = ps.session_state != "N/A"

    panel_h = 150 if session_active else 110
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, 0), (w, panel_h), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.55, canvas, 0.45, 0, canvas)

    state_name = ps.state
    state_col = _COL["wait"] if state_name != "IDLE" else _COL["idle"]
    if state_name == "COOLDOWN":
        state_col = (80, 80, 200)
    cv2.putText(
        canvas,
        f"Gate: {state_name}",
        (16, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        state_col,
        2,
        cv2.LINE_AA,
    )

    label_str = ps.label if ps.label is not None else "—"
    conf_str = f"{ps.conf:.2f}" if ps.label is not None else "—"
    label_col = _COL["confirm"] if ps.conf >= unlock_min_confidence else _COL["idle"]
    cv2.putText(
        canvas,
        f"Label: {label_str:<14} Conf: {conf_str}",
        (16, 58),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        label_col,
        1,
        cv2.LINE_AA,
    )

    bar_x, bar_y, bar_w, bar_h = 16, 70, 220, 14
    fill = int(bar_w * min(ps.streak, n_confirm) / n_confirm)
    cv2.rectangle(
        canvas, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), _COL["bar_bg"], -1
    )
    if fill > 0:
        cv2.rectangle(
            canvas, (bar_x, bar_y), (bar_x + fill, bar_y + bar_h), _COL["bar_fg"], -1
        )
    cv2.rectangle(
        canvas, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), _COL["white"], 1
    )
    cv2.putText(
        canvas,
        f"{ps.streak}/{n_confirm}",
        (bar_x + bar_w + 8, bar_y + 11),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        _COL["white"],
        1,
        cv2.LINE_AA,
    )

    rms_bar_w, rms_bar_h = 160, 14
    rms_x, rms_y = w - rms_bar_w - 16, 16
    rms_fill = int(rms_bar_w * min(ps.rms / 0.30, 1.0))
    cv2.rectangle(
        canvas,
        (rms_x, rms_y),
        (rms_x + rms_bar_w, rms_y + rms_bar_h),
        _COL["bar_bg"],
        -1,
    )
    if rms_fill > 0:
        cv2.rectangle(
            canvas,
            (rms_x, rms_y),
            (rms_x + rms_fill, rms_y + rms_bar_h),
            _COL["rms_fg"],
            -1,
        )
    cv2.rectangle(
        canvas, (rms_x, rms_y), (rms_x + rms_bar_w, rms_y + rms_bar_h), _COL["white"], 1
    )
    thresh_x = rms_x + int(rms_bar_w * rms_threshold / 0.30)
    cv2.line(
        canvas,
        (thresh_x, rms_y - 3),
        (thresh_x, rms_y + rms_bar_h + 3),
        (0, 80, 255),
        2,
    )
    cv2.putText(
        canvas,
        f"RMS {ps.rms:.3f}",
        (rms_x, rms_y + rms_bar_h + 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        _COL["rms_fg"],
        1,
        cv2.LINE_AA,
    )

    if session_active:
        sess_col = _COL["locked"] if ps.session_state == "LOCKED" else _COL["unlocked"]
        cv2.putText(
            canvas,
            f"Session: {ps.session_state}",
            (16, 100),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            sess_col,
            2,
            cv2.LINE_AA,
        )

        cmd_raw = ps.cmd_raw_label if ps.cmd_raw_label is not None else "—"
        cmd_norm = ps.cmd_norm_label if ps.cmd_norm_label is not None else "—"
        dropped = ps.cmd_raw_label is not None and ps.cmd_norm_label is None
        if dropped:
            cmd_str, cmd_col = f"raw={cmd_raw}  →  DROPPED", (60, 60, 220)
        elif ps.cmd_norm_label is not None and ps.cmd_norm_label != ps.cmd_raw_label:
            cmd_str, cmd_col = f"raw={cmd_raw}  →  {cmd_norm}", _COL["cmd_bar"]
        else:
            cmd_str = f"{cmd_norm} ({ps.cmd_raw_conf:.2f})"
            cmd_col = _COL["confirm"] if ps.cmd_norm_label else _COL["idle"]
        cv2.putText(
            canvas,
            f"Cmd: {cmd_str}",
            (16, 126),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            cmd_col,
            1,
            cv2.LINE_AA,
        )

        cbar_x, cbar_y, cbar_w, cbar_h = 16, 136, 220, 12
        cfill = int(cbar_w * min(ps.cmd_streak, cmd_n_confirm) / cmd_n_confirm)
        cv2.rectangle(
            canvas,
            (cbar_x, cbar_y),
            (cbar_x + cbar_w, cbar_y + cbar_h),
            _COL["bar_bg"],
            -1,
        )
        if cfill > 0:
            cv2.rectangle(
                canvas,
                (cbar_x, cbar_y),
                (cbar_x + cfill, cbar_y + cbar_h),
                _COL["cmd_bar"],
                -1,
            )
        cv2.rectangle(
            canvas,
            (cbar_x, cbar_y),
            (cbar_x + cbar_w, cbar_y + cbar_h),
            _COL["white"],
            1,
        )
        cv2.putText(
            canvas,
            f"{ps.cmd_streak}/{cmd_n_confirm}",
            (cbar_x + cbar_w + 8, cbar_y + 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            _COL["white"],
            1,
            cv2.LINE_AA,
        )

    flash_duration = 0.4
    if (now - ps.flash) < flash_duration:
        alpha = 1.0 - (now - ps.flash) / flash_duration
        banner = canvas.copy()
        cv2.rectangle(banner, (0, h - 60), (w, h), (30, 180, 60), -1)
        cv2.putText(
            banner,
            "  CONFIRMED!",
            (w // 2 - 120, h - 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.1,
            _COL["white"],
            2,
            cv2.LINE_AA,
        )
        cv2.addWeighted(banner, alpha, canvas, 1 - alpha, 0, canvas)

    if session_active and (now - ps.cmd_flash) < flash_duration:
        alpha = 1.0 - (now - ps.cmd_flash) / flash_duration
        banner = canvas.copy()
        cv2.rectangle(banner, (0, 0), (w, 8), (0, 165, 255), -1)
        cv2.addWeighted(banner, alpha, canvas, 1 - alpha, 0, canvas)

    cv2.putText(
        canvas,
        "Q  quit preview",
        (16, h - 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (160, 160, 160),
        1,
        cv2.LINE_AA,
    )


def _draw_progress_bar(
    canvas: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    value: int,
    maximum: int,
    color: tuple[int, int, int],
) -> None:
    """Shared by _draw_overlay's inline bars and _draw_hud — small enough
    that factoring it out mainly avoids repeating the same six lines."""
    cv2.rectangle(canvas, (x, y), (x + w, y + h), _COL["bar_bg"], -1)
    fill = int(w * min(value, maximum) / maximum) if maximum > 0 else 0
    if fill > 0:
        cv2.rectangle(canvas, (x, y), (x + fill, y + h), color, -1)
    cv2.rectangle(canvas, (x, y), (x + w, y + h), _COL["white"], 1)


_HUD_STATE_COLOR = {
    "LOCKED": (90, 90, 90),
    "STATIC_UNLOCKED": (60, 180, 100),
    "DYNAMIC_UNLOCKED": (20, 130, 210),  # BGR — amber/orange
    "N/A": (70, 70, 70),
}
_HUD_STATE_TITLE = {
    "LOCKED": "LOCKED",
    "STATIC_UNLOCKED": "STATIC COMMANDS",
    "DYNAMIC_UNLOCKED": "SWIPE MODE",
    "N/A": "READY",
}

HUD_WIDTH = 420
HUD_HEIGHT = 460


def _draw_hud(
    ps: PreviewState,
    n_confirm: int,
    cmd_n_confirm: int,
) -> np.ndarray:
    """Renders a clean, camera-free status panel — a UX indicator for an
    end user tracking what the system is doing, not a developer debug
    dump.
    """
    canvas = np.full((HUD_HEIGHT, HUD_WIDTH, 3), (25, 25, 25), dtype=np.uint8)
    now = time.monotonic()
    state_key = ps.session_state
    font = cv2.FONT_HERSHEY_SIMPLEX

    # Big state badge
    badge_col = _HUD_STATE_COLOR.get(state_key, _HUD_STATE_COLOR["N/A"])
    badge_title = _HUD_STATE_TITLE.get(state_key, state_key)
    cv2.rectangle(canvas, (16, 16), (HUD_WIDTH - 16, 84), badge_col, -1)
    (tw, th), _ = cv2.getTextSize(badge_title, font, 0.95, 2)
    cv2.putText(
        canvas,
        badge_title,
        ((HUD_WIDTH - tw) // 2, 16 + (84 - 16 + th) // 2),
        font,
        0.95,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    y = 116

    if state_key in ("LOCKED", "N/A"):
        cv2.putText(
            canvas,
            "Perform an unlock sequence:",
            (20, y),
            font,
            0.5,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        y += 26
        cv2.putText(
            canvas,
            "clap -> open palm -> fist",
            (20, y),
            font,
            0.45,
            (150, 150, 150),
            1,
            cv2.LINE_AA,
        )
        y += 22
        if state_key == "LOCKED":  # command model present -> dynamic gate exists
            cv2.putText(
                canvas,
                "or   clap -> peace sign -> fist  (swipes)",
                (20, y),
                font,
                0.45,
                (150, 150, 150),
                1,
                cv2.LINE_AA,
            )
            y += 22
        y += 14

        label = ps.label if ps.label is not None else "-"
        cv2.putText(
            canvas,
            f"Seeing: {label}  ({ps.conf:.2f})",
            (20, y),
            font,
            0.55,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
        y += 16
        _draw_progress_bar(
            canvas, 20, y, HUD_WIDTH - 40, 18, ps.streak, n_confirm, (60, 180, 220)
        )
        y += 44

    elif state_key == "STATIC_UNLOCKED":
        cv2.putText(
            canvas,
            "Command vocabulary active",
            (20, y),
            font,
            0.5,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        y += 34
        cmd = ps.cmd_norm_label or ps.cmd_raw_label or "-"
        cv2.putText(
            canvas,
            f"Seeing: {cmd}  ({ps.cmd_raw_conf:.2f})",
            (20, y),
            font,
            0.55,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
        y += 16
        _draw_progress_bar(
            canvas,
            20,
            y,
            HUD_WIDTH - 40,
            18,
            ps.cmd_streak,
            cmd_n_confirm,
            (60, 180, 220),
        )
        y += 44

    elif state_key == "DYNAMIC_UNLOCKED":
        tracking = ps.cmd_norm_label == "tracking..."
        if tracking:
            cv2.putText(
                canvas,
                "Tracking swipe motion...",
                (20, y),
                font,
                0.58,
                (0, 165, 255),
                2,
                cv2.LINE_AA,
            )
            y += 34
        else:
            cv2.putText(
                canvas,
                "Show a bookend pose:",
                (20, y),
                font,
                0.5,
                (200, 200, 200),
                1,
                cv2.LINE_AA,
            )
            y += 24
            cv2.putText(
                canvas,
                "palm (swipe left/right)  or  two-up (swipe up/down)",
                (20, y),
                font,
                0.42,
                (150, 150, 150),
                1,
                cv2.LINE_AA,
            )
            y += 34

    # Idle countdown (only while a gate is genuinely open)
    if ps.idle_remaining is not None and state_key not in ("LOCKED", "N/A"):
        secs = max(0, int(ps.idle_remaining))
        cv2.putText(
            canvas,
            f"Auto-lock in {secs}s",
            (20, HUD_HEIGHT - 60),
            font,
            0.45,
            (140, 140, 140),
            1,
            cv2.LINE_AA,
        )

    # Toast: most recent confirmation, whichever happened last
    flash_duration = 1.2
    toast_at, toast_text = None, None
    if (now - ps.flash) < flash_duration and ps.last_unlock_label:
        toast_at, toast_text = ps.flash, f"Unlock: {ps.last_unlock_label}"
    if (now - ps.cmd_flash) < flash_duration and ps.last_command_label:
        if toast_at is None or ps.cmd_flash > toast_at:
            toast_at, toast_text = ps.cmd_flash, f"Confirmed: {ps.last_command_label}"

    if toast_at is not None:
        alpha = min(1.0, 1.0 - (now - toast_at) / flash_duration)
        banner = canvas.copy()
        cv2.rectangle(
            banner, (0, HUD_HEIGHT - 40), (HUD_WIDTH, HUD_HEIGHT), (40, 160, 60), -1
        )
        (tw, th), _ = cv2.getTextSize(toast_text, font, 0.6, 2)
        cv2.putText(
            banner,
            toast_text,
            ((HUD_WIDTH - tw) // 2, HUD_HEIGHT - 12),
            font,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.addWeighted(banner, alpha, canvas, 1 - alpha, 0, canvas)

    cv2.putText(
        canvas,
        "Q  quit",
        (20, HUD_HEIGHT - 12),
        font,
        0.4,
        (110, 110, 110),
        1,
        cv2.LINE_AA,
    )

    return canvas


def preview_loop(
    ps: PreviewState,
    stop_event: threading.Event,
    n_confirm: int,
    cmd_n_confirm: int,
    unlock_min_confidence: float,
    rms_threshold: float,
    show_camera: bool = True,
    show_hud: bool = False,
) -> None:
    """Drives the OpenCV window(s). Must be called from the main thread
    on Windows. Exits when the user presses Q or stop_event is set.

    show_camera and show_hud are independent — either or both may be
    True. Both windows (when both requested) are driven from this same
    loop/thread rather than separate threads, since cv2's HighGUI event
    loop is not safe to drive concurrently from multiple threads on the
    Win32 backend. The HUD renders every iteration regardless of camera
    frame availability — it never depends on ps.get_frame() — so
    `--hud` alone works even before the first camera frame arrives.
    """
    camera_win = "Gesture debug preview  (Q to quit)"
    hud_win = "Gesture status  (Q to quit)"

    if show_camera:
        cv2.namedWindow(camera_win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(camera_win, 960, 540)
    if show_hud:
        cv2.namedWindow(hud_win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(hud_win, HUD_WIDTH, HUD_HEIGHT)

    while not stop_event.is_set():
        frame = ps.get_frame() if show_camera else None

        if show_camera and frame is not None:
            _draw_overlay(
                frame,
                ps,
                n_confirm,
                cmd_n_confirm,
                unlock_min_confidence,
                rms_threshold,
            )
            cv2.imshow(camera_win, frame)

        if show_hud:
            cv2.imshow(hud_win, _draw_hud(ps, n_confirm, cmd_n_confirm))

        # Always call waitKey once per iteration (even if nothing new was
        # drawn this tick) so Q remains responsive from the first
        # iteration onward
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            stop_event.set()
            break

        if show_camera and frame is None:
            time.sleep(0.01)  # camera enabled but no frame yet; avoid a spin loop

    cv2.destroyAllWindows()
