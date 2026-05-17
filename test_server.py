"""
End-to-end voice test for the KidBot server.
Records 4 seconds from your default mic, sends to server, plays the response.
Run from the kidbot folder: py test_server.py
"""
import os
import struct
import subprocess
import tempfile
import wave

import numpy as np
import sounddevice as sd
import requests

SERVER_URL = "http://localhost:8765"
SAMPLE_RATE = 16000
RECORD_SECONDS = 4
SESSION_ID = "test-session"  # fixed so history persists across test runs


def record_wav() -> str:
    print(f"Recording for {RECORD_SECONDS} seconds — say something...")
    audio = sd.rec(
        int(SAMPLE_RATE * RECORD_SECONDS),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
    )
    sd.wait()
    print("Done recording.")

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())

    return path


def send_audio(wav_path: str):
    print("Sending to server...")
    with open(wav_path, "rb") as f:
        resp = requests.post(
            f"{SERVER_URL}/chat",
            files={"audio": ("test.wav", f, "audio/wav")},
            data={"session_id": SESSION_ID},
            timeout=60,
        )

    print(f"Response: {resp.status_code} ({len(resp.content)} bytes)")

    if resp.status_code == 200:
        out = "test_response.mp3"
        with open(out, "wb") as f:
            f.write(resp.content)
        print("Playing response...")
        proc = subprocess.run(
            ["ffmpeg", "-i", out, "-f", "s16le", "-acodec", "pcm_s16le",
             "-ar", "22050", "-ac", "1", "-"],
            capture_output=True,
        )
        samples = np.frombuffer(proc.stdout, dtype=np.int16)
        sd.play(samples, samplerate=22050)
        sd.wait()
        os.unlink(out)
    else:
        print(f"Error: {resp.text}")


if __name__ == "__main__":
    wav = record_wav()
    try:
        send_audio(wav)
    finally:
        os.unlink(wav)
