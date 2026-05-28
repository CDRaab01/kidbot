import logging
import os
import subprocess
import tempfile
import threading
import wave

import pyaudio

from .config import ALSA_CONTROL, CHANNELS, CHUNK_SIZE, MAX_RECORD_SECONDS, MIC_DEVICE_HINT, SAMPLE_RATE, STARTUP_VOLUME

logger = logging.getLogger(__name__)


class AudioManager:
    def __init__(self):
        # Suppress ALSA/JACK noise on stderr during device enumeration
        import ctypes
        try:
            asound = ctypes.cdll.LoadLibrary("libasound.so.2")
            asound.snd_lib_error_set_handler(None)
        except Exception:
            pass
        self._pa = pyaudio.PyAudio()
        self._device_index = self._find_mic()
        self._recording = False
        self._frames: list[bytes] = []
        self._stream: pyaudio.Stream | None = None
        self._playback_proc: subprocess.Popen | None = None
        self._playback_lock = threading.Lock()

        # Pre-open the mic stream so the ADC sigma-delta HPF settles before
        # the first button press.  The idle loop drains the hardware buffer
        # continuously and starts saving frames only when _recording=True.
        if self._device_index is not None:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self._device_index,
                frames_per_buffer=CHUNK_SIZE,
            )
            threading.Thread(target=self._idle_loop, daemon=True).start()
            logger.info("Mic stream pre-opened; ADC warming up.")

    def _find_mic(self) -> int | None:
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if MIC_DEVICE_HINT in info["name"].lower() and info["maxInputChannels"] > 0:
                logger.info("Using mic device %d: %s", i, info["name"])
                return i
        logger.warning("ReSpeaker device not found — using system default mic")
        return None

    def _idle_loop(self):
        """Drain mic buffer continuously; save frames to _frames when _recording=True."""
        max_chunks = int(SAMPLE_RATE / CHUNK_SIZE * MAX_RECORD_SECONDS)
        record_count = 0
        while self._stream is not None:
            try:
                data = self._stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except (OSError, IOError):
                break
            if self._recording:
                self._frames.append(data)
                record_count += 1
                if record_count >= max_chunks:
                    logger.warning("Max recording length reached — auto-stopping.")
                    self._recording = False
                    record_count = 0
            else:
                record_count = 0

    def start_recording(self):
        self._frames = []
        if self._stream is None:
            # Fallback: device wasn't found at init (e.g. default mic)
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self._device_index,
                frames_per_buffer=CHUNK_SIZE,
            )
            threading.Thread(target=self._idle_loop, daemon=True).start()
        self._recording = True
        logger.info("Recording started.")

    def stop_recording(self) -> str:
        """Stop recording and return path to a temp WAV file (caller must delete it)."""
        self._recording = False
        # Keep the stream open so the ADC stays warm for the next recording.

        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(self._pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"".join(self._frames))

        logger.info("Saved recording: %s (%d frames)", path, len(self._frames))
        return path

    def play_startup_sound(self):
        """Generate 8-bit startup jingle (once) and play it through the speaker."""
        import math
        import struct as _struct
        sound_path = os.path.join(os.path.dirname(__file__), "startup.wav")
        if not os.path.exists(sound_path):
            R = 48000

            def sq(f, dur, v=0.45, a=40, r=1200):
                n = int(R * dur)
                return [
                    ((math.sin(6.28 * f * i / R) > 0) * 0.8 - 0.4
                     + sum(math.sin(6.28 * f * (2*k-1) * i / R) / (2*k-1) for k in range(1, 4)) / 3)
                    * v * min(1, i / a) * min(1, (n - i) / r)
                    for i in range(n)
                ]

            def mx(buf, pos, wave_data):
                for i, v in enumerate(wave_data):
                    idx = pos + i
                    if idx < len(buf):
                        buf[idx] = max(-1.0, min(1.0, buf[idx] + v))

            buf = [0.0] * int(R * 4)
            for f, t in [(262,.0),(330,.18),(392,.36),(523,.56),(659,.76),(784,.96),(1047,1.2)]:
                mx(buf, int(R * t), sq(f, 0.3))
            for f in [523, 659, 784, 1047]:
                mx(buf, int(R * 1.6), sq(f, 1.8, 0.28, 40, 7000))
            n = int(R * 0.08)
            hit = [((i % 3 == 0) * 2 - 1) * 0.35 * min(1, i / 20) * min(1, (n - i) / 300)
                   for i in range(n)]
            mx(buf, int(R * 1.6), hit)

            with wave.open(sound_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(R)
                for v in buf:
                    s = max(-32768, min(32767, int(v * 27000)))
                    wf.writeframes(_struct.pack("<h", s))
            logger.info("Generated startup sound: %s", sound_path)

        with self._chime_volume():
            subprocess.run(["aplay", "-D", "plughw:1,0", sound_path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def play_shutdown_sound(self):
        """Generate 8-bit shutdown chime (once) and play it through the speaker."""
        import math
        import struct as _struct
        sound_path = os.path.join(os.path.dirname(__file__), "shutdown.wav")
        if not os.path.exists(sound_path):
            R = 48000

            def sq(f, dur, v=0.45, a=40, r=1200):
                n = int(R * dur)
                return [
                    ((math.sin(6.28 * f * i / R) > 0) * 0.8 - 0.4
                     + sum(math.sin(6.28 * f * (2*k-1) * i / R) / (2*k-1) for k in range(1, 4)) / 3)
                    * v * min(1, i / a) * min(1, (n - i) / r)
                    for i in range(n)
                ]

            def mx(buf, pos, wave_data):
                for i, v in enumerate(wave_data):
                    idx = pos + i
                    if idx < len(buf):
                        buf[idx] = max(-1.0, min(1.0, buf[idx] + v))

            buf = [0.0] * int(R * 3.5)
            for f, t in [(1047,.0),(784,.18),(659,.36),(523,.56),(392,.76),(262,.96)]:
                mx(buf, int(R * t), sq(f, 0.3))
            for f in [262, 392, 523]:
                mx(buf, int(R * 1.3), sq(f, 1.8, 0.22, 40, 9000))
            n = int(R * 0.08)
            hit = [((i % 3 == 0) * 2 - 1) * 0.3 * min(1, i / 20) * min(1, (n - i) / 300)
                   for i in range(n)]
            mx(buf, int(R * 1.3), hit)

            with wave.open(sound_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(R)
                for v in buf:
                    s = max(-32768, min(32767, int(v * 27000)))
                    wf.writeframes(_struct.pack("<h", s))
            logger.info("Generated shutdown sound: %s", sound_path)

        with self._chime_volume():
            subprocess.run(["aplay", "-D", "plughw:1,0", sound_path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _chime_volume(self):
        """Context manager: set PCM to STARTUP_VOLUME, restore on exit."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            try:
                out = subprocess.check_output(
                    ["amixer", "sget", ALSA_CONTROL], text=True, stderr=subprocess.DEVNULL
                )
                import re
                m = re.search(r"\[(\d+)%\]", out)
                prev = m.group(1) if m else None
            except Exception:
                prev = None
            subprocess.run(
                ["amixer", "sset", ALSA_CONTROL, f"{STARTUP_VOLUME}%"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                yield
            finally:
                if prev is not None:
                    subprocess.run(
                        ["amixer", "sset", ALSA_CONTROL, f"{prev}%"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )

        return _ctx()

    def play_volume_blip(self, pct: int) -> None:
        """Play a short pitch-scaled blip to confirm a volume change.

        Frequency scales on a log curve from ~300 Hz (0 %) to ~1200 Hz (100 %)
        so the blip audibly reflects the new level.  Runs synchronously but
        only takes ~80 ms; call from a daemon thread so it doesn't block.
        Silently ignored if the audio device is busy.
        """
        import math
        import struct as _struct

        # Re-assert PCM level — mpg123 can reset it to 0 on exit
        subprocess.run(
            ["amixer", "sset", ALSA_CONTROL, f"{pct}%"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        R = 48000
        n = int(R * 0.08)                          # 80 ms
        freq = 300.0 * (4.0 ** (pct / 100.0))      # 300 Hz → 1200 Hz log
        attack  = max(1, int(R * 0.008))            # 8 ms attack
        release = max(1, int(R * 0.025))            # 25 ms release
        buf = bytearray(n * 2)
        for i in range(n):
            env = min(1.0, i / attack) * min(1.0, (n - i) / release)
            v   = math.sin(2 * math.pi * freq * i / R) * env * 0.55
            _struct.pack_into("<h", buf, i * 2, max(-32768, min(32767, int(v * 32767))))
        proc = subprocess.Popen(
            ["aplay", "-D", "plughw:1,0", "-f", "S16_LE", "-r", "48000", "-c", "1", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            proc.stdin.write(bytes(buf))
            proc.stdin.close()
            _, stderr = proc.communicate()
            if proc.returncode != 0:
                logger.warning("Volume blip aplay failed (rc=%d): %s", proc.returncode, stderr.decode().strip())
        except (BrokenPipeError, OSError) as e:
            logger.warning("Volume blip error: %s", e)
            proc.kill()

    def stop_playback(self) -> None:
        """Kill any active mpg123 playback immediately."""
        with self._playback_lock:
            proc = self._playback_proc
            self._playback_proc = None
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass

    def play_mp3(self, mp3_data: bytes):
        """Write MP3 to a temp file and play it via mpg123."""
        fd, path = tempfile.mkstemp(suffix=".mp3")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(mp3_data)
            proc = subprocess.Popen(
                ["mpg123", "-q", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with self._playback_lock:
                self._playback_proc = proc
            proc.wait()
        finally:
            with self._playback_lock:
                if self._playback_proc is proc:
                    self._playback_proc = None
            os.unlink(path)

    def play_mp3_stream(self, chunks) -> None:
        """Pipe a streaming MP3 chunk iterator to mpg123 via stdin for low-latency playback."""
        proc = subprocess.Popen(
            ["mpg123", "-q", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with self._playback_lock:
            self._playback_proc = proc
        try:
            for chunk in chunks:
                if proc.poll() is not None:
                    break  # killed externally
                proc.stdin.write(chunk)
            proc.stdin.close()
            proc.wait()
        except (BrokenPipeError, OSError):
            proc.kill()
        finally:
            with self._playback_lock:
                if self._playback_proc is proc:
                    self._playback_proc = None

    def cleanup(self):
        self._recording = False
        stream = self._stream
        self._stream = None  # Signal _idle_loop to exit
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        self._pa.terminate()
