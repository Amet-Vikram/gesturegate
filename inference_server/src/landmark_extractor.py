"""
landmark_extractor.py — extracts a single 2D hand-position signal from a
MediaPipe GestureRecognizer result, alongside the existing (label,
confidence) classification output.

This is a SPIKE artifact for assessing whether raw landmark position is a
viable signal for dynamic (swipe) gesture detection. If the spike concludes
the signal is viable, this logic folds into video.py's
_RecognizerSlot.classify() as an additional return value; kept standalone
here so it can be deleted without touching the production pipeline if the
spike concludes the signal is NOT viable.

Why this doesn't need a second model: MediaPipe's GestureRecognizer runs
hand landmark detection internally as a precursor to classification — the
result object already carries `hand_landmarks` alongside `gestures`. This
applies to both the stock recognizer and any Model Maker fine-tune (only
the classification head is retrained; the landmark detector is untouched).
So extracting position costs nothing extra at inference time — it's reading
a field off a call already being made, not adding a new model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# MediaPipe HandLandmarker landmark indices (21-point model).
WRIST = 0
INDEX_MCP = 5
MIDDLE_MCP = 9
RING_MCP = 13
PINKY_MCP = 17

# Centroid over wrist + all four MCP (knuckle) landmarks — a broader
# reference point than wrist alone, computed for comparison.
_CENTROID_INDICES = (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)


@dataclass
class HandPosition:
    """Normalized [0,1] image-space position signals for one detected hand,
    extracted from a single frame's recognizer result. Two candidate
    signals are reported side by side so the spike can compare them:

    wrist_x/y:    position of the WRIST landmark alone. Chosen as the
                  likely primary signal because it is a single rigid
                  reference point relatively unaffected by finger
                  articulation — a "palm" and "two_up" pose have very
                  different finger landmark positions even when the hand
                  as a whole hasn't moved, so averaging all 21 points
                  would inject pose-dependent noise into a position
                  signal that is supposed to represent hand TRANSLATION,
                  not hand SHAPE.
    centroid_x/y: average of wrist + 4 MCP knuckle landmarks. Broader
                  base than wrist alone; may reduce single-landmark
                  detection jitter at the cost of some pose sensitivity.
                  Logged for comparison, not assumed superior.
    """

    wrist_x: float
    wrist_y: float
    centroid_x: float
    centroid_y: float
    num_landmarks: int  # sanity check — should be 21 when a hand is present


def extract_hand_position(result) -> Optional[HandPosition]:
    """Extracts wrist and centroid position from a MediaPipe
    GestureRecognizer result.

    Returns None if no hand was detected in this frame
    (result.hand_landmarks is empty). This is a NORMAL, expected
    occurrence — occlusion, motion blur, hand briefly leaving frame — and
    callers must treat it as a gap in the position signal, not an error.
    Silently dropping or interpolating through gaps is a real design
    decision for any eventual SwipeDetector; this function just reports
    the gap honestly.

    result.hand_landmarks is a list (one entry per detected hand) of
    lists (21 NormalizedLandmark objects per hand, each with .x, .y, .z
    in [0,1] image-relative coordinates, origin top-left). Since the
    recognizer is configured with num_hands=1, only
    result.hand_landmarks[0] is ever relevant.
    """
    if not result.hand_landmarks:
        return None

    landmarks = result.hand_landmarks[0]
    if len(landmarks) < max(_CENTROID_INDICES) + 1:
        return None  # malformed/truncated result — treat as no detection

    wrist = landmarks[WRIST]
    cx = sum(landmarks[i].x for i in _CENTROID_INDICES) / len(_CENTROID_INDICES)
    cy = sum(landmarks[i].y for i in _CENTROID_INDICES) / len(_CENTROID_INDICES)

    return HandPosition(
        wrist_x=wrist.x,
        wrist_y=wrist.y,
        centroid_x=cx,
        centroid_y=cy,
        num_landmarks=len(landmarks),
    )


# http://10.131.146.24:4747/video
