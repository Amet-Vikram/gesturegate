"""
infer_live_v2.py — CLI entry point.
======================================

Model-free gesture sequence detection, with an optional session-gated
command vocabulary. Requires:

    config.py        — AppConfig and its sub-configs
    audio.py          — audio producer thread
    video.py          — video producer thread, MediaPipe plumbing
    relay.py          — RelayPublisher
    preview.py        — PreviewState, overlay rendering, preview_loop
    orchestrator.py   — run(): wires everything together
    detection.py       — GestureDebouncer, SequenceStateMachine (unchanged)
    session.py          — SessionStateMachine (unchanged)
    command_label_normalizer.py — label normalization (unchanged)

Usage:
    python infer_live_v2.py
    python infer_live_v2.py --preview
    python infer_live_v2.py --relay ws://localhost:8080/ws/gesture \
                            --token dev-edge-token-change-me
    python infer_live_v2.py --command-model command_gesture_recognizer.task
    python infer_live_v2.py --list_devices
    
    python infer_live_v2.py --command-model command_gesture_recognizer.task --camera 1 --relay ws://localhost:8080/ws/gesture --token dev-edge-token-change-me --hud
    
    python infer_live_v2.py --command-model command_gesture_recognizer.task --camera 1 --relay ws://10.131.146.9:8080/ws/gesture --token dev-edge-token-change-me --hud
    
Requirements:
    pip install mediapipe sounddevice opencv-python numpy websockets
    (websockets only needed when --relay is given)
"""

from __future__ import annotations

import argparse
import os
import sys
import threading

from config import AppConfig
from relay import RelayPublisher
from preview import PreviewState, preview_loop
from orchestrator import run
from audio import list_audio_devices
from video import download_mediapipe_model, list_camera_devices


def build_config(args: argparse.Namespace) -> AppConfig:
    """Starts from AppConfig's defaults and applies CLI overrides."""
    cfg = AppConfig()

    cfg.audio.rms_threshold = args.threshold
    cfg.audio.device_index = args.device
    cfg.video.camera_index = args.camera
    cfg.video.camera_url = (
        args.camera_url
    )  # None if not supplied; video_producer checks

    cfg.relay_url = args.relay
    cfg.relay_token = args.token or cfg.relay_token

    cfg.command_model_path = args.command_model
    cfg.preview = args.preview
    cfg.hud = args.hud

    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gesture sequence detection (v2), with optional "
        "session-gated command vocabulary"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=AppConfig().audio.rms_threshold,
        help="RMS energy threshold for clap detection",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=AppConfig().audio.device_index,
        help="Microphone device index",
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=AppConfig().video.camera_index,
        help="Local camera device index (default 1). "
        "Ignored when --camera-url is given.",
    )
    parser.add_argument(
        "--camera-url",
        default=None,
        help="IP camera stream URL instead of a local device, e.g. "
        "http://<ip>:<port>/video (IP Webcam on Android) "
        "When set, --camera and the platform camera backend are "
        "both ignored. The stream is opened at startup and "
        "automatically reconnected on drop.",
    )
    parser.add_argument(
        "--relay",
        default=None,
        help="Relay WebSocket URL, e.g. ws://host:8080/ws/gesture",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer token for the relay " "(or EDGE_DEVICE_TOKEN env var)",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Open a live debug window showing the camera feed, "
        "per-frame label, confidence, streak bar, and RMS level",
    )
    parser.add_argument(
        "--hud",
        action="store_true",
        help="Open a clean, camera-free status window showing the "
        "current gate (locked/static/dynamic), recognition "
        "progress, and last confirmed result ",
    )
    parser.add_argument(
        "--command-model",
        default=None,
        help="Path to a custom command_gesture_recognizer.task ",
    )
    parser.add_argument(
        "--list_devices",
        action="store_true",
        help="List audio and camera devices then exit",
    )
    args = parser.parse_args()

    if args.list_devices:
        print("=== Audio devices ===")
        list_audio_devices()
        print("\n=== Camera devices ===")
        list_camera_devices(AppConfig().video)
        sys.exit(0)

    if args.command_model is not None and not os.path.exists(args.command_model):
        print(
            f"ERROR: --command-model file not found: {args.command_model}",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = build_config(args)

    download_mediapipe_model(cfg.unlock_model_path, cfg.unlock_model_url)

    preview = PreviewState() if (cfg.preview or cfg.hud) else None
    relay = RelayPublisher(cfg.relay_url, cfg.relay_token)

    if preview is not None:
        t = threading.Thread(target=run, args=(cfg, relay, preview), daemon=True)
        t.start()
        preview_loop(
            preview,
            threading.Event(),
            cfg.debounce.n_confirm,
            cfg.command_debounce.n_confirm,
            cfg.debounce.min_confidence,
            cfg.audio.rms_threshold,
            show_camera=cfg.preview,
            show_hud=cfg.hud,
        )
        # preview_loop sets its own stop_event internally on Q; give the
        # background thread time to exit before the process ends.
        t.join(timeout=3.0)
    else:
        run(cfg, relay, preview=None)


if __name__ == "__main__":
    main()
