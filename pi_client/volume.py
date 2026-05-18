import logging
import re
import subprocess
import threading

import RPi.GPIO as GPIO

from .config import ALSA_CONTROL, VOL_DOWN_PIN, VOL_MAX, VOL_MIN, VOL_STEP, VOL_UP_PIN

logger = logging.getLogger(__name__)


def _get_volume(alsa_control: str) -> int | None:
    try:
        out = subprocess.check_output(
            ["amixer", "sget", alsa_control], text=True, stderr=subprocess.DEVNULL
        )
        m = re.search(r"\[(\d+)%\]", out)
        return int(m.group(1)) if m else None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _set_volume(pct: int, alsa_control: str) -> None:
    subprocess.run(
        ["amixer", "sset", alsa_control, f"{pct}%"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class VolumeRocker:
    """
    Two-button volume rocker using GPIO falling-edge events.

    on_change(pct) is called in a daemon thread after each successful
    volume change so the display overlay can be updated without blocking.
    """

    def __init__(self, on_change=None):
        self._on_change = on_change
        self._lock = threading.Lock()

        # BCM mode may already be set by PushToTalkButton — idempotent
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(VOL_UP_PIN,   GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(VOL_DOWN_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        GPIO.add_event_detect(VOL_UP_PIN,   GPIO.FALLING, callback=self._on_up,   bouncetime=150)
        GPIO.add_event_detect(VOL_DOWN_PIN, GPIO.FALLING, callback=self._on_down, bouncetime=150)

        logger.info(
            "VolumeRocker ready (up=GPIO%d, down=GPIO%d, step=%d%%)",
            VOL_UP_PIN, VOL_DOWN_PIN, VOL_STEP,
        )

    # ------------------------------------------------------------------
    # GPIO callbacks — run in RPi.GPIO thread; hand off immediately
    # ------------------------------------------------------------------

    def _on_up(self, _channel):
        threading.Thread(target=self._adjust, args=(+VOL_STEP,), daemon=True).start()

    def _on_down(self, _channel):
        threading.Thread(target=self._adjust, args=(-VOL_STEP,), daemon=True).start()

    # ------------------------------------------------------------------
    # Volume adjustment
    # ------------------------------------------------------------------

    def _adjust(self, delta: int) -> None:
        with self._lock:
            current = _get_volume(ALSA_CONTROL)
            if current is None:
                logger.warning("Could not read ALSA volume for control %r", ALSA_CONTROL)
                return
            new_pct = max(VOL_MIN, min(VOL_MAX, current + delta))
            if new_pct == current:
                return  # already at limit — skip display flash
            _set_volume(new_pct, ALSA_CONTROL)

        if self._on_change:
            threading.Thread(target=self._on_change, args=(new_pct,), daemon=True).start()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        GPIO.remove_event_detect(VOL_UP_PIN)
        GPIO.remove_event_detect(VOL_DOWN_PIN)
