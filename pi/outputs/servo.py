"""
servo.py -- Steering servo driver.

Wraps gpiozero.AngularServo with:
  - Fail-soft import: if gpiozero/lgpio aren't available (e.g. local dev
    on a non-Pi machine), the SteeringServo becomes a no-op so the rest
    of the client can still run.
  - Input clamping in set_normalized() (defense in depth -- the watchdog
    already runs every 50ms; we still don't trust the wire).
  - Centers on init (initial_angle=0) so the very first physical state
    of the servo when the service starts is SAFE.
  - close() centers and releases the GPIO pin.
"""

import logging
import threading
import time
from typing import Optional

log = logging.getLogger("mower.servo")

DEFAULT_PIN = 18           # GPIO 18 = physical pin 12
DEFAULT_MAX_ANGLE_DEG = 60.0

try:
    from gpiozero import AngularServo
    _IMPORT_ERROR: Optional[BaseException] = None
except (ImportError, OSError, RuntimeError) as e:
    AngularServo = None  # type: ignore[assignment,misc]
    _IMPORT_ERROR = e


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


class SteeringServo:
    def __init__(
        self,
        pin: int = DEFAULT_PIN,
        max_angle_deg: float = DEFAULT_MAX_ANGLE_DEG,
    ) -> None:
        self._pin = pin
        self._max_angle_deg = float(max_angle_deg)
        self._lock = threading.Lock()
        self._servo = None
        self._last_set_error: Optional[str] = None

        if AngularServo is None:
            log.warning(
                "gpiozero unavailable (%s) -- steering servo is a no-op",
                _IMPORT_ERROR,
            )
            return

        try:
            self._servo = AngularServo(
                pin,
                initial_angle=0.0,
                min_angle=-self._max_angle_deg,
                max_angle=self._max_angle_deg,
            )
            log.info(
                "steering servo ready on GPIO %d (+/-%.0f deg)",
                pin, self._max_angle_deg,
            )
        except (ImportError, OSError, RuntimeError, ValueError) as e:
            log.exception("failed to initialize steering servo: %s", e)
            self._servo = None

    def set_normalized(self, value: float) -> None:
        """Set steering position from a normalized command in [-1.0, +1.0]."""
        clamped = _clamp(float(value), -1.0, 1.0)
        angle = clamped * self._max_angle_deg
        if self._servo is None:
            return
        try:
            with self._lock:
                if self._servo is not None:
                    self._servo.angle = angle
            self._last_set_error = None
        except (OSError, RuntimeError, ValueError) as e:
            msg = f"{type(e).__name__}: {e}"
            if msg != self._last_set_error:
                log.warning("servo.set_normalized failed: %s", msg)
                self._last_set_error = msg

    def center(self) -> None:
        self.set_normalized(0.0)

    def close(self) -> None:
        """Center the servo, then release the GPIO pin."""
        if self._servo is None:
            return
        try:
            with self._lock:
                if self._servo is not None:
                    self._servo.angle = 0.0
            time.sleep(0.2)  # let the servo physically reach center before PWM stops
            with self._lock:
                if self._servo is not None:
                    self._servo.close()
                    self._servo = None
            log.info("steering servo closed")
        except (OSError, RuntimeError) as e:
            log.warning("servo close failed: %s", e)
            self._servo = None
