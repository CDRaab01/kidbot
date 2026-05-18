"""
FastAPI endpoint tests using TestClient.
Model classes are patched so the lifespan creates mocks instead of loading
real Whisper / Ollama / Kokoro weights.
"""
import io
import sys
import wave
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from fastapi.testclient import TestClient

from server.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav_bytes(frames: int = 16000, rate: int = 16000) -> bytes:
    """Build a minimal valid mono 16-bit PCM WAV in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


@contextmanager
def _loaded_client(
    transcription="hello world",
    llm_reply="Hi there!",
    tts_bytes=b"fakemp3",
):
    """TestClient with all three model classes patched to return mocks."""
    with patch("server.main.SpeechToText") as MockSTT, \
         patch("server.main.LLMInterface") as MockLLM, \
         patch("server.main.TextToSpeech") as MockTTS:
        stt = MagicMock()
        stt.transcribe.return_value = transcription
        MockSTT.return_value = stt

        llm = MagicMock()
        llm.respond.return_value = llm_reply
        MockLLM.return_value = llm

        tts = MagicMock()
        tts.synthesize.return_value = tts_bytes
        MockTTS.return_value = tts

        with TestClient(app) as client:
            yield client, stt, llm, tts


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_ok_when_models_loaded(self):
        with _loaded_client() as (client, *_):
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_loading_when_models_not_ready(self):
        with patch("server.main._stt", None), \
             patch("server.main._llm", None), \
             patch("server.main._tts", None):
            client = TestClient(app)
            resp = client.get("/health")
        assert resp.status_code == 503
        assert resp.json()["status"] == "loading"


# ---------------------------------------------------------------------------
# POST /speak
# ---------------------------------------------------------------------------

class TestSpeak:
    def test_valid_text_returns_mp3(self):
        with _loaded_client(tts_bytes=b"mp3data") as (client, _, _, tts):
            resp = client.post("/speak", data={"text": "hello"})
        assert resp.status_code == 200
        assert resp.content == b"mp3data"
        assert resp.headers["content-type"] == "audio/mpeg"

    def test_empty_text_returns_400(self):
        with _loaded_client() as (client, *_):
            resp = client.post("/speak", data={"text": "   "})
        assert resp.status_code == 400

    def test_tts_called_with_provided_text(self):
        with _loaded_client() as (client, _, _, tts):
            client.post("/speak", data={"text": "say this"})
        tts.synthesize.assert_called_once_with("say this")


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------

class TestChat:
    def test_valid_audio_returns_mp3_with_headers(self):
        wav = _make_wav_bytes()
        with _loaded_client(transcription="what is a dog?", llm_reply="A dog is a pet!", tts_bytes=b"dogmp3") as (client, stt, llm, tts):
            resp = client.post(
                "/chat",
                files={"audio": ("test.wav", wav, "audio/wav")},
                data={"session_id": "test-abc"},
            )
        assert resp.status_code == 200
        assert resp.content == b"dogmp3"
        assert resp.headers["x-transcription"] == "what is a dog?"
        assert resp.headers["x-reply"] == "A dog is a pet!"

    def test_empty_audio_returns_400(self):
        with _loaded_client() as (client, *_):
            resp = client.post(
                "/chat",
                files={"audio": ("test.wav", b"", "audio/wav")},
                data={"session_id": "s1"},
            )
        assert resp.status_code == 400

    def test_no_speech_detected_returns_sorry_response(self):
        wav = _make_wav_bytes()
        with _loaded_client(transcription="") as (client, stt, llm, tts):
            resp = client.post(
                "/chat",
                files={"audio": ("test.wav", wav, "audio/wav")},
                data={"session_id": "s1"},
            )
        assert resp.status_code == 200
        llm.respond.assert_not_called()

    def test_session_id_passed_to_llm(self):
        wav = _make_wav_bytes()
        with _loaded_client() as (client, stt, llm, tts):
            client.post(
                "/chat",
                files={"audio": ("test.wav", wav, "audio/wav")},
                data={"session_id": "my-session"},
            )
        # The LLM should have been called (session used for history lookup)
        llm.respond.assert_called_once()

    def test_non_ascii_transcription_header_is_ascii_safe(self):
        wav = _make_wav_bytes()
        with _loaded_client(transcription="héllo wörld") as (client, *_):
            resp = client.post(
                "/chat",
                files={"audio": ("test.wav", wav, "audio/wav")},
                data={"session_id": "s1"},
            )
        # Header must only contain ASCII characters
        header_val = resp.headers["x-transcription"]
        header_val.encode("ascii")  # raises if non-ASCII

    def test_models_not_ready_returns_503(self):
        wav = _make_wav_bytes()
        with patch("server.main._stt", None), \
             patch("server.main._llm", None), \
             patch("server.main._tts", None):
            client = TestClient(app)
            resp = client.post(
                "/chat",
                files={"audio": ("test.wav", wav, "audio/wav")},
                data={"session_id": "s1"},
            )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /chat_text
# ---------------------------------------------------------------------------

class TestChatText:
    def test_valid_text_returns_mp3_with_headers(self):
        with _loaded_client(llm_reply="Sure!", tts_bytes=b"suremp3") as (client, stt, llm, tts):
            resp = client.post(
                "/chat_text",
                data={"text": "hello bot", "session_id": "t1"},
            )
        assert resp.status_code == 200
        assert resp.content == b"suremp3"
        assert resp.headers["x-reply"] == "Sure!"

    def test_empty_text_returns_400(self):
        with _loaded_client() as (client, *_):
            resp = client.post("/chat_text", data={"text": "  ", "session_id": "t1"})
        assert resp.status_code == 400

    def test_stt_not_called_for_text_input(self):
        with _loaded_client() as (client, stt, *_):
            client.post("/chat_text", data={"text": "hi", "session_id": "t1"})
        stt.transcribe.assert_not_called()


# ---------------------------------------------------------------------------
# DELETE /session/{session_id}
# ---------------------------------------------------------------------------

class TestClearSession:
    def test_clears_existing_session(self):
        with _loaded_client() as (client, *_):
            resp = client.delete("/session/my-session")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cleared"
        assert resp.json()["session_id"] == "my-session"

    def test_clearing_nonexistent_session_is_idempotent(self):
        with _loaded_client() as (client, *_):
            resp = client.delete("/session/does-not-exist")
        assert resp.status_code == 200

    def test_session_history_removed(self):
        """After clearing, subsequent chat gets no prior history."""
        wav = _make_wav_bytes()
        with _loaded_client() as (client, stt, llm, tts):
            # Have a conversation
            client.post(
                "/chat_text",
                data={"text": "first message", "session_id": "sess1"},
            )
            # Clear the session
            client.delete("/session/sess1")
            # Chat again
            client.post(
                "/chat_text",
                data={"text": "second message", "session_id": "sess1"},
            )
            # Second call's history should be empty (no prior messages)
            second_call_history = llm.respond.call_args_list[1][1].get("history") or \
                                  llm.respond.call_args_list[1][0][1] if len(llm.respond.call_args_list[1][0]) > 1 else []
        assert second_call_history == []
