"""
config.py — centralized configuration for the inference server.
=================================================================

Everything tunable lives here as dataclasses, built once into a single
AppConfig and passed explicitly down the call chain (entry point →
orchestrator → audio/video producers → preview).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import cv2

from detection import DebouncerConfig, SequenceConfig
from session import SessionConfig
from swipe_detector import SwipeConfig

MEDIAPIPE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/1/gesture_recognizer.task"
)


def _default_camera_backend() -> int:
    """CAP_DSHOW is a Windows-only DirectShow backend — cv2.VideoCapture
    silently fails to open ANY camera with it on Linux (the constant
    still exists and importing it never errors, but opening a device
    with the wrong backend just returns isOpened()==False). This matters
    for Docker: a Linux container running this exact same code needs
    CAP_V4L2 instead, or camera passthrough will appear to "not work"
    for a backend reason that has nothing to do with device access."""
    if sys.platform.startswith("win"):
        return cv2.CAP_DSHOW
    if sys.platform.startswith("linux"):
        return cv2.CAP_V4L2
    return cv2.CAP_ANY


@dataclass
class AudioConfig:
    sample_rate: int = 44100
    channels: int = 1
    device_index: int = 1
    clap_window_sec: float = 1.0
    rms_threshold: float = 0.08
    chunk_size: int = 1024
    retrigger_guard_sec: float = 0.5  # local debounce on the clap trigger itself


@dataclass
class VideoConfig:
    camera_index: int = 1
    camera_backend: int = field(default_factory=_default_camera_backend)
    requested_width: int = 1280
    requested_height: int = 720

    # When set, the video producer opens this URL instead of a local
    # device index. cv2.VideoCapture accepts URLs directly — MJPEG HTTP
    # streams (e.g. IP Webcam on Android: http://<phone-ip>:8080/video)
    # and RTSP streams both work. When camera_url is set, camera_index
    # and camera_backend are both ignored.
    camera_url: str | None = None


@dataclass
class AppConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)
    video: VideoConfig = field(default_factory=VideoConfig)

    unlock_model_path: str = "gesture_recognizer.task"
    unlock_model_url: str = MEDIAPIPE_MODEL_URL
    command_model_path: str | None = None

    # Unlock-gate detection tuning
    debounce: DebouncerConfig = field(
        default_factory=lambda: DebouncerConfig(
            n_confirm=5, n_release=3, min_confidence=0.60
        )
    )
    sequence: SequenceConfig = field(
        default_factory=lambda: SequenceConfig(
            gesture_1="Open_Palm",
            gesture_2="Closed_Fist",
            gesture_1_timeout_sec=3.0,
            gesture_2_timeout_sec=3.0,
            cooldown_sec=2.0,
        )
    )

    # Dynamic-gate unlock sequence (only meaningful when command_model_path
    # is set, since the dynamic gate's whole purpose is routing to
    # SwipeDetector, which requires the HaGRID model's "palm"/"two_up"
    # bookend vocabulary). "Victory" is the STOCK MediaPipe canned name
    # for a peace sign.
    dynamic_sequence: SequenceConfig = field(
        default_factory=lambda: SequenceConfig(
            gesture_1="Victory",
            gesture_2="Closed_Fist",
            gesture_1_timeout_sec=3.0,
            gesture_2_timeout_sec=3.0,
            cooldown_sec=2.0,
        )
    )

    # SwipeDetector tuning
    swipe: SwipeConfig = field(default_factory=SwipeConfig)

    # Command-vocabulary detection tuning
    command_debounce: DebouncerConfig = field(
        default_factory=lambda: DebouncerConfig(
            n_confirm=8, n_release=3, min_confidence=0.65
        )
    )

    # Session gating (only used when command_model_path is set).
    session: SessionConfig = field(
        default_factory=lambda: SessionConfig(idle_timeout_sec=30.0)
    )

    # How long after EITHER gate unlocks to ignore all command/bookend
    # frames entirely (not fed to the debouncer at all).
    command_transition_cooldown_sec: float = 0.6

    # The vocabulary label (AFTER normalize_command_label) that
    # corresponds to EITHER unlock sequence's final gesture (both static
    # and dynamic end on Closed_Fist → normalized "fist").
    unlock_tail_as_command: str = "fist"

    device_id: str = field(
        default_factory=lambda: os.environ.get("EDGE_DEVICE_ID", "edge-dev-01")
    )

    relay_url: str | None = None
    relay_token: str | None = field(
        default_factory=lambda: os.environ.get("EDGE_DEVICE_TOKEN")
    )

    preview: bool = False
    hud: bool = False  # camera-free status HUD — see preview.py's _draw_hud()
