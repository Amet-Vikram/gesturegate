"""
video.py — Video producer thread and MediaPipe recognizer plumbing.
======================================================================

Single camera capture loop feeding up to TWO independently-gated
MediaPipe Gesture Recognizer instances:

    detect_event   → unlock-gate recognizer (stock gesture_recognizer.task)
    command_event  → command recognizer (custom HaGRID .task, optional)

One camera can only be opened by one cv2.VideoCapture handle at a time
on most platforms (especially Windows/DSHOW), so a single thread reads
each frame once and offers it to whichever gate(s) are currently set.
"""

from __future__ import annotations

import contextlib
import os
import queue
import sys
import threading
import time
import urllib.request

# Suppress MediaPipe's C++ backend INFO and WARNING messages.
os.environ.setdefault("GLOG_minloglevel", "3")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from config import VideoConfig
from landmark_extractor import extract_hand_position


def download_mediapipe_model(model_path: str, model_url: str) -> str:
    if not os.path.exists(model_path):
        print(f"Downloading MediaPipe Gesture Recognizer to {model_path}...")
        urllib.request.urlretrieve(model_url, model_path)
        print("Done.")
    return model_path


@contextlib.contextmanager
def _suppress_native_stderr():
    """LOG SUPPRESSION
    Temporarily redirects the OS-level stderr file descriptor (fd 2)
    to os.devnull.
    """
    sys.stderr.flush()
    saved_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, 2)
        yield
    finally:
        sys.stderr.flush()
        os.dup2(saved_fd, 2)
        os.close(devnull_fd)
        os.close(saved_fd)


def create_recognizer(model_path: str):
    """Gesture Recognizer in VIDEO mode."""
    options = mp_vision.GestureRecognizerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=model_path),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_hands=1,
    )
    with _suppress_native_stderr():
        return mp_vision.GestureRecognizer.create_from_options(options)


def top_gesture(result) -> tuple[str | None, float]:
    """Extracts (label, confidence) for the highest-scoring gesture, or
    (None, 0.0) when no hand / no known gesture is present."""
    if not result.gestures:
        return None, 0.0
    best = result.gestures[0][0]
    if best.category_name in ("", "None"):
        return None, 0.0
    return best.category_name, float(best.score)


class _RecognizerSlot:

    def __init__(self, model_path: str):
        self.model_path = model_path
        self.recognizer = None
        self.t0_ms = None
        self.last_ts_ms = -1
        # Raw result from the most recent classify() call, including
        # hand_landmarks. Additive only — top_gesture()'s (label, conf)
        # return from classify() is unchanged, so the unlock gate and
        # command layer are unaffected. Consumers that need landmark
        # position (e.g. a SwipeDetector) read this directly rather than
        # classify() growing a second return value on every caller.
        self.last_result = None

    def ensure_open(self):
        if self.recognizer is None:
            self.recognizer = create_recognizer(self.model_path)
            self.t0_ms = int(time.monotonic() * 1000)
            self.last_ts_ms = -1

    def close(self):
        if self.recognizer is not None:
            self.recognizer.close()
            self.recognizer = None

    def classify(self, rgb_frame: np.ndarray) -> tuple[str | None, float]:
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        ts_ms = int(time.monotonic() * 1000) - self.t0_ms
        if ts_ms <= self.last_ts_ms:

            ts_ms = self.last_ts_ms + 1
        self.last_ts_ms = ts_ms
        result = self.recognizer.recognize_for_video(mp_image, ts_ms)
        self.last_result = result
        return top_gesture(result)


def _open_capture(video_cfg: VideoConfig) -> cv2.VideoCapture:
    """Open a VideoCapture from either a local device index or a URL,
    based on whether video_cfg.camera_url is set.

    Local device: opens with the platform-appropriate backend (CAP_DSHOW
    on Windows, CAP_V4L2 on Linux — see config.py) and requests the
    configured resolution.

    Network stream: cv2.VideoCapture accepts HTTP MJPEG streams (e.g.
    http://<phone-ip>:8080/video from IP Webcam on Android) and RTSP
    streams identically to a device index.
    """
    if video_cfg.camera_url:
        cap = cv2.VideoCapture(video_cfg.camera_url)
    else:
        cap = cv2.VideoCapture(video_cfg.camera_index, video_cfg.camera_backend)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, video_cfg.requested_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, video_cfg.requested_height)
    return cap


