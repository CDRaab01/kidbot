import logging
import uuid
import requests
from .config import SERVER_URL

logger = logging.getLogger(__name__)

CHAT_URL  = f"{SERVER_URL}/chat"
HEALTH_URL = f"{SERVER_URL}/health"
SPEAK_URL  = f"{SERVER_URL}/speak"

# Canned phrases pre-fetched from the server at startup
OFFLINE_MESSAGE  = "I can't reach my brain right now! Please check the connection and try again."
ERROR_MESSAGE    = "Something went wrong on my end! Please try again in a moment."


class ServerClient:
    def __init__(self):
        self.session_id = str(uuid.uuid4())
        logger.info("Session ID: %s", self.session_id)

        # Pre-fetch audio clips at startup so the Pi can speak even if the
        # server later becomes temporarily unreachable mid-session
        self.offline_audio: bytes | None = None
        self.error_audio: bytes | None = None

    def prefetch_audio(self):
        """Call once after confirming server is reachable."""
        self.offline_audio = self._fetch_speech(OFFLINE_MESSAGE)
        self.error_audio   = self._fetch_speech(ERROR_MESSAGE)
        if self.offline_audio and self.error_audio:
            logger.info("Error audio clips cached.")

    def _fetch_speech(self, text: str) -> bytes | None:
        try:
            resp = requests.post(SPEAK_URL, data={"text": text}, timeout=30)
            if resp.status_code == 200:
                return resp.content
        except requests.RequestException as e:
            logger.warning("Could not pre-fetch audio clip: %s", e)
        return None

    def ping(self) -> bool:
        try:
            r = requests.get(HEALTH_URL, timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def send_audio(self, wav_path: str) -> bytes | None:
        """
        Send WAV to server.
        Returns MP3 bytes on success (including server-side spoken errors),
        or None if the server is completely unreachable.
        """
        try:
            with open(wav_path, "rb") as f:
                resp = requests.post(
                    CHAT_URL,
                    files={"audio": ("audio.wav", f, "audio/wav")},
                    data={"session_id": self.session_id},
                    timeout=60,
                )
            if resp.status_code == 200:
                return resp.content
            logger.error("Server returned %d: %s", resp.status_code, resp.text)
            return self.error_audio  # play cached error clip
        except requests.RequestException as e:
            logger.error("Connection error: %s", e)
            return self.offline_audio  # play cached offline clip
