# Gesture Inference Server

A real-time gesture recognition edge server that solves the "Midas Touch"
problem: an always-on camera cannot tell a deliberate gesture from an
incidental one. Waving at someone across the room should not fire a
command.

The answer here is a **trigger sequence protocol**. Nothing is published
unless the user performs a deliberate three-part sequence:

```
clap  →  gesture_1  →  gesture_2
```

An audio clap opens a short detection window. Two gestures, confirmed in
order within that window, unlock a session. Only while a session is open
does the server publish command gestures. Outside a session, every
gesture is seen, classified, and deliberately ignored.

This is the edge component of a three-part distributed system. It runs
inference locally and publishes events over WebSocket to a relay, which
fans them out to subscribed clients.

---

## How it works

The unlock path is deterministic logic over an off-the-shelf classifier,
not a trained model. MediaPipe supplies per-frame labels; everything that
decides whether those labels constitute intent is plain Python and is
unit-testable without a camera, a microphone, or an ML runtime.

**Two independent gates.** Two finite state machines run concurrently
from the same clap, built from the same class with different
configurations:

| Gate | Sequence | Unlocks |
|---|---|---|
| Static | `Open_Palm` → `Closed_Fist` | Single-gesture commands (HaGRID model) |
| Dynamic | `Victory` → `Closed_Fist` | Directional swipes (motion tracking) |

The two are mutually exclusive by construction. A three-state session
machine (`LOCKED`, `STATIC_UNLOCKED`, `DYNAMIC_UNLOCKED`) makes "only one
gate is open" a property of the type rather than a rule enforced by
convention. Once one gate opens, the other machine stops receiving input
entirely.

**Debouncing before sequencing.** A label counts only after `n_confirm`
consecutive frames at or above `min_confidence`. Transitional poses
between deliberate gestures never reach the sequence logic.

**The command classifier is never on the unlock path.** A
misclassification inside an open session costs one wrong command. It can
never cause an unwanted unlock.

---

## Requirements

- Python 3.11
- A camera (local device or an IP stream) and a microphone
- CPU only. No GPU required.

```bash
pip install mediapipe sounddevice opencv-python numpy websockets
```

`websockets` is only needed when publishing to a relay.

---

## Quick start

```bash
# Check what devices are visible
python infer_live_v2.py --list_devices

# Run standalone with a debug preview window
python infer_live_v2.py --preview

# Run with the command vocabulary and publish to a relay
python infer_live_v2.py \
    --command-model command_gesture_recognizer.task \
    --relay ws://localhost:8080/ws/gesture \
    --token your-edge-token \
    --hud
```

Without `--command-model`, the server runs in single-event mode: the
unlock sequence publishes a `gesture_sequence_detected` event directly,
with no session layer. With it, the unlock sequence toggles a session and
command gestures are published only while that session is open.

---

## Options

| Flag | Default | Description |
|---|---|---|
| `--threshold` | from config | RMS energy threshold for clap detection |
| `--device` | from config | Microphone device index |
| `--camera` | `1` | Local camera index. Ignored when `--camera-url` is set. |
| `--camera-url` | none | IP camera stream instead of a local device, e.g. `http://192.168.1.42:8080/video` or an `rtsp://` URL. Reconnects automatically on drop. |
| `--relay` | none | Relay WebSocket URL |
| `--token` | none | Bearer token for the relay (or `EDGE_DEVICE_TOKEN` env var) |
| `--preview` | off | Debug window: camera feed, per-frame label, confidence, streak bar, RMS level |
| `--hud` | off | Camera-free status window showing gate, progress, and last result. Combinable with `--preview`. |
| `--command-model` | none | Path to a custom `.task` recognizer (e.g. trained on HaGRID) |
| `--list_devices` | — | List audio and camera devices, then exit |

`--camera-url` is what makes containerised deployment practical: an IP
stream needs only LAN reachability, so no USB passthrough is required.

---

## Published events

Three event types, all `schema_version: 2`:

```json
{
  "schema_version": 2,
  "event": "gesture_sequence_detected",
  "source": "mediapipe_gesture_recognizer_v1",
  "detected_at": "2026-08-12T21:49:57.123456+00:00",
  "sequence": ["Open_Palm", "Closed_Fist"],
  "confidences": [0.91, 0.88],
  "model_vocab": "mediapipe_canned"
}
```

`session_unlocked` and `session_locked` carry a `gate` field naming which
vocabulary is active, so a subscriber knows the session state without
inferring it from the first gesture event. `session_locked` also carries
a `reason`: `user_sequence` or `idle_timeout`.

The relay stamps `device_id` on every event from the authenticated
connection. It is never read from the payload, so an edge device cannot
claim to be another.

---

## Architecture

Eleven modules, each with one reason to change:

```
infer_live_v2.py              CLI entry point — argument parsing only
  config.py                   AppConfig and sub-configs
  audio.py                    Audio producer thread, RMS clap gate
  video.py                    Video producer thread, MediaPipe plumbing
  landmark_extractor.py       Wrist position from the recognizer result
  relay.py                    RelayPublisher
  preview.py                  Debug and HUD windows
  orchestrator.py             run() — wires everything together
  detection.py                GestureDebouncer, SequenceStateMachine
  session.py                  SessionStateMachine
  swipe_detector.py           Net-displacement swipe direction detector
  command_label_normalizer.py Label normalization
```

`detection.py`, `session.py` and `swipe_detector.py` are pure logic. They
perform no I/O, read no clock, and return action values rather than
executing side effects. Time is passed in as a parameter, so every
timeout path can be tested with synthetic timestamps and no wall-clock
waiting.

Two producer threads share one camera read per iteration and are released
by a shared `threading.Event`, so audio and video agree on a single
instant rather than on a start order.

**Swipe detection measures one vector, not many.** `swipe_detector.py`
takes a single net displacement from the wrist position at bookend
confirmation to its position at a fixed time horizon. Which pair of directions is even considered comes from the bookend pose — `palm` arms horizontal, `two_up` arms vertical. An ambiguous attempt is discarded and the detector re-arms, matching the "ignored by default" posture of the sequence logic.

---

## Known limitations

These are documented rather than smoothed over.

**The pre-latch guard has a compositional fault.** The guard that forces
a held fist to be released before it can register as a command fails in
integration, despite both relevant unit tests passing. The fault lives
between `config.py` and `orchestrator.py`, not within either module.

**Audio cannot be passed through to a container on Windows.** Docker
Desktop offers no microphone passthrough. The camera has a workaround via
`--camera-url`; audio does not, and runs natively on the host during
development. Both problems disappear on Linux edge hardware.

**No TLS.** The relay serves plain `ws://`, so bearer tokens cross the
network in the clear. The mitigation is a reverse proxy terminating TLS;
no code change is required.

---

## Related components

- **Relay server** (Go) — pub/sub fan-out, per-device bearer token auth,
  role enforcement between publishers and subscribers
- **GestureViewer** (WPF) — read-only reference client displaying gate
  state and incoming events


