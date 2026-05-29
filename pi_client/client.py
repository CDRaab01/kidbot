import logging
import time
import uuid

import requests

from .config import API_KEY, SERVER_URL

logger = logging.getLogger(__name__)

CHAT_URL   = f"{SERVER_URL}/chat"
HEALTH_URL = f"{SERVER_URL}/health"
SPEAK_URL  = f"{SERVER_URL}/speak"

# Canned phrases pre-fetched from the server at startup
OFFLINE_MESSAGE = "I can't reach my brain right now! Please check the connection and try again."
ERROR_MESSAGE   = "Something went wrong on my end! Please try again in a moment."

_MAX_RETRIES   = 2
_RETRY_DELAYS  = (1, 2)  # seconds between retry attempts


class ServerClient:
    def __init__(self):
        self.session_id = str(uuid.uuid4())
        logger.info("Session ID: %s", self.session_id)
        self.offline_audio: bytes | None = None
        self.error_audio:   bytes | None = None

    @property
    def _headers(self) -> dict:
        return {"X-API-Key": API_KEY} if API_KEY else {}

    def _post_with_retry(self, url: str, **kwargs) -> "requests.Response | None":
        """POST with automatic retry on connection errors; returns None on all failures."""
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return requests.post(url, headers=self._headers, **kwargs)
            except requests.Timeout:
                logger.error("Request timed out (attempt %d/%d)", attempt + 1, _MAX_RETRIES + 1)
                return None
            except requests.ConnectionError as exc:
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning("Connection error (attempt %d/%d), retrying in %ds: %s",
                                   attempt + 1, _MAX_RETRIES + 1, delay, exc)
                    time.sleep(delay)
                else:
                    logger.error("Connection failed after %d attempts: %s",
                                 _MAX_RETRIES + 1, exc)
        return None

    def prefetch_audio(self):
        """Call once after confirming server is reachable."""
        self.offline_audio = self._fetch_speech(OFFLINE_MESSAGE)
        self.error_audio   = self._fetch_speech(ERROR_MESSAGE)
        if self.offline_audio and self.error_audio:
            logger.info("Error audio clips cached.")

    def _fetch_speech(self, text: str) -> bytes | None:
        resp = self._post_with_retry(SPEAK_URL, data={"text": text}, timeout=30)
        if resp and resp.status_code == 200:
            return resp.content
        return None

    def ping(self) -> bool:
        try:
            r = requests.get(HEALTH_URL, timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def send_audio(self, wav_path: str) -> bytes | None:
        """
        Send WAV to server, returning MP3 bytes on success.
        Falls back to cached offline audio on network failure, or cached
        error audio if the server responds with a non-200 status.
        """
        with open(wav_path, "rb") as f:
            wav_data = f.read()

        resp = self._post_with_retry(
            CHAT_URL,
            files={"audio": ("audio.wav", wav_data, "audio/wav")},
            data={"session_id": self.session_id},
            timeout=60,
        )
        if resp is None:
            logger.warning("Server unreachable — playing offline clip.")
            return self.offline_audio
        if resp.status_code == 200:
            return resp.content
        logger.error("Server returned %d: %s", resp.status_code, resp.text)
        return self.error_audio

    def get_latest_image(self) -> tuple[str | None, bool]:
        """Poll the server for an image URL generated during the last exchange.

        Returns (image_url_or_None, pending). `pending` is True when the server
        is still fetching an image, so the caller should poll again.
        """
        try:
            resp = requests.get(
                f"{SERVER_URL}/session/{self.session_id}/latest_image",
                headers=self._headers,
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                return (data.get("image_url") or None, bool(data.get("pending")))
        except requests.RequestException as exc:
            logger.warning("Could not fetch latest image: %s", exc)
        return None, False

    @staticmethod
    def _iter_and_close(resp):
        """Yield chunks then always release the connection, even if the consumer
        stops early (e.g. playback is interrupted)."""
        try:
            for chunk in resp.iter_content(chunk_size=4096):
                yield chunk
        finally:
            resp.close()

    def send_audio_stream(self, wav_path: str):
        """
        Send WAV to /chat_stream. Returns a chunk iterator on success, or None on
        any failure (caller should fall back to cached audio).
        """
        with open(wav_path, "rb") as f:
            wav_data = f.read()
        try:
            resp = requests.post(
                f"{SERVER_URL}/chat_stream",
                files={"audio": ("audio.wav", wav_data, "audio/wav")},
                data={"session_id": self.session_id},
                headers=self._headers,
                timeout=60,
                stream=True,
            )
            if resp.status_code == 200:
                return self._iter_and_close(resp)
            logger.error("Stream endpoint returned %d", resp.status_code)
            resp.close()
            return None
        except requests.RequestException as exc:
            logger.error("Stream connection error: %s", exc)
            return None
