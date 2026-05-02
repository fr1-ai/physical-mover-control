"""
servo.py -- Steering servo driver.

Uses lgpio.tx_servo() directly for kernel-timed pulse generation. This
avoids the visible jitter you get from gpiozero.AngularServo's software
PWM, which is bit-banged in userspace and hiccups whenever the Linux
scheduler preempts the process.

SAFETY:
  - Centers on construction
  - Centers on close()
  - close() stops pulses and releases the chip handle
  - set_normalized() clamps to [-1.0, 1.0] before mapping to pulse width
  - Fail-soft: if lgpio isn't available (e.g. dev on a non-Pi), the
    driver becomes a logged no-op so the rest of the client still runs
"""

import logging
import threading
from typing import Optional

log = logging.getLogger("mower.servo")

DEFAULT_GPIO = 18              # BCM GPIO 18 = physical pin 12
DEFAULT_MAX_ANGLE_DEG = 60.0
CENTER_PULSE_US = 1500
HALF_RANGE_PULSE_US = 500      # +/-500us = +/-90deg full servo range
MIN_PULSE_US = 500
MAX_PULSE_US = 2500

try:
    import lgpio
    _IMPORT_ERROR: Optional[BaseException] = None
except (ImportError, OSError) as e:
    lgpio = None  # type: ignore[assignment]
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
        gpio: int = DEFAULT_GPIO,
        max_angle_deg: float = DEFAULT_MAX_ANGLE_DEG,
    ) -> None:
        self._gpio = gpio
        self._max_angle_deg = float(max_angle_deg)
        # Pulse offset from center for full deflection at this max angle.
        # 90deg full range = HALF_RANGE_PULSE_US, so scale linearly.
        self._max_offset_us = HALF_RANGE_PULSE_US * (self._max_angle_deg / 90.0)
        self._lock = threading.Lock()
        self._chip = None
        self._last_pulse_us: Optional[int] = None
        self._last_set_error: Optional[str] = None

        if lgpio is None:
            log.warning(
                "lgpio unavailable (%s) -- steering servo is a no-op",
                _IMPORT_ERROR,
            )
            return

        try:
            self._chip = lgpio.gpiochip_open(0)
            # Center the servo immediately so first physical state is SAFE.
            lgpio.tx_servo(self._chip, self._gpio, CENTER_PULSE_US)
            self._last_pulse_us = CENTER_PULSE_US
            log.info(
                "steering servo ready on GPIO %d (+/-%.0f deg, +/-%.0f us)",
                gpio, self._max_angle_deg, self._max_offset_us,
            )
        except (OSError, RuntimeError, ValueError, AttributeError) as e:
            log.exception("failed to initialize steering servo: %s", e)
            self._chip = None

    def set_normalized(self, value: float) -> None:
        """Set steering position from a normalized command in [-1.0, +1.0]."""
        clamped = _clamp(float(value), -1.0, 1.0)
        pulse_us = int(round(CENTER_PULSE_US + clamped * self._max_offset_us))
        # Hard floor/ceiling on pulse width as well -- defense in depth.
        if pulse_us < MIN_PULSE_US:
            pulse_us = MIN_PULSE_US
        elif pulse_us > MAX_PULSE_US:
            pulse_us = MAX_PULSE_US

        if self._chip is None:
            return
        # Skip the syscall if the requested pulse hasn't changed -- lgpio
        # is smart about this internally too, but skipping in Python is
        # cheaper and avoids any chance of re-arming the pulse generator.
        if pulse_us == self._last_pulse_us:
            return
        try:
            with self._lock:
                if self._chip is not None:
                    lgpio.tx_servo(self._chip, self._gpio, pulse_us)
                    self._last_pulse_us = pulse_us
            self._last_set_error = None
        except (OSError, RuntimeError, ValueError) as e:
            msg = f"{type(e).__name__}: {e}"
            if msg != self._last_set_error:
                log.warning("servo.set_normalized failed: %s", msg)
                self._last_set_error = msg

    def center(self) -> None:
        self.set_normalized(0.0)

    def close(self) -> None:
        """Center, stop sending pulses, release the chip handle."""
        if self._chip is None:
            return
        try:
            with self._lock:
                if self._chip is not None:
                    # Command center first.
                    lgpio.tx_servo(self._chip, self._gpio, CENTER_PULSE_US)
            # Tiny pause so the servo physically reaches center before
            # we stop driving it.
            import time
            time.sleep(0.2)
            with self._lock:
                if self._chip is not None:
                    # 0 disables the servo pulse train.
                    try:
                        lgpio.tx_servo(self._chip, self._gpio, 0)
                    except (OSError, RuntimeError, ValueError):
                        pass
                    lgpio.gpiochip_close(self._chip)
                    self._chip = None
            log.info("steering servo closed")
        except (OSError, RuntimeError, ValueError) as e:
            log.warning("servo close failed: %s", e)
            self._chip = None