def video_producer(
    video_cfg: VideoConfig,
    detect_event: threading.Event,
    video_queue: queue.Queue,
    command_event: threading.Event | None,
    command_video_queue: queue.Queue | None,
    stop_event: threading.Event,
    unlock_model_path: str,
    command_model_path: str | None,
    preview=None,
) -> None:
    """Idle for BOTH gates: read + discard frames (keeps camera warm,
    drains driver buffer). `detect_event` and `command_event` are
    independent and may be set simultaneously — each recognizer runs on
    every frame while its own gate is open, sharing only the raw camera
    read and the BGR→RGB conversion.
    """
    using_url = bool(video_cfg.camera_url)

    cap = _open_capture(video_cfg)
    if not cap.isOpened():
        source = video_cfg.camera_url or f"index {video_cfg.camera_index}"
        print(f"[video] cannot open {source}", file=sys.stderr)
        stop_event.set()
        return

    source_label = video_cfg.camera_url or f"index {video_cfg.camera_index}"
    print(f"[video] opened {'stream' if using_url else 'camera'}: {source_label}")

    unlock_slot = _RecognizerSlot(unlock_model_path)
    command_slot = _RecognizerSlot(command_model_path) if command_model_path else None

    # Consecutive read-failure counter for the network reconnect loop.
    _MAX_CONSECUTIVE_FAILURES = 10  # ~0.1s of failed reads before reconnect
    _RECONNECT_WAIT_SEC = 2.0
    consecutive_failures = 0

    try:
        while not stop_event.is_set():
            ok, frame = cap.read()

            if not ok:
                consecutive_failures += 1
                if using_url and consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    # Network stream dropped — attempt reconnect.
                    print(
                        f"[video] stream lost, reconnecting in "
                        f"{_RECONNECT_WAIT_SEC:.0f}s…",
                        file=sys.stderr,
                    )
                    cap.release()
                    time.sleep(_RECONNECT_WAIT_SEC)
                    cap = _open_capture(video_cfg)
                    if cap.isOpened():
                        print(f"[video] reconnected to {video_cfg.camera_url}")
                    else:
                        print("[video] reconnect failed, retrying…", file=sys.stderr)
                    consecutive_failures = 0
                else:
                    time.sleep(0.01)
                continue

            consecutive_failures = 0  # reset on any successful read

            if preview is not None:
                preview.put_frame(frame)

            unlock_active = detect_event.is_set()
            command_active = command_event is not None and command_event.is_set()

            if not unlock_active:
                unlock_slot.close()
            if command_slot is not None and not command_active:
                command_slot.close()

            if not unlock_active and not command_active:
                continue  # nothing gated on — pure idle, stream stays drained

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            now_ts = time.monotonic()

            if unlock_active:
                unlock_slot.ensure_open()
                label, conf = unlock_slot.classify(rgb)
                video_queue.put((label, conf, now_ts))

            if command_active and command_slot is not None:
                command_slot.ensure_open()
                label, conf = command_slot.classify(rgb)
                pos = extract_hand_position(command_slot.last_result)
                wrist_x = pos.wrist_x if pos is not None else None
                wrist_y = pos.wrist_y if pos is not None else None
                command_video_queue.put((label, conf, now_ts, wrist_x, wrist_y))
    finally:
        unlock_slot.close()
        if command_slot is not None:
            command_slot.close()
        cap.release()


def list_camera_devices(video_cfg: VideoConfig, max_index: int = 5) -> None:
    """CLI helper: probe camera indices 0..max_index-1 and print which
    ones open successfully, with the resolution actually negotiated."""
    if video_cfg.camera_url:
        print(f"Network stream configured: {video_cfg.camera_url}")
        print("(Local camera device probe skipped — URL will be tested at startup)")
        return
    for idx in range(max_index):
        cap = cv2.VideoCapture(idx, video_cfg.camera_backend)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok:
                h, w = frame.shape[:2]
                print(f"Index {idx}: available ({w}x{h})")
        cap.release()
