from __future__ import annotations

import collections
import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd

from config import AudioConfig


def audio_producer(
    audio_cfg: AudioConfig,
    audio_queue: queue.Queue,
    stop_event: threading.Event,
    preview=None,
) -> None:
    """Rolling deque + RMS trigger. Pushes a COPY of the buffer at trigger
    time (deque keeps running and will be overwritten).
    """
    buffer = collections.deque(
        maxlen=int(audio_cfg.sample_rate * audio_cfg.clap_window_sec)
    )
    last_trigger = 0.0

    def callback(indata, frames, time_info, status):
        nonlocal last_trigger
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        samples = indata[:, 0]
        buffer.extend(samples)
        rms = float(np.sqrt(np.mean(samples**2)))
        if preview is not None:
            preview.rms = rms
        now = time.monotonic()
        if (
            rms > audio_cfg.rms_threshold
            and (now - last_trigger) > audio_cfg.retrigger_guard_sec
        ):
            last_trigger = now
            audio_queue.put((list(buffer), now))

    with sd.InputStream(
        samplerate=audio_cfg.sample_rate,
        channels=audio_cfg.channels,
        device=audio_cfg.device_index,
        blocksize=audio_cfg.chunk_size,
        callback=callback,
    ):
        while not stop_event.is_set():
            time.sleep(0.1)


def list_audio_devices() -> None:
    """CLI helper: print sounddevice's device table."""
    print(sd.query_devices())
