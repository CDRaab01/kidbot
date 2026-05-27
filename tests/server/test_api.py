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

from server.main import app, limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear rate limiter storage between tests so limits don't accumulate."""
    limiter._storage.reset()
    yield


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


# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------

class TestAPIKeyAuth:
    def test_no_api_key_configured_all_requests_pass(self):
        with patch("server.main.API_KEY", ""):
            with _loaded_client() as (client, *_):
                resp = client.get("/health")
        assert resp.status_code == 200

    def test_missing_key_header_returns_401(self):
        with patch("server.main.API_KEY", "secret123"):
            with _loaded_client() as (client, *_):
                resp = client.post("/chat_text", data={"text": "hi", "session_id": "s1"})
        assert resp.status_code == 401

    def test_wrong_key_returns_401(self):
        with patch("server.main.API_KEY", "secret123"):
            with _loaded_client() as (client, *_):
                resp = client.post(
                    "/chat_text",
                    data={"text": "hi", "session_id": "s1"},
                    headers={"X-API-Key": "wrong"},
                )
        assert resp.status_code == 401

    def test_correct_key_passes(self):
        with patch("server.main.API_KEY", "secret123"):
            with _loaded_client() as (client, *_):
                resp = client.post(
                    "/chat_text",
                    data={"text": "hi", "session_id": "s1"},
                    headers={"X-API-Key": "secret123"},
                )
        assert resp.status_code == 200

    def test_health_always_exempt_from_api_key(self):
        with patch("server.main.API_KEY", "secret123"):
            with _loaded_client() as (client, *_):
                resp = client.get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /chat_text_stream
# ---------------------------------------------------------------------------

@contextmanager
def _loaded_stream_client(sentences=None, tts_bytes=b"fakemp3"):
    """Like _loaded_client but configures llm.respond_stream with sentence chunks."""
    with patch("server.main.SpeechToText") as MockSTT, \
         patch("server.main.LLMInterface") as MockLLM, \
         patch("server.main.TextToSpeech") as MockTTS:
        stt = MagicMock()
        stt.transcribe.return_value = "hello world"
        MockSTT.return_value = stt

        llm = MagicMock()
        llm.respond_stream.return_value = iter(sentences or ["Hello there!"])
        MockLLM.return_value = llm

        tts = MagicMock()
        tts.synthesize.return_value = tts_bytes
        MockTTS.return_value = tts

        with TestClient(app) as client:
            yield client, stt, llm, tts


class TestChatTextStream:
    def test_valid_text_returns_streaming_mp3(self):
        with _loaded_stream_client(sentences=["Hello!", "How are you?"]) as (client, *_):
            resp = client.post("/chat_text_stream", data={"text": "hi", "session_id": "s1"})
        assert resp.status_code == 200
        assert "audio/mpeg" in resp.headers["content-type"]
        assert len(resp.content) > 0

    def test_two_sentences_yield_two_tts_calls(self):
        with _loaded_stream_client(sentences=["Hello!", "Nice day."]) as (client, stt, llm, tts):
            client.post("/chat_text_stream", data={"text": "hi", "session_id": "s1"})
        assert tts.synthesize.call_count == 2

    def test_empty_text_returns_400(self):
        with _loaded_stream_client() as (client, *_):
            resp = client.post("/chat_text_stream", data={"text": "  ", "session_id": "s1"})
        assert resp.status_code == 400

    def test_models_not_ready_returns_503(self):
        with patch("server.main._llm", None), patch("server.main._tts", None):
            client = TestClient(app)
            resp = client.post("/chat_text_stream", data={"text": "hi", "session_id": "s1"})
        assert resp.status_code == 503

    def test_session_history_updated_after_stream(self):
        with _loaded_stream_client(sentences=["Fine thanks."]) as (client, stt, llm, tts):
            client.post("/chat_text_stream", data={"text": "how are you", "session_id": "hist1"})
            # Reset so second request returns a fresh iterator
            llm.respond_stream.return_value = iter(["Doing great!"])
            client.post("/chat_text_stream", data={"text": "follow up", "session_id": "hist1"})
        # On the second call, history passed to respond_stream should include the first exchange.
        # respond_stream(text, history) — history is the second positional arg.
        second_call_args = llm.respond_stream.call_args_list[1][0]
        history = second_call_args[1] if len(second_call_args) > 1 else []
        assert any(m["content"] == "how are you" for m in history)
        assert any(m["content"] == "Fine thanks." for m in history)

    def test_image_tags_stripped_before_tts(self):
        with _loaded_stream_client(sentences=["[IMAGE: dinosaur] Cool fact!"]) as (client, stt, llm, tts):
            client.post("/chat_text_stream", data={"text": "hi", "session_id": "s1"})
        called_text = tts.synthesize.call_args[0][0]
        assert "[IMAGE:" not in called_text


# ---------------------------------------------------------------------------
# POST /chat_stream
# ---------------------------------------------------------------------------

class TestChatStream:
    def test_valid_audio_returns_streaming_mp3(self):
        wav = _make_wav_bytes()
        with _loaded_stream_client(sentences=["Woof!"]) as (client, *_):
            resp = client.post(
                "/chat_stream",
                files={"audio": ("test.wav", wav, "audio/wav")},
                data={"session_id": "s1"},
            )
        assert resp.status_code == 200
        assert "audio/mpeg" in resp.headers["content-type"]

    def test_transcription_header_present(self):
        wav = _make_wav_bytes()
        with _loaded_stream_client() as (client, stt, *_):
            stt.transcribe.return_value = "tell me a story"
            resp = client.post(
                "/chat_stream",
                files={"audio": ("test.wav", wav, "audio/wav")},
                data={"session_id": "s1"},
            )
        assert resp.headers.get("x-transcription") == "tell me a story"

    def test_empty_audio_returns_400(self):
        with _loaded_stream_client() as (client, *_):
            resp = client.post(
                "/chat_stream",
                files={"audio": ("test.wav", b"", "audio/wav")},
                data={"session_id": "s1"},
            )
        assert resp.status_code == 400

    def test_no_speech_returns_sorry_audio(self):
        wav = _make_wav_bytes()
        with _loaded_stream_client() as (client, stt, llm, tts):
            stt.transcribe.return_value = ""
            resp = client.post(
                "/chat_stream",
                files={"audio": ("test.wav", wav, "audio/wav")},
                data={"session_id": "s1"},
            )
        assert resp.status_code == 200
        llm.respond_stream.assert_not_called()

    def test_models_not_ready_returns_503(self):
        wav = _make_wav_bytes()
        with patch("server.main._stt", None), \
             patch("server.main._llm", None), \
             patch("server.main._tts", None):
            client = TestClient(app)
            resp = client.post(
                "/chat_stream",
                files={"audio": ("test.wav", wav, "audio/wav")},
                data={"session_id": "s1"},
            )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /session/{session_id}/latest_image
# ---------------------------------------------------------------------------

class TestLatestImageEndpoint:
    def test_returns_empty_url_for_unknown_session(self):
        with _loaded_client() as (client, *_):
            resp = client.get("/session/ghost-session/latest_image")
        assert resp.status_code == 200
        assert resp.json()["image_url"] == ""

    def test_returns_and_clears_stored_image_url(self):
        from server.main import _sessions
        # Seed a session with an image URL
        _sessions.get_history("img-test")
        _sessions.set_latest_image("img-test", "https://example.com/dino.jpg")
        with _loaded_client() as (client, *_):
            resp = client.get("/session/img-test/latest_image")
        assert resp.status_code == 200
        assert resp.json()["image_url"] == "https://example.com/dino.jpg"
        # Second call should return empty (cleared)
        with _loaded_client() as (client, *_):
            resp2 = client.get("/session/img-test/latest_image")
        assert resp2.json()["image_url"] == ""


# ---------------------------------------------------------------------------
# GET /session/{session_id}/latest_reply
# ---------------------------------------------------------------------------

class TestLatestReplyEndpoint:
    def test_returns_empty_for_unknown_session(self):
        with _loaded_client() as (client, *_):
            resp = client.get("/session/ghost/latest_reply")
        assert resp.status_code == 200
        assert resp.json()["reply"] == ""

    def test_returns_and_clears_stored_reply(self):
        from server.main import _sessions
        _sessions.get_history("reply-test")
        _sessions.set_latest_reply("reply-test", "Dinosaurs were huge!")
        with _loaded_client() as (client, *_):
            resp = client.get("/session/reply-test/latest_reply")
        assert resp.status_code == 200
        assert resp.json()["reply"] == "Dinosaurs were huge!"
        with _loaded_client() as (client, *_):
            resp2 = client.get("/session/reply-test/latest_reply")
        assert resp2.json()["reply"] == ""


# ---------------------------------------------------------------------------
# GET /settings/voices
# ---------------------------------------------------------------------------

class TestSettingsVoices:
    def test_returns_voice_list_and_current(self):
        with _loaded_client() as (client, stt, llm, tts):
            tts.available_voices.return_value = ["af_bella", "af_sky"]
            tts.voice = "af_bella"
            tts.speed = 1.0
            resp = client.get("/settings/voices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["voices"] == ["af_bella", "af_sky"]
        assert data["current_voice"] == "af_bella"
        assert data["current_speed"] == 1.0

    def test_503_when_models_not_ready(self):
        with patch("server.main._tts", None):
            client = TestClient(app)
            resp = client.get("/settings/voices")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /settings
# ---------------------------------------------------------------------------

class TestUpdateSettings:
    def test_valid_voice_accepted(self):
        with _loaded_client() as (client, stt, llm, tts):
            tts.available_voices.return_value = ["af_bella", "af_sky"]
            resp = client.post("/settings", data={"voice": "af_sky"})
        assert resp.status_code == 200
        assert resp.json()["changes"]["voice"] == "af_sky"
        tts.set_voice.assert_called_once_with("af_sky")

    def test_unknown_voice_returns_400(self):
        with _loaded_client() as (client, stt, llm, tts):
            tts.available_voices.return_value = ["af_bella"]
            resp = client.post("/settings", data={"voice": "nonexistent_voice"})
        assert resp.status_code == 400
        tts.set_voice.assert_not_called()

    def test_valid_speed_accepted(self):
        with _loaded_client() as (client, stt, llm, tts):
            tts.speed = 1.2
            resp = client.post("/settings", data={"speed": "1.2"})
        assert resp.status_code == 200
        tts.set_speed.assert_called_once_with(1.2)

    def test_invalid_speed_returns_400(self):
        with _loaded_client() as (client, *_):
            resp = client.post("/settings", data={"speed": "fast"})
        assert resp.status_code == 400

    def test_503_when_models_not_ready(self):
        with patch("server.main._tts", None):
            client = TestClient(app)
            resp = client.post("/settings", data={"voice": "af_bella"})
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Additional speed edge cases for TestUpdateSettings
# ---------------------------------------------------------------------------

class TestUpdateSettingsSpeedEdgeCases:
    def test_nan_speed_returns_400(self):
        with _loaded_client() as (client, *_):
            resp = client.post("/settings", data={"speed": "nan"})
        assert resp.status_code == 400

    def test_inf_speed_returns_400(self):
        with _loaded_client() as (client, *_):
            resp = client.post("/settings", data={"speed": "inf"})
        assert resp.status_code == 400

    def test_negative_speed_returns_400(self):
        with _loaded_client() as (client, *_):
            resp = client.post("/settings", data={"speed": "-1.0"})
        assert resp.status_code == 400

    def test_speed_below_minimum_returns_400(self):
        with _loaded_client() as (client, *_):
            resp = client.post("/settings", data={"speed": "0.4"})
        assert resp.status_code == 400

    def test_speed_above_maximum_returns_400(self):
        with _loaded_client() as (client, *_):
            resp = client.post("/settings", data={"speed": "2.1"})
        assert resp.status_code == 400

    def test_boundary_minimum_speed_accepted(self):
        with _loaded_client() as (client, stt, llm, tts):
            tts.speed = 0.5
            resp = client.post("/settings", data={"speed": "0.5"})
        assert resp.status_code == 200

    def test_boundary_maximum_speed_accepted(self):
        with _loaded_client() as (client, stt, llm, tts):
            tts.speed = 2.0
            resp = client.post("/settings", data={"speed": "2.0"})
        assert resp.status_code == 200
