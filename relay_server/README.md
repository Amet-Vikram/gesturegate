# Gesture Relay Server

A small WebSocket relay in Go. Edge devices publish gesture and session
events under their own topic; client applications subscribe to the topics
their token authorises and receive those events.

The relay does no inference and holds no business logic beyond routing. It
authenticates connections, validates message structure, and fans messages
out to the right subscribers. That narrow scope is deliberate: a new model
with a new gesture vocabulary requires no change here.

---

## Quick start

```bash
docker compose up -d --build
```

The relay listens on `:8080`. Check it is alive:

```bash
curl http://localhost:8080/healthz          # -> ok
```

Then list the edge devices your token can see:

```bash
curl -H "Authorization: Bearer your-token" http://localhost:8080/devices
```

`devices.json` is mounted read-only rather than baked into the image, so
rotating a token is an edit and a `docker compose restart relay` with no
rebuild.

---

## Endpoints

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/healthz` | GET | none | Liveness check |
| `/devices` | GET | any valid token | List online, authorised edge devices |
| `/ws/gesture` | WS upgrade | any valid token | Publish (`edge`) or subscribe (`client`) |

Every endpoint except `/healthz` requires a standard bearer token:

```
Authorization: Bearer <token>
```

On the WebSocket endpoint the header goes on the **handshake request**,
before the upgrade. Browsers' native `WebSocket` API cannot set headers,
so use a real client library. `test_client.py` is a working example.

---

## Roles

A token's role decides what a connection can do. There is no way to do
both.

| Role | Sends | Receives |
|---|---|---|
| `edge` | `gesture_sequence_detected`, `session_unlocked`, `session_locked` | nothing — publishing is one-way |
| `client` | subscribe / unsubscribe requests | all three event types, for subscribed topics only |

Tokens are issued out of band via `devices.json`:

```json
[
  {
    "token": "some-long-random-string",
    "device_id": "edge-1",
    "role": "edge",
    "topics": ["edge-1"]
  }
]
```

`topics` is the **ceiling** of what a token may ever subscribe to, not a
list of active subscriptions. An edge token normally lists just its own
`device_id`; a client token lists every edge device it should be allowed
to see.

---

## Publishing (role `edge`)

Send one JSON message per event. All events share an envelope:

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

`confidences` must be the same length as `sequence`, position-matched,
each in `[0, 1]`.

**Do not send `device_id`.** It is not part of the inbound schema. The
relay stamps your authenticated `device_id` onto the copy it forwards, so
an edge device cannot claim to be another.

`session_unlocked` and `session_locked` carry a required `gate` field,
`"static"` or `"dynamic"`, naming which of the two mutually exclusive
vocabularies just became active. It is required because it is the only
signal a subscriber has for which vocabulary went live without waiting
for the first gesture event and inferring it. `session_locked` also
carries a `reason`: `"user_sequence"` or `"idle_timeout"`.

Anything failing validation is dropped and logged server-side. Nothing is
reported back over the socket.

---

## Subscribing (role `client`)

**You receive nothing until you explicitly subscribe.** Active
subscriptions start empty on every connection.

```json
{ "action": "subscribe", "topic": "edge-1" }
```

Subscriptions are live, mutable, per-connection state. Subscribe to
multiple topics and change them at any point while connected. Requesting
a topic outside your token's `topics` list is rejected and logged; the
connection stays open and no error is sent back.

You will never receive your own messages echoed back, and never anything
from a topic you have not subscribed to, even if your token would
authorise it.

---

## How it works

**One goroutine owns all shared state.** Every connection gets its own
goroutines, but exactly one — the Hub's `run()` loop — ever touches
`h.clients` or `h.subscriptions`. Connections send *requests* on channels
(`register`, `unregister`, `broadcast`, `subscribe`, `list`, `shutdown`)
rather than reading or writing maps directly.

This is why there is no mutex anywhere in the codebase. There is
structurally one door into shared state, so there is nothing to race on.

Per connection:

- **`readPump`** runs in the goroutine that handled the HTTP upgrade and
  blocks on `ReadMessage()` for the life of the connection.
- **`writePump`** runs in its own goroutine and is the only code path
  that ever calls `WriteMessage()` for that connection. `gorilla/websocket`
  permits one concurrent reader and one concurrent writer, so this keeps
  that requirement structural rather than a convention someone has to
  remember.

**Backpressure.** Each client has a buffered outbound channel, capacity
16. If a client cannot keep up and that buffer fills, the Hub drops it
rather than block delivery to everyone else. One slow subscriber cannot
stall the fan-out.

### Authorisation, three independent layers

1. **Authentication** — does the bearer token match an entry in
   `devices.json`? Checked **before** the WebSocket upgrade, so a bad
   token gets a plain `401` and no connection is ever created. A normal
   HTTP status is easier for a client to handle than a close code.
2. **Role** — enforced structurally. A `client` connection's bytes are
   *always* parsed as a subscription request, never as an edge event.
   There is no code path for a client to smuggle a gesture event through.
3. **Topic scoping** — re-checked on every subscribe request, not just at
   connect time. `/devices` applies the same filter, so you never see a
   device your token could not subscribe to.

### File map

| File | Owns |
|---|---|
| `main.go` | Entry point: config, routes, graceful shutdown sequence |
| `hub.go` | `Hub` — single owner of all connection and subscription state |
| `client.go` | `Client` — one connection's read/write loops and role dispatch |
| `auth.go` | Loads and indexes `devices.json` at startup |
| `schema.go` | Validates everything crossing the wire |
| `devices.json` | Config, not code — the token store |
| `test_client.py` | Reference client covering publish, subscribe, discover |

---

## Graceful shutdown

`SIGTERM`/`SIGINT` runs a two-stage sequence:

1. `http.Server.Shutdown()` stops accepting new connections and drains
   in-flight plain HTTP requests. It does **not** touch WebSocket
   connections — `net/http` considers them hijacked.
2. A shutdown request to the Hub closes every open connection with a real
   close frame, code `1001`, rather than a raw disconnect.

That distinction is observable: clients see `close_code=1001` rather than
the abrupt `1006` an unhandled process kill produces, so a client can
tell a deliberate shutdown from a broken connection.

Budgeted at roughly ten seconds. `docker-compose.yml` sets a 15s
`stop_grace_period` so it can finish before Docker escalates to
`SIGKILL`.

---

## Known gaps

Flagged deliberately rather than discovered later.

**No TLS.** Every token currently crosses the network in plaintext. The
fix is a reverse proxy terminating TLS; no Go changes are required.

**`/devices` is a live snapshot, not a registry.** A device that exists
in `devices.json` but is currently offline does not appear at all. There
is no database, deliberately, at this scale.

**No reconnect or resume state.** A client that reconnects starts with an
empty subscription set and must re-subscribe.

**`role` is not validated at load time.** A typo such as `"edg"` loads
silently and only surfaces per-message later, logged and dropped, rather
than failing loudly at startup.

---

## Related components

- **Inference server** (Python) — MediaPipe gesture recognition, RMS clap
  gate, FSM session management. Publishes as role `edge`.
- **GestureViewer** (WPF) — read-only reference client displaying gate
  state and incoming events. Subscribes as role `client`.


