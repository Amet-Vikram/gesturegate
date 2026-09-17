"""
Test client for the relay server's /ws/gesture channel.

Requires: pip install websockets

Usage:
    python test_client.py <token> [message_to_send]

"""

import asyncio
import sys

import websockets

RELAY_URL = "ws://localhost:8080/ws/gesture"


async def main(token: str, message: str | None) -> None:
    headers = {"Authorization": f"Bearer {token}"}

    async with websockets.connect(RELAY_URL, additional_headers=headers) as ws:
        print("connected")

        if message:
            await ws.send(message)
            print(f"sent: {message}")

        # Listen for whatever the hub broadcasts to us, forever, until
        # the connection closes or you Ctrl+C.
        try:
            async for incoming in ws:
                print(f"received: {incoming}")
        except websockets.ConnectionClosed as e:
            print(f"connection closed: code={e.code} reason={e.reason!r}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    token_arg = sys.argv[1]
    message_arg = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(main(token_arg, message_arg))
