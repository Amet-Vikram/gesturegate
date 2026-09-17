from __future__ import annotations

import json


class RelayPublisher:
    def __init__(self, url: str | None, token: str | None):
        self.url = url
        self.token = token
        self._ws = None
        self.connected = False  # readable by the preview overlay
        if url and not token:
            print("[relay] --relay given without --token; publishing disabled")
            self.url = None

    def _connect(self):
        from websockets.sync.client import connect

        self._ws = connect(
            self.url,
            additional_headers={"Authorization": f"Bearer {self.token}"},
        )
        self.connected = True
        print(f"[relay] connected to {self.url}")

    def connect_eager(self) -> None:
        """Establish the WebSocket connection at startup rather than
        waiting for the first publish. A failed eager connect is logged
        as a warning but does not abort the process — publish() will
        retry when the first event is ready to send."""
        if not self.url:
            return
        try:
            self._connect()
        except Exception as exc:
            print(
                f"[relay] WARNING: could not connect at startup ({exc}); "
                f"will retry on first publish"
            )

    def publish(self, payload: dict) -> None:
        if not self.url:
            return
        message = json.dumps(payload)
        for attempt in (1, 2):
            try:
                if self._ws is None:
                    self._connect()

                print(f"[LOG] sending message: {message}")
                self._ws.send(message)
                return
            except Exception as exc:
                self._ws = None
                self.connected = False
                if attempt == 2:
                    print(f"[relay] publish failed ({exc}); event dropped")

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
        self.connected = False
