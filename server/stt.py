import logging
from faster_whisper import WhisperModel
from .config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE

logger = logging.getLogger(__name__)


class SpeechToText:
    def __init__(self):
        logger.info("Loading Whisper model: %s on %s", WHISPER_MODEL, WHISPER_DEVICE)
        self.model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
        logger.info("Whisper ready.")

    def transcribe(self, audio_path: str) -> str:
        segments, _info = self.model.transcribe(
            audio_path,
            beam_size=1,
            language="en",
            vad_filter=True,
            vad_parameters={"threshold": 0.2},  # permissive — catches quiet speech
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        logger.info("Transcribed: %r", text)
        return text
