import logging
import os
import subprocess
import tempfile
import threading
import wave

import pyaudio

from .config import CHANNELS, CHUNK_SIZE, MAX_RECORD_SECONDS, MIC_DEVICE_HINT, SAMPLE_RATE

logger = logging.getLogger(__name__)


class AudioManager:
    def __init__(self):
        self._pa = pyaudio.PyAudio()
        self._device_index = self._find_mic()
        self._recording = False
        self._frames: list[bytes] = []
        self._stream: pyaudio.Stream | None = None

    def _find_mic(self) -> int | None:
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if MIC_DEVICE_HINT in info["name"].lower() and info["maxInputChannels"] > 0:
                logger.info("Using mic device %d: %s", i, info["name"])
                return i
        logger.warning("ReSpeaker device not found — using system default mic")
        return None

    def start_recording(self):
        self._frames = []
        self._recording = True
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=self._device_index,
            frames_per_buffer=CHUNK_SIZE,
        )
        threading.Thread(target=self._capture_loop, daemon=True).start()
        logger.info("Recording started.")

    def _capture_loop(self):
        max_chunks = int(SAMPLE_RATE / CHUNK_SIZE * MAX_RECORD_SECONDS)
        count = 0
        while self._recording and count < max_chunks:
            data = self._stream.read(CHUNK_SIZE, exception_on_overflow=False)
            self._frames.append(data)
            count += 1
        if count >= max_chunks:
            logger.warning("Max recording length reached — auto-stopping.")
            self._recording = False

    def stop_recording(self) -> str:
        """Stop recording and return path to a temp WAV file (caller must delete it)."""
        self._recording = False
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(self._pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"".join(self._frames))

        logger.info("Saved recording: %s (%d frames)", path, len(self._frames))
        return path

    def play_mp3(self, mp3_data: bytes):
        """Write MP3 to a temp file and play it via mpg123."""
        fd, path = tempfile.mkstemp(suffix=".mp3")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(mp3_data)
            subprocess.run(["mpg123", "-q", path], check=True)
        finally:
            os.unlink(path)

    def play_mp3_stream(self, chunks) -> None:
        """Pipe a streaming MP3 chunk iterator to mpg123 via stdin for low-latency playback."""
        proc = subprocess.Popen(
            ["mpg123", "-q", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for chunk in chunks:
                proc.stdin.write(chunk)
            proc.stdin.close()
            proc.wait()
        except (BrokenPipeError, OSError):
            proc.kill()

    def cleanup(self):
        self._pa.terminate()
