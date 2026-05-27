import logging
import os
import re
import subprocess
import tempfile

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

from .config import KOKORO_MODEL_PATH, KOKORO_VOICES_PATH, KOKORO_VOICE, KOKORO_SPEED, TEMP_DIR

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000  # Kokoro always outputs at 24 kHz

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001F9FF"
    "\U00010000-\U0010FFFF"
    "☀-➿"
    "⬀-⯿"
    "‍"
    "️]",
    flags=re.UNICODE,
)


def clean_for_speech(text: str) -> str:
    text = _EMOJI_RE.sub("", text)
    text = text.replace("‘", "'").replace("’", "'")  # curly apostrophes
    text = text.replace("“", "").replace("”", "")    # curly double quotes
    text = re.sub(r"[—–]", ", ", text)               # em/en dash -> pause
    text = re.sub(r"[*#_`]", "", text)                         # markdown symbols
    text = re.sub(r"\(.*?\)", "", text)                        # parenthetical asides
    text = re.sub(r"[\[\]{}]", "", text)                       # brackets
    text = re.sub(r"\s{2,}", " ", text)                        # collapse spaces
    return text.strip()


class TextToSpeech:
    def __init__(self):
        logger.info("Loading Kokoro ONNX TTS...")
        self.kokoro = Kokoro(KOKORO_MODEL_PATH, KOKORO_VOICES_PATH)
        self.voice = KOKORO_VOICE
        self.speed = KOKORO_SPEED
        logger.info("TTS ready. Voice: %s  Speed: %s", self.voice, self.speed)

    def available_voices(self) -> list[str]:
        """Return sorted list of voice names from the voices file."""
        try:
            data = np.load(KOKORO_VOICES_PATH)
            return sorted(data.files)
        except Exception:
            return [self.voice]

    def set_voice(self, voice: str) -> None:
        self.voice = voice
        logger.info("Voice changed to: %s", voice)

    def set_speed(self, speed: float) -> None:
        self.speed = max(0.5, min(2.0, speed))
        logger.info("Speed changed to: %.2f", self.speed)

    def synthesize(self, text: str) -> bytes:
        """Return MP3 bytes for the given text."""
        text = clean_for_speech(text)
        if not text:
            text = "Hmm."

        samples, sample_rate = self.kokoro.create(
            text,
            voice=self.voice,
            speed=self.speed,
            lang="en-gb",
        )

        wav_fd, wav_path = tempfile.mkstemp(suffix=".wav", dir=TEMP_DIR)
        mp3_path = wav_path.replace(".wav", ".mp3")
        os.close(wav_fd)

        try:
            sf.write(wav_path, samples, sample_rate)
            try:
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", wav_path,
                        "-codec:a", "libmp3lame", "-qscale:a", "4",
                        mp3_path,
                    ],
                    capture_output=True,
                    timeout=30,
                    check=True,
                )
            except subprocess.CalledProcessError as exc:
                logger.error(
                    "ffmpeg failed (rc=%d): %s",
                    exc.returncode,
                    exc.stderr.decode(errors="replace"),
                )
                raise
            except subprocess.TimeoutExpired:
                logger.error("ffmpeg timed out after 30 s")
                raise
            with open(mp3_path, "rb") as f:
                return f.read()
        finally:
            for p in (wav_path, mp3_path):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass
