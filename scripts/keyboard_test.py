#!/usr/bin/env python3
"""Keyboard-driven KidBot test harness — no hardware buttons needed.

Run from the kidbot directory:
    python3 scripts/keyboard_test.py

Keys:
    SPACE   — first press starts recording; press again to stop & submit
              (auto-repeat is debounced — must hold for at least 0.5 s before
              a second SPACE is accepted as a stop command)
    + / =   — volume up
    -       — volume down
    q       — quit
"""
import os
import sys
import tty
import termios
import threading
import time
import logging

# Allow running from repo root or scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pi_client.audio import AudioManager
from pi_client.client import ServerClient
from pi_client.volume import VolumeRocker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger("keyboard_test")

# ── init subsystems ───────────────────────────────────────────────
audio = AudioManager()
client = ServerClient()
volume = VolumeRocker(on_change=lambda pct: logger.info("Volume: %d%%", pct))

_recording = False
_recording_start = 0.0
_MIN_RECORD_SECONDS = 0.5   # ignore SPACE auto-repeat within this window
_busy = threading.Lock()


def on_press():
    global _recording, _recording_start
    if not _busy.acquire(blocking=False):
        return
    _recording = True
    _recording_start = time.time()
    audio.start_recording()
    logger.info("🔴 Recording... (press SPACE to stop)")


def on_release():
    global _recording
    _recording = False
    logger.info("⏹  Stopped — processing...")
    try:
        wav_path = audio.stop_recording()
        chunk_iter = client.send_audio_stream(wav_path)
        if chunk_iter:
            logger.info("🔊 Playing response...")
            audio.play_mp3_stream(chunk_iter)
            logger.info("✅ Done.")
        else:
            logger.warning("No response from server.")
    except Exception:
        logger.exception("Error during processing")
    finally:
        try:
            os.unlink(wav_path)
        except Exception:
            pass
        _busy.release()


def getch():
    """Read a single character from stdin without waiting for Enter."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main():
    if client.ping():
        logger.info("Server reachable.")
        client.prefetch_audio()
    else:
        logger.warning("Server not reachable — responses may fail.")

    audio.play_startup_sound()

    print("\n  KidBot keyboard test")
    print("  SPACE = start / stop recording  |  + = vol up  |  - = vol down  |  q = quit\n")

    while True:
        ch = getch()

        if ch == " ":
            if not _recording:
                threading.Thread(target=on_press, daemon=True).start()
            elif time.time() - _recording_start >= _MIN_RECORD_SECONDS:
                threading.Thread(target=on_release, daemon=True).start()

        elif ch in ("+", "="):
            volume.step_up()
            logger.info("Volume up")

        elif ch == "-":
            volume.step_down()
            logger.info("Volume down")

        elif ch in ("q", "Q", "\x03"):  # q or Ctrl+C
            logger.info("Quitting.")
            if _recording:
                audio.stop_recording()
            audio.play_shutdown_sound()
            audio.cleanup()
            volume.cleanup()
            sys.exit(0)


if __name__ == "__main__":
    main()
