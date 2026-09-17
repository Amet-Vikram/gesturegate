#!/usr/bin/env python3
"""
Minimal fake relay for testing GestureClient without the real Go relay or an
edge device.

Usage:
    pip install websockets
    python fake_relay.py

Then point GestureClient's appsettings.json at:
    "RelayUrl": "ws://localhost:8080/ws/gesture"

IMPORTANT: use ws:// not wss:// against this test server — it has no TLS.
Pointing a wss:// client at it produces a cryptic server-side error
("EOFError: line without CRLF") because the client sends a TLS handshake
that this plaintext server can't parse as HTTP.

Valid token for testing: dev-client-token-change-me
"""

import asyncio
import json
import websockets
from datetime import datetime, timezone

VALID_TOKENS = {"dev-client-token-change-me"}
GESTURES = ["gesture_1", "gesture_2", "clap_sequence"]

connected = set()


def _extract_token(headers) -> str:
    auth = headers.get("Authorization", "")
    return auth[len("Bearer ") :] if auth.startswith("Bearer ") else ""


async def process_request_newest(connection, request):
    if request.path != "/ws/gesture":
        return connection.respond(404, "not found\n")
    token = _extract_token(request.headers)
    if token not in VALID_TOKENS:
        print(f"rejected connection: bad token {token!r}")
        return connection.respond(401, "unauthorized\n")
    return None


async def process_request_new(connection):
    request = connection.request
    if request.path != "/ws/gesture":
        return connection.respond(404, "not found\n")
    token = _extract_token(request.headers)
    if token not in VALID_TOKENS:
        print(f"rejected connection: bad token {token!r}")
        return connection.respond(401, "unauthorized\n")
    return None


async def process_request_old(path, request_headers):
    if path != "/ws/gesture":
        return 404, [], b"not found\n"
    token = _extract_token(request_headers)
    if token not in VALID_TOKENS:
        print(f"rejected connection: bad token {token!r}")
        return 401, [], b"unauthorized\n"
    return None


async def handler(ws):
    print("client connected")
    connected.add(ws)
    try:
        await ws.wait_closed()
    finally:
        connected.discard(ws)
        print("client disconnected")


async def broadcaster():
    """Send a synthetic prediction to all clients every 5 seconds."""
    i = 0
    while True:
        await asyncio.sleep(5)
        if not connected:
            continue
        gesture = GESTURES[i % len(GESTURES)]
        i += 1
        payload = json.dumps(
            {
                "device_id": "edge-1",
                "predicted_class": gesture,
                "confidence": 0.9,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        print(f"broadcasting: {payload}")
        websockets.broadcast(connected, payload)


async def main():
    version = getattr(websockets, "__version__", "unknown")
    print(f"websockets library version: {version}")

    major = 0
    try:
        major = int(version.split(".")[0])
    except (ValueError, IndexError):
        pass

    if major >= 14:
        process_request = process_request_newest
        label = ">=14 (connection, request)"
    elif major >= 12:
        process_request = process_request_new
        label = "12-13 (connection)"
    else:
        process_request = process_request_old
        label = "<12 (path, headers)"

    print(f"using process_request handler for websockets {label}")
    server = await websockets.serve(
        handler, "localhost", 8080, process_request=process_request
    )

    async with server:
        print("fake relay on ws://localhost:8080/ws/gesture  (NOT wss:// — no TLS)")
        print("valid token: dev-client-token-change-me")
        await broadcaster()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nshutting down")
