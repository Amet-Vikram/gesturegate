# Running the Full System


---

> Trained models: inference_server/src/*.task are excluded from this repository. gesture_recognizer.task is MediaPipe's stock model; command_gesture_recognizer.task is a custom model fine-tuned on a HaGRID subset. To regenerate it, follow HAGRID_MODEL_TRAINING.md.

## Step 1 — Relay server

The relay is the only component both others connect to, so it goes
first.

```bash
cd relay
docker compose up -d --build
```

Or without Docker:

```bash
cd relay
go run .
```

**Confirm it is up** before continuing:

```bash
curl http://localhost:8080/healthz
```

The startup log should read:

```
loaded 3 device credential(s) from devices.json
relay server listening on :8080
```

If the credential count is 0, `devices.json` was not found — it is
mounted read-only at runtime rather than baked into the image, so check
the compose mount path or `RELAY_DEVICES_FILE`.

---

## Step 2 — Inference server (edge stack)

Run natively rather than in Docker. Docker Desktop on Windows cannot
pass through a USB microphone, and the clap gate needs one.

```bash
python infer_live_v2.py \
    --relay ws://localhost:8080/ws/gesture \
    --token dev-edge-token-change-me \
    --command-model command_gesture_recognizer.task \
    --hud
```

**Check the banner.** It prints the configuration actually in force,
which is the fastest way to catch a stale `config.py`:

```
Static unlock:  CLAP -> Open_Palm -> Closed_Fist
Clap threshold: RMS > 0.08   Debounce: 5 frames @ >=0.6
Dynamic unlock: CLAP -> Victory -> Closed_Fist
Command layer: ACTIVE (command_gesture_recognizer.task)
  Debounce: 8 frames @ >=0.65   Idle auto-lock: 30.0s
```

Two values to verify: the **unlock** debounce reads `>=0.6` and the
**command** debounce reads `>=0.65`. They are different parameters and
the difference is deliberate.

Then:

```
[video] opened camera: index 1
[relay] connected to ws://localhost:8080/ws/gesture
```

If the camera index is wrong, `python infer_live_v2.py --list_devices`
lists cameras and microphones and exits. Add `--camera N` or
`--device N`. For an IP camera use `--camera-url http://<ip>:8080/video`
instead of `--camera`.

Use `--hud` for a clean status window, or `--preview` for the camera
feed with landmarks drawn — `--preview` is the one to use when
diagnosing recognition, `--hud` when demonstrating.

---

## Step 3 — Gesture Viewer client

Launch the client. On start-up it calls `GET /devices` and populates the
device dropdown; select `edge-1`.

Before the first run, confirm `appsettings.json` holds the relay URL and
a **client** token — not the edge token. The roles are enforced
separately, so an edge token will authenticate and then be unable to
subscribe.

```json
{
  "RelayUrl": "ws://localhost:8080/ws/gesture",
  "BearerToken": "dev-client-token-change-me"
}
```

The banner should read **Locked**. Nothing else will appear until a
session opens: the client's state is driven only by
`session_unlocked` and `session_locked`, never inferred from gesture
traffic.

---

## Verifying the three are talking

Perform a static unlock in front of the camera:

**clap → `Open_Palm` → `Closed_Fist`**

Hold each gesture for about a second. Expect, in order:

| Where | What you should see |
|---|---|
| Inference server | `[static:WAIT_GESTURE_1]` and `[dynamic:WAIT_GESTURE_1]` on the clap |
| Inference server | `[confirmed] Open_Palm`, then `[confirmed] Closed_Fist` |
| Inference server | `UNLOCKED [STATIC]`, then `[session] UNLOCKED (static) -> relay` |
| Relay | a broadcast line for the subscribed client |
| Gesture Viewer | banner changes **Locked → Static** |

Repeating the same sequence locks it again. Leaving it idle for 30
seconds auto-locks with `reason: idle_timeout`.

For the dynamic gate, substitute `Victory` for `Open_Palm`. Only one
gate can be open at a time — while a session is live, the other gate is
not armed at all, so its sequence will do nothing.


