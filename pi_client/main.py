import logging
import logging.handlers
import os
import signal
import sys
import threading
import time

from .audio import AudioManager
from .button import PushToTalkButton
from .client import ServerClient
from .config import LOG_FILE, SERVER_URL


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

_busy_lock = threading.Lock()  # prevents overlapping sessions


def on_press():
    if not _busy_lock.acquire(blocking=False):
        return
    logger.info("PTT pressed — recording...")
    button.led(True)
    audio.start_recording()


def on_release():
    logger.info("PTT released — processing...")
    button.led(False)
    button.blink(count=2, interval=0.15)

    wav_path = audio.stop_recording()
    try:
        chunk_iter = client.send_audio_stream(wav_path)
        if chunk_iter is not None:
            logger.info("Streaming KidBot response...")
            button.led(True)
            audio.play_mp3_stream(chunk_iter)
            button.led(False)
        else:
            logger.warning("No response from server — playing fallback clip.")
            button.blink(count=5, interval=0.1)
            fallback = client.offline_audio or client.error_audio
            if fallback:
                audio.play_mp3(fallback)
    finally:
        os.unlink(wav_path)
        _busy_lock.release()


def shutdown(sig=None, _frame=None):
    logger.info("Shutting down KidBot.")
    button.cleanup()
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

    button.blink(count=3, interval=0.3)  # three slow blinks = ready
    logger.info("Ready. Hold the button to talk.")

    while True:
        time.sleep(0.1)


if __name__ == "__main__":
    main()
