import logging
import os
import signal
import sys
import time

from .audio import AudioManager
from .button import PushToTalkButton
from .client import ServerClient
from .config import SERVER_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)

button = PushToTalkButton()
audio = AudioManager()
client = ServerClient()

_busy = False  # prevents overlapping sessions


def on_press():
    global _busy
    if _busy:
        return
    _busy = True
    logger.info("PTT pressed — recording...")
    button.led(True)
    audio.start_recording()


def on_release():
    global _busy
    logger.info("PTT released — processing...")
    button.led(False)
    button.blink(count=2, interval=0.15)

    wav_path = audio.stop_recording()
    try:
        mp3_data = client.send_audio(wav_path)
        if mp3_data:
            logger.info("Playing KidBot response...")
            button.led(True)
            audio.play_mp3(mp3_data)
            button.led(False)
        else:
            logger.warning("No response received from server.")
            button.blink(count=5, interval=0.1)  # rapid blink = error
    finally:
        os.unlink(wav_path)
        _busy = False


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
