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
        logger.info("TTS ready. Voice: %s  Speed: %s", KOKORO_VOICE, KOKORO_SPEED)

    def synthesize(self, text: str) -> bytes:
        """Return MP3 bytes for the given text."""
        text = clean_for_speech(text)
        if not text:
            text = "Hmm."

        samples, sample_rate = self.kokoro.create(
            text,
            voice=KOKORO_VOICE,
            speed=KOKORO_SPEED,
            lang="en-gb",
        )

        wav_fd, wav_path = tempfile.mkstemp(suffix=".wav", dir=TEMP_DIR)
        mp3_path = wav_path.replace(".wav", ".mp3")
        os.close(wav_fd)

        try:
            sf.write(wav_path, samples, sample_rate)
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
            with open(mp3_path, "rb") as f:
                return f.read()
        finally:
            for p in (wav_path, mp3_path):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass
