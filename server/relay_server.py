"""
relay_server.py
===============

WebSocket relay between operator(s) and the mower.

ROLES:
  - "mower"    — connects from the Pi. Receives commands, sends status.
  - "operator" — connects from the web UI. Sends commands, receives status.

WHAT THE RELAY DOES:
  1. Auth: every connection must present Bearer token.
     - Mower must use MOWER_TOKEN
     - Operator must use OPERATOR_TOKEN
  2. There is exactly ONE mower slot. New mower connection kicks the old.
  3. There can be many operator connections (e.g. you on phone + laptop).
  4. Operator command messages are forwarded ONLY to the connected mower.
  5. Mower status messages are broadcast to all connected operators.
  6. Server injects its own ping every 100ms toward the mower as a keepalive
     so the mower's watchdog stays fresh even when no operator is actively
     touching the controls (idle = SAFE state, but link stays alive).
     IMPORTANT: ping does not change command state. Watchdog still goes SAFE
     if no operator is sending real commands; the ping just prevents
     reconnect churn during idle periods.

DEPLOYMENT (Railway):
  Set env vars in Railway dashboard:
    MOWER_TOKEN    = some-long-random-string
    OPERATOR_TOKEN = another-long-random-string
    PORT           = (Railway sets this automatically)
"""

import asyncio
import json
import logging
import os
from typing import Optional, Set

import websockets
from websockets.asyncio.server import ServerConnection, serve

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("PORT", "8080"))
MOWER_TOKEN = os.environ.get("MOWER_TOKEN", "dev-token-change-me")
OPERATOR_TOKEN = os.environ.get("OPERATOR_TOKEN", "dev-operator-change-me")

KEEPALIVE_PERIOD_S = 0.1  # 100ms ping to mower

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("relay")


# ---------------------------------------------------------------------------
# Connection registry
# ---------------------------------------------------------------------------

NOTES_MAX_BYTES = 65536  # cap shared notes at 64 KB to bound memory


class Hub:
    """Single source of truth for who's connected."""

    def __init__(self) -> None:
        self.mower: Optional[ServerConnection] = None
        self.operators: Set[ServerConnection] = set()
        self.notes_text: str = ""
        self._lock = asyncio.Lock()

    async def set_mower(self, ws: ServerConnection) -> Optional[ServerConnection]:
        async with self._lock:
            old = self.mower
            self.mower = ws
            return old

    async def clear_mower_if(self, ws: ServerConnection) -> None:
        async with self._lock:
            if self.mower is ws:
                self.mower = None

    async def add_operator(self, ws: ServerConnection) -> None:
        async with self._lock:
            self.operators.add(ws)

    async def remove_operator(self, ws: ServerConnection) -> None:
        async with self._lock:
            self.operators.discard(ws)

    async def send_to_mower(self, msg: str) -> bool:
        async with self._lock:
            target = self.mower
        if target is None:
            return False
        try:
            await target.send(msg)
            return True
        except websockets.exceptions.WebSocketException:
            return False

    async def broadcast_to_operators(self, msg: str) -> None:
        async with self._lock:
            targets = list(self.operators)
        for op in targets:
            try:
                await op.send(msg)
            except websockets.exceptions.WebSocketException:
                pass

    async def broadcast_to_operators_except(
        self, msg: str, except_ws: ServerConnection
    ) -> None:
        async with self._lock:
            targets = [op for op in self.operators if op is not except_ws]
        for op in targets:
            try:
                await op.send(msg)
            except websockets.exceptions.WebSocketException:
                pass

    async def get_notes(self) -> str:
        async with self._lock:
            return self.notes_text

    async def set_notes(self, text: str) -> None:
        if len(text) > NOTES_MAX_BYTES:
            text = text[:NOTES_MAX_BYTES]
        async with self._lock:
            self.notes_text = text


hub = Hub()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def authorize(ws: ServerConnection, expected_token: str) -> bool:
    """
    Check token against expected. Accepts either:
      - Authorization: Bearer <token>  header (used by Pi client)
      - ?token=<token>                  query string (used by browser; the
        WebSocket API in browsers cannot set custom headers on handshake)
    """
    auth = ws.request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):].strip()
        if token == expected_token:
            return True

    # Fallback: query string
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(ws.request.path).query)
    token_list = qs.get("token", [])
    if token_list and token_list[0] == expected_token:
        return True

    return False


# ---------------------------------------------------------------------------
# Connection handlers
# ---------------------------------------------------------------------------

async def keepalive_to_mower(ws: ServerConnection) -> None:
    """While the mower is connected, send a ping every 100ms."""
    try:
        while True:
            await asyncio.sleep(KEEPALIVE_PERIOD_S)
            try:
                await ws.send(json.dumps({"type": "ping"}))
            except websockets.exceptions.WebSocketException:
                return
    except asyncio.CancelledError:
        return


async def handle_mower(ws: ServerConnection) -> None:
    log.info("mower connected from %s", ws.remote_address)
    old = await hub.set_mower(ws)
    if old is not None:
        log.warning("kicking previous mower connection")
        try:
            await old.close(code=4000, reason="replaced by new mower")
        except Exception:
            pass

    keepalive_task = asyncio.create_task(keepalive_to_mower(ws))

    try:
        async for raw in ws:
            # Mower sends status / telemetry. Forward to operators.
            await hub.broadcast_to_operators(raw)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        keepalive_task.cancel()
        await hub.clear_mower_if(ws)
        log.info("mower disconnected")


async def handle_operator(ws: ServerConnection) -> None:
    log.info("operator connected from %s", ws.remote_address)
    await hub.add_operator(ws)

    # Push current shared notes to the new operator so all devices start in sync.
    try:
        notes = await hub.get_notes()
        await ws.send(json.dumps({"type": "notes_update", "text": notes}))
    except websockets.exceptions.WebSocketException:
        pass

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "command":
                ok = await hub.send_to_mower(raw)
                if not ok:
                    try:
                        await ws.send(json.dumps({"type": "error", "code": "mower_offline"}))
                    except websockets.exceptions.WebSocketException:
                        pass

            elif msg_type == "notes_set":
                text = msg.get("text", "")
                if not isinstance(text, str):
                    continue
                await hub.set_notes(text)
                stored = await hub.get_notes()
                broadcast = json.dumps({"type": "notes_update", "text": stored})
                await hub.broadcast_to_operators_except(broadcast, ws)

            # Other message types are ignored on purpose.
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        await hub.remove_operator(ws)
        log.info("operator disconnected")


async def router(ws: ServerConnection) -> None:
    """Route a new connection by URL path."""
    raw_path = ws.request.path
    # Strip query string for routing
    path = raw_path.split("?", 1)[0]
    log.info("incoming connection path=%s", path)

    if path == "/mower":
        if not authorize(ws, MOWER_TOKEN):
            log.warning("mower auth failed")
            await ws.close(code=4401, reason="unauthorized")
            return
        await handle_mower(ws)
    elif path == "/operator":
        if not authorize(ws, OPERATOR_TOKEN):
            log.warning("operator auth failed")
            await ws.close(code=4401, reason="unauthorized")
            return
        await handle_operator(ws)
    else:
        await ws.close(code=4404, reason="unknown path")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    log.info("relay starting on 0.0.0.0:%d", PORT)
    if MOWER_TOKEN == "dev-token-change-me" or OPERATOR_TOKEN == "dev-operator-change-me":
        log.warning("USING DEFAULT TOKENS — set MOWER_TOKEN and OPERATOR_TOKEN in production")
    async with serve(router, "0.0.0.0", PORT):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
