FROM python:3.11-slim AS base

ARG BUILD_DATE
ARG VCS_REF
LABEL org.opencontainers.image.title="gesture-inference" \
      org.opencontainers.image.description="CPU-only inference server; WebSocket client to relay (see ARCHITECTURE.md #6, #7)" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/<org>/<repo>"

# System libraries required by mediapipe / opencv-python-headless at
# import time, even without GUI support: libgl1 is still linked by
# OpenCV's video I/O code paths, and libglib is needed by several
# native extensions mediapipe pulls in. libportaudio2 is required
# because audio.py is imported unconditionally at module load
# (infer_live_v2.py), even though the container has no mic access.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY detection.py session.py command_label_normalizer.py .
COPY config.py audio.py video.py relay.py preview.py orchestrator.py infer_live_v2.py swipe_detector.py landmark_extractor.py .

# Model artifacts baked in at build time (decision #7's rationale —
# no first-boot download, no internet dependency at runtime). Combined
# size is ~16MB; if command_gesture_recognizer.task is not being used,
# comment out that COPY line and omit --command-model at runtime.
COPY gesture_recognizer.task .
COPY command_gesture_recognizer.task .

# Runs as a non-root user — no reason for this process to have root
# inside the container. 
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# No EXPOSE needed: this process is a WebSocket CLIENT to the relay,
# not a server — it makes outbound connections only.

ENTRYPOINT ["python", "infer_live_v2.py"]
# Default CMD is empty args (legacy mode, no relay, no command model).
# Override at `docker run` time, e.g.:
#   docker run --rm gesture-inference:latest --relay ws://relay-host:8080/ws/gesture --token <token>
CMD []