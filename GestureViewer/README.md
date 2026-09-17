# Gesture Viewer (Windows WPF window app)

A rudimentary demonstration client for the relay server. It does two things:
connects to the relay as a `client` and shows what arrives — the incoming
gesture events and the current session state (**LOCKED / STATIC / DYNAMIC**).

This replaces the tray-based `GestureClient`. It is a normal window app: no
tray icon, no single-instance guard, no keystroke mapping or `SendInput`, no
DPAPI. The token lives in plain `appsettings.json`, which is fine for a demo
and deliberately not fine for anything else.

## Window layout

| Area | What it shows |
|---|---|
| Header | The configured relay URL and a connection pill (Disconnected / Connecting / Connected / Reconnecting / Connection failed). |
| Connection bar | Device dropdown from `GET /devices`, **Refresh devices**, **Connect** / **Disconnect**. |
| State banner | The current gate in large type, colour-coded, with the active vocabulary and the time it last changed. |
| Gesture table | One row per `gesture_sequence_detected`, newest first: time, sequence, per-label confidences, the gate it arrived under, `model_vocab`, and the relay-stamped `device_id`. Gestures that arrive while Locked are shown greyed and italic, marked *LOCKED — ignored*. |
| Activity log | Connection lifecycle, subscribe frames, gate transitions, and anything that failed to parse. |

## Session state

The relay owns the lock state; this app is a read-only observer.
`session_unlocked` carries the `gate` that just went live, `session_locked`
carries the gate that closed plus a `reason`. `gesture_sequence_detected`
carries **no** gate, so the banner is driven solely by the session events.

- **LOCKED** — no gate open. Gestures still get listed, marked as ignored.
- **STATIC** — `fist, four, like, ok, one, palm, peace, stop, three2, two_up`
- **DYNAMIC** — `swipe_up, swipe_down, swipe_left, swipe_right`

The banner resets to LOCKED whenever the connection drops, since at that
point the app no longer knows the relay's state.

## Configure

`appsettings.json`, copied next to the exe on build:

```json
{
  "RelayUrl": "ws://localhost:8080/ws/gesture",
  "Token": "dev-client-token-change-me",
  "DeviceId": "",
  "MaxGestureRows": 200
}
```

| Setting | Meaning |
|---|---|
| `RelayUrl` | The relay's WebSocket endpoint. `ws://` local, `wss://` behind TLS. The `GET /devices` base URL is derived from it (`ws→http`, `wss→https`). |
| `Token` | A `client`-role bearer token from the relay's `devices.json`. Sent as `Authorization: Bearer …` on both the HTTP request and the WebSocket handshake. |
| `DeviceId` | Last connected device, pre-selected in the dropdown. Written back on connect. |
| `MaxGestureRows` | How many gesture rows to keep before the oldest are dropped. |

A missing, unreadable, or non-`ws`/`wss` config falls back to the defaults
above and says so in the activity log, rather than failing to start.

## Server contract used

- `GET /devices` — online edge devices the token is authorised for. An empty
  array is a valid answer and is reported as "no devices online".
- `WS /ws/gesture` — the token goes on the handshake, so a bad token is a
  plain HTTP 401 before the upgrade (reported as such, not as a socket error).
  Nothing arrives until `{"action":"subscribe","topic":"<device_id>"}` is
  sent; the subscribe frame is re-sent after every reconnect because the
  relay keeps no subscription state across connections.
- Reconnects use exponential backoff capped at 30s and run until you press
  **Disconnect**.
- Parsing is defensive: an unknown event type, a malformed frame, a missing
  `gate`, or an empty `sequence` is logged and skipped rather than taking the
  receive loop down. A `schema_version` other than 2 is noted in the log.

Changing device while connected isn't supported — disconnect, pick another,
reconnect.

## Build & run (Windows, .NET 8 SDK)

```powershell
cd GestureViewer
dotnet build
dotnet run
```

Single-file exe:

```powershell
dotnet publish -c Release -r win-x64 --self-contained true `
  -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true
```

Copy `appsettings.json` next to the published exe. WPF-only, so this cannot
be built or run on Linux/macOS.

## Test without a real relay

`fake_relay.py` from the previous client implements the same v2 contract and
works unchanged:

```bash
pip install websockets
python fake_relay.py
```

Keep `RelayUrl` on `ws://localhost:8080/ws/gesture` and the token as
`dev-client-token-change-me`. Refresh devices, pick `edge-1`, connect, and
watch the banner cycle Locked → Static → Dynamic as gesture rows arrive.


