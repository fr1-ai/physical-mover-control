"""
mower_pi_client.py
==================

Pi-side WebSocket client with dead-man heartbeat safety.

Connects to Railway-hosted relay server, registers as the "mower" client,
receives commands from operator(s), and enforces FAIL-SAFE behavior if
heartbeats stop arriving.

SAFETY MODEL:
  Default state of every output is SAFE.
  Movement only occurs while fresh, valid commands are arriving continuously.
  If no heartbeat received for HEARTBEAT_TIMEOUT_MS, all outputs go to SAFE.

THIS VERSION:
  No real GPIO. Outputs are LOGGED so you can verify the safety logic.
  GPIO output layer comes in a later phase.

USAGE:
  pip install -r requirements.txt
  RELAY_URL=wss://your-app.up.railway.app/mower \\
  MOWER_TOKEN=your-secret-token \\
  python3 mower_pi_client.py
"""

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Optional

import websockets
from websockets.exceptions import ConnectionClosed

from outputs import SteeringServo

# ---------------------------------------------------------------------------
# Configuration (env vars with safe defaults)
# ---------------------------------------------------------------------------

RELAY_URL = os.environ.get("RELAY_URL", "ws://localhost:8080/mower")
MOWER_TOKEN = os.environ.get("MOWER_TOKEN", "dev-token-change-me")

HEARTBEAT_TIMEOUT_MS = 200
WATCHDOG_PERIOD_MS = 50
RECONNECT_DELAY_S = 1.0
RECONNECT_MAX_DELAY_S = 30.0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d  %(levelname)-7s  [%(name)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mower")


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

state_lock = threading.Lock()
last_heartbeat_ns = 0
latest_command = {"drive": 0.0, "steer": 0.0, "blade": False}
shutdown_event = threading.Event()


# ---------------------------------------------------------------------------
# Output layer
# ---------------------------------------------------------------------------

_last_printed_state: Optional[tuple] = None
_steering_servo: Optional[SteeringServo] = None


def apply_outputs(drive: float, steer: float, blade: bool, reason: str) -> None:
    """Drive physical outputs. Logs only when state CHANGES (not every tick)."""
    global _last_printed_state
    state_tuple = (round(drive, 2), round(steer, 2), blade)
    if state_tuple != _last_printed_state:
        log.info(
            "OUTPUT  drive=%+.2f steer=%+.2f blade=%-3s   (%s)",
            drive, steer, "ON" if blade else "off", reason,
        )
        _last_printed_state = state_tuple
    if _steering_servo is not None:
        _steering_servo.set_normalized(steer)


def go_safe(reason: str) -> None:
    """The single source of truth for what 'stopped' means."""
    apply_outputs(drive=0.0, steer=0.0, blade=False, reason=reason)


# ---------------------------------------------------------------------------
# Watchdog thread (independent of asyncio)
# ---------------------------------------------------------------------------

def watchdog_loop() -> None:
    wlog = logging.getLogger("mower.watchdog")
    wlog.info("starting (timeout=%dms, period=%dms)",
              HEARTBEAT_TIMEOUT_MS, WATCHDOG_PERIOD_MS)

    while not shutdown_event.is_set():
        now_ns = time.monotonic_ns()
        with state_lock:
            hb_ns = last_heartbeat_ns
            cmd = dict(latest_command)

        age_ms = (now_ns - hb_ns) / 1_000_000 if hb_ns else float("inf")

        if age_ms > HEARTBEAT_TIMEOUT_MS:
            go_safe(reason=f"no heartbeat for {age_ms:.0f}ms")
        else:
            apply_outputs(
                drive=cmd["drive"],
                steer=cmd["steer"],
                blade=cmd["blade"],
                reason=f"heartbeat {age_ms:.0f}ms ago",
            )

        time.sleep(WATCHDOG_PERIOD_MS / 1000.0)

    go_safe(reason="shutdown")
    wlog.info("stopped")


# ---------------------------------------------------------------------------
# Network layer (WebSocket client with reconnect)
# ---------------------------------------------------------------------------

async def handle_message(raw: str) -> None:
    """Parse and validate one inbound message from the relay."""
    global last_heartbeat_ns

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("bad json: %r", raw[:80])
        return

    msg_type = msg.get("type")

    if msg_type == "command":
        # Clamp to safe ranges. Defense in depth -- never trust the wire.
        drive = max(-1.0, min(1.0, float(msg.get("drive", 0.0))))
        steer = max(-1.0, min(1.0, float(msg.get("steer", 0.0))))
        blade = bool(msg.get("blade", False))

        with state_lock:
            last_heartbeat_ns = time.monotonic_ns()
            latest_command["drive"] = drive
            latest_command["steer"] = steer
            latest_command["blade"] = blade

    elif msg_type == "ping":
        # Heartbeat-only message. Resets the watchdog without changing command.
        with state_lock:
            last_heartbeat_ns = time.monotonic_ns()

    else:
        log.debug("unknown message type: %s", msg_type)


async def network_session(url: str) -> None:
    """One connection lifecycle. Returns on disconnect; caller reconnects."""
    nlog = logging.getLogger("mower.net")
    nlog.info("connecting to %s", url)

    headers = {"Authorization": f"Bearer {MOWER_TOKEN}"}

    async with websockets.connect(
        url,
        additional_headers=headers,
        ping_interval=20,    # send WS ping every 20s (transport-level keepalive)
        ping_timeout=10,
        close_timeout=2,
    ) as ws:
        nlog.info("connected")
        # Announce ourselves
        await ws.send(json.dumps({"type": "hello", "role": "mower"}))

        # Receive loop
        async for raw in ws:
            await handle_message(raw)

    nlog.info("disconnected")


async def network_loop() -> None:
    """Reconnect-with-backoff loop. Survives all transient network errors."""
    nlog = logging.getLogger("mower.net")
    delay = RECONNECT_DELAY_S

    while not shutdown_event.is_set():
        try:
            await network_session(RELAY_URL)
            delay = RECONNECT_DELAY_S  # reset on clean disconnect
        except ConnectionClosed as e:
            nlog.warning("connection closed: %s", e)
        except (OSError, websockets.exceptions.WebSocketException) as e:
            nlog.warning("connection error: %s", e)
        except Exception as e:
            # Catch-all so the loop can never die. Watchdog still runs.
            nlog.exception("unexpected error: %s", e)

        if shutdown_event.is_set():
            break

        nlog.info("reconnecting in %.1fs", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_MAX_DELAY_S)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def install_signal_handlers() -> None:
    def handler(signum, frame):
        log.info("signal %d received -> shutdown", signum)
        shutdown_event.set()
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def main() -> None:
    install_signal_handlers()
    log.info("starting mower client; relay=%s", RELAY_URL)

    global _steering_servo
    _steering_servo = SteeringServo()

    wd_thread = threading.Thread(target=watchdog_loop, daemon=True, name="watchdog")
    wd_thread.start()

    try:
        asyncio.run(network_loop())
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_event.set()
        wd_thread.join(timeout=1.0)
        if _steering_servo is not None:
            _steering_servo.close()
        log.info("clean exit")


if __name__ == "__main__":
    main()
