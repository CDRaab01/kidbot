import logging
import logging.handlers
import os
import signal
import sys
import threading
import time

import RPi.GPIO as GPIO

from .audio import AudioManager
from .button import PushToTalkButton
from .client import ServerClient
from .config import BORED_AFTER_SECONDS, CURIOUS_DISPLAY_SECONDS, LOG_FILE, SERVER_URL
from .display import DisplayManager
from .volume import VolumeRocker


def _configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if LOG_FILE:
        from pathlib import Path
        Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        rh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5,
        )
        rh.setFormatter(fmt)
        root.addHandler(rh)


_configure_logging()
logger = logging.getLogger(__name__)

button = PushToTalkButton()
audio = AudioManager()
client = ServerClient()
display = DisplayManager()
def _on_volume_change(pct: int) -> None:
    _mark_activity()
    display.show_volume(pct)
    audio.play_volume_blip(pct)

volume_rocker = VolumeRocker(on_change=_on_volume_change)

_busy_lock = threading.Lock()  # prevents overlapping sessions

# Image-poll budget: the server fetches images in the background and may still
# be working when a short reply finishes playing.
_IMAGE_POLL_ATTEMPTS = 12
_IMAGE_POLL_INTERVAL = 0.5  # seconds

# Idle timer for the BORED face — last_activity is updated on every press.
_last_activity = time.time()
_bored = False
_BORED_CHECK_INTERVAL = 5  # seconds


def _mark_activity() -> None:
    """Reset the BORED idle timer (any button press or volume rocker tap)."""
    global _last_activity, _bored
    _last_activity = time.time()
    if _bored:
        _bored = False


def _bored_watcher() -> None:
    """Background timer that drifts the face to BORED after inactivity."""
    global _bored
    while True:
        time.sleep(_BORED_CHECK_INTERVAL)
        if BORED_AFTER_SECONDS <= 0:
            continue  # disabled
        if _busy_lock.locked():
            continue  # mid-conversation — never bored
        idle_for = time.time() - _last_activity
        if idle_for >= BORED_AFTER_SECONDS and not _bored:
            _bored = True
            logger.info("Idle for %ds — drifting to BORED.", int(idle_for))
            display.set_state("BORED")


def _poll_for_image():
    """Poll latest_image until a URL arrives or the fetch is no longer pending.

    Returns the image URL, or None if no image was produced. While polling
    we surface LOADING on the display so the child sees the bot is reaching
    for something rather than a frozen SPEAKING mouth; if the first poll
    already says nothing is coming, we exit immediately without changing
    state (the common no-image turn).
    """
    showed_loading = False
    for _ in range(_IMAGE_POLL_ATTEMPTS):
        url, pending = client.get_latest_image()
        if url:
            return url
        if not pending:
            return None
        if not showed_loading:
            display.set_state("LOADING")
            showed_loading = True
        time.sleep(_IMAGE_POLL_INTERVAL)
    return None


def _ends_with_question(text: str) -> bool:
    """True if the bot's reply ends with a question mark (ignoring trailing
    whitespace and a quote mark). Used to dispatch the CURIOUS face state."""
    if not text:
        return False
    stripped = text.rstrip().rstrip("\"'”’")
    return stripped.endswith("?")


def on_press():
    if not _busy_lock.acquire(blocking=False):
        return
    _mark_activity()
    try:
        logger.info("PTT pressed — recording...")
        button.led(True)
        display.set_state("LISTENING")
        audio.start_recording()
    except Exception:
        logger.exception("on_press failed")
        _busy_lock.release()


def on_release():
    # Only handle a release that pairs with a press we accepted. GPIO.BOTH plus
    # contact bounce can deliver an unpaired release (or a release whose press
    # was rejected as busy); without this guard the finally block below would
    # call release() on an unheld lock and raise RuntimeError.
    if not _busy_lock.locked():
        logger.debug("Ignoring unpaired button release.")
        return

    logger.info("PTT released — processing...")
    button.led(False)
    button.blink(count=2, interval=0.15)
    display.set_state("THINKING")

    try:
        wav_path = audio.stop_recording()
    except Exception:
        logger.exception("stop_recording failed")
        _busy_lock.release()
        return
    try:
        chunk_iter = client.send_audio_stream(wav_path)
        if chunk_iter is not None:
            logger.info("Streaming KidBot response...")
            button.led(True)
            display.set_state("SPEAKING")
            audio.play_mp3_stream(chunk_iter)
            button.led(False)
            # The image is fetched server-side as a background task that can lag
            # behind a short reply's playback. Poll until it arrives or the
            # server reports the fetch is no longer pending. Non-image turns
            # return pending=False on the first poll, so there's no added delay.
            image_url = _poll_for_image()
            if image_url:
                logger.info("Showing image: %s", image_url)
                display.show_image_url(image_url)
            else:
                # If the bot ended with a question, show the CURIOUS "hm?"
                # face instead of HAPPY so the child sees they're being
                # invited to respond.
                reply = client.get_latest_reply()
                if _ends_with_question(reply):
                    display.set_state("CURIOUS")
                    time.sleep(CURIOUS_DISPLAY_SECONDS)
                else:
                    display.set_state("HAPPY")
                    time.sleep(1.5)
                display.set_state("IDLE")
        else:
            logger.warning("No response from server — playing fallback clip.")
            display.set_state("ERROR")
            button.blink(count=5, interval=0.1)
            fallback = client.offline_audio or client.error_audio
            if fallback:
                audio.play_mp3(fallback)
            time.sleep(2)
            display.set_state("IDLE")
    finally:
        # Guard unlink so a missing temp file can never skip the release below
        # and permanently wedge the button.
        try:
            os.unlink(wav_path)
        except OSError:
            pass
        _busy_lock.release()


def shutdown(sig=None, _frame=None):
    logger.info("Shutting down KidBot.")
    audio.stop_playback()
    audio.play_shutdown_sound()
    display.cleanup()
    volume_rocker.cleanup()
    button.cleanup()
    GPIO.cleanup()
    audio.cleanup()
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("KidBot starting. Server: %s", SERVER_URL)

    if client.ping():
        logger.info("Server reachable.")
        client.prefetch_audio()
    else:
        logger.warning("Server not reachable — will retry when button is pressed.")

    button.on_press(on_press)
    button.on_release(on_release)

    display.set_state("IDLE")
    button.blink(count=3, interval=0.3)  # three slow blinks = ready
    audio.play_startup_sound()
    # Start the BORED idle watcher in the background.
    threading.Thread(target=_bored_watcher, daemon=True, name="bored-watcher").start()
    logger.info("Ready. Hold the button to talk.")

    while True:
        time.sleep(0.1)


if __name__ == "__main__":
    main()
