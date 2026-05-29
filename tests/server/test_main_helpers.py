"""Unit tests for _safe_header() and _extract_image() in server/main.py."""
import pytest
from server.main import _extract_image, _safe_header


class TestSafeHeader:
    def test_plain_ascii_unchanged(self):
        assert _safe_header("Hello world") == "Hello world"

    def test_newline_replaced_with_space(self):
        result = _safe_header("line1\nline2")
        assert "\n" not in result
        assert "line1" in result and "line2" in result

    def test_carriage_return_replaced_with_space(self):
        result = _safe_header("line1\rline2")
        assert "\r" not in result

    def test_crlf_injection_neutralised(self):
        evil = "value\r\nX-Injected: bad"
        result = _safe_header(evil)
        assert "\r\n" not in result
        assert "X-Injected" not in result or "X-Injected" in result.replace("\r\n", "  ")

    def test_non_ascii_replaced(self):
        result = _safe_header("héllo wörld")
        result.encode("ascii")  # raises ValueError if non-ASCII present

    def test_curly_apostrophe_normalised(self):
        assert "'" in _safe_header("it’s fine")
        assert "’" not in _safe_header("it’s fine")

    def test_curly_double_quotes_normalised(self):
        result = _safe_header("“hello”")
        assert '"' in result
        assert "“" not in result and "”" not in result

    def test_em_dash_becomes_hyphen(self):
        assert "-" in _safe_header("one—two")
        assert "—" not in _safe_header("one—two")

    def test_en_dash_becomes_hyphen(self):
        assert "-" in _safe_header("one–two")

    def test_multiple_spaces_collapsed(self):
        assert _safe_header("a  b   c") == "a b c"

    def test_empty_string_returns_empty(self):
        assert _safe_header("") == ""


class TestExtractImage:
    def test_returns_term_and_clean_text(self):
        text, term = _extract_image("Cats are cool. [IMAGE: fluffy cat]")
        assert term == "fluffy cat"
        assert "[IMAGE:" not in text
        assert "Cats are cool." in text

    def test_no_tag_returns_none_term(self):
        text, term = _extract_image("No image here.")
        assert term is None
        assert text == "No image here."

    def test_tag_only_returns_empty_text(self):
        text, term = _extract_image("[IMAGE: dinosaur]")
        assert term == "dinosaur"
        assert text == ""

    def test_case_insensitive(self):
        _, term = _extract_image("[image: elephant]")
        assert term == "elephant"

    def test_term_whitespace_trimmed(self):
        _, term = _extract_image("[IMAGE:   space panda   ]")
        assert term == "space panda"

    def test_tag_removed_from_middle_of_text(self):
        text, term = _extract_image("Here is [IMAGE: tiger] a fact.")
        assert term == "tiger"
        assert "[IMAGE:" not in text
        assert "Here is" in text and "a fact." in text

    def test_multiword_term_preserved(self):
        _, term = _extract_image("[IMAGE: blue whale swimming]")
        assert term == "blue whale swimming"


# ---------------------------------------------------------------------------
# _fallback_image_term()
# ---------------------------------------------------------------------------

from server.main import _fallback_image_term


class TestFallbackImageTerm:
    def test_show_me_picture_of_extracts_subject(self):
        assert _fallback_image_term("show me a picture of a blue whale") == "a blue whale"

    def test_see_photo_of_extracts_subject(self):
        result = _fallback_image_term("can I see a photo of the moon?")
        assert result is not None
        assert "moon" in result

    def test_what_does_look_like_without_image_word_returns_none(self):
        # REQUEST_RE requires show/see/picture/image/photo — "what does" alone fails
        assert _fallback_image_term("what does a volcano look like") is None

    def test_plain_picture_of_extracts_subject(self):
        result = _fallback_image_term("picture of a rainbow")
        assert result is not None
        assert "rainbow" in result

    def test_no_image_request_returns_none(self):
        assert _fallback_image_term("tell me about dinosaurs") is None

    def test_show_me_without_picture_of_returns_none(self):
        # "show me a dinosaur" — image request word present but no "picture/photo/image of"
        assert _fallback_image_term("show me a dinosaur") is None

    def test_empty_string_returns_none(self):
        assert _fallback_image_term("") is None

    def test_case_insensitive(self):
        result = _fallback_image_term("Show Me A Picture Of a tiger")
        assert result is not None
        assert "tiger" in result


# ---------------------------------------------------------------------------
# Input length limits (10 000 chars) across endpoints
# ---------------------------------------------------------------------------

import io
import wave
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from server.main import app, limiter


@contextmanager
def _client_with_models():
    limiter._storage.reset()
    with patch("server.main.SpeechToText") as MockSTT, \
         patch("server.main.LLMInterface") as MockLLM, \
         patch("server.main.TextToSpeech") as MockTTS:
        MockSTT.return_value = MagicMock()
        MockLLM.return_value = MagicMock()
        tts = MagicMock()
        tts.synthesize.return_value = b"fakemp3"
        MockTTS.return_value = tts
        with TestClient(app) as client:
            yield client


class TestInputLengthLimits:
    _long_text = "x" * 10_001

    def test_speak_too_long_returns_400(self):
        with _client_with_models() as client:
            resp = client.post("/speak", data={"text": self._long_text})
        assert resp.status_code == 400

    def test_chat_text_too_long_returns_400(self):
        with _client_with_models() as client:
            resp = client.post("/chat_text", data={"text": self._long_text, "session_id": "s1"})
        assert resp.status_code == 400

    def test_chat_text_stream_too_long_returns_400(self):
        with _client_with_models() as client:
            resp = client.post("/chat_text_stream", data={"text": self._long_text, "session_id": "s1"})
        assert resp.status_code == 400

    def test_exactly_10000_chars_is_accepted(self):
        with _client_with_models() as client:
            resp = client.post("/speak", data={"text": "x" * 10_000})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _run_llm_pipeline() image fallback (non-streaming path)
# ---------------------------------------------------------------------------

class TestRunLlmPipelineImageFallback:
    def test_explicit_picture_request_fetches_image_without_tag(self):
        """When the LLM reply has no [IMAGE:] tag but the child asked for a
        picture, the non-streaming pipeline falls back to the user's message."""
        import server.main as main
        with patch.object(main, "_llm") as llm, \
             patch.object(main, "_sessions") as sessions, \
             patch.object(main, "fetch_image_url", return_value="http://img/x.jpg") as fetch:
            llm.respond.return_value = "Sure! Spider-Man is amazing."  # no tag
            sessions.get_history.return_value = []
            sessions.get_shown_image_urls.return_value = []
            reply, url = main._run_llm_pipeline(
                "show me a picture of Spider-Man", "s1")
        assert url == "http://img/x.jpg"
        assert fetch.call_args[0][0] == "Spider-Man"

    def test_no_picture_request_no_fallback_fetch(self):
        import server.main as main
        with patch.object(main, "_llm") as llm, \
             patch.object(main, "_sessions") as sessions, \
             patch.object(main, "fetch_image_url") as fetch:
            llm.respond.return_value = "Dinosaurs were huge reptiles."  # no tag
            sessions.get_history.return_value = []
            reply, url = main._run_llm_pipeline("tell me about dinosaurs", "s1")
        assert url == ""
        fetch.assert_not_called()

    def test_explicit_tag_takes_priority_over_fallback(self):
        import server.main as main
        with patch.object(main, "_llm") as llm, \
             patch.object(main, "_sessions") as sessions, \
             patch.object(main, "fetch_image_url", return_value="http://img/y.jpg") as fetch:
            llm.respond.return_value = "Here you go. [IMAGE: tiger photo]"
            sessions.get_history.return_value = []
            sessions.get_shown_image_urls.return_value = []
            main._run_llm_pipeline("show me a picture of a lion", "s1")
        assert fetch.call_args[0][0] == "tiger photo"


# ---------------------------------------------------------------------------
# Startup auth warning
# ---------------------------------------------------------------------------

class TestAuthWarning:
    def test_warns_when_no_api_key(self):
        import server.main as main
        with patch.object(main, "API_KEY", ""), \
             patch.object(main.logger, "warning") as mock_warn:
            main._warn_if_auth_disabled()
        mock_warn.assert_called_once()

    def test_silent_when_api_key_set(self):
        import server.main as main
        with patch.object(main, "API_KEY", "secret"), \
             patch.object(main.logger, "warning") as mock_warn:
            main._warn_if_auth_disabled()
        mock_warn.assert_not_called()


# ---------------------------------------------------------------------------
# Non-streaming endpoints offload blocking work to the threadpool
# ---------------------------------------------------------------------------

class TestNonStreamingOffload:
    def _spy_threadpool(self, calls):
        import server.main as main
        real = main.run_in_threadpool

        async def spy(func, *a, **k):
            calls.append(getattr(func, "__name__", str(func)))
            return await real(func, *a, **k)

        return patch.object(main, "run_in_threadpool", spy)

    def test_chat_text_offloads_pipeline_and_tts(self):
        import server.main as main
        calls = []
        limiter._storage.reset()
        with patch("server.main.SpeechToText"), \
             patch("server.main.LLMInterface") as MockLLM, \
             patch("server.main.TextToSpeech") as MockTTS, \
             self._spy_threadpool(calls):
            llm = MagicMock()
            llm.respond.return_value = "Dinosaurs are great!"
            MockLLM.return_value = llm
            tts = MagicMock()
            tts.synthesize.return_value = b"mp3"
            MockTTS.return_value = tts
            with TestClient(app) as client:
                resp = client.post("/chat_text", data={"text": "hi", "session_id": "s1"})
        assert resp.status_code == 200
        # Both the LLM pipeline and the TTS response build were offloaded.
        assert "_run_llm_pipeline" in calls
        assert "_mp3_response" in calls

    def test_chat_offloads_transcription(self):
        calls = []
        limiter._storage.reset()
        with patch("server.main.SpeechToText") as MockSTT, \
             patch("server.main.LLMInterface") as MockLLM, \
             patch("server.main.TextToSpeech") as MockTTS, \
             self._spy_threadpool(calls):
            stt = MagicMock()
            stt.transcribe.return_value = "hello"
            stt.transcribe.__name__ = "transcribe"  # so the spy can record it
            MockSTT.return_value = stt
            llm = MagicMock()
            llm.respond.return_value = "Hi!"
            MockLLM.return_value = llm
            tts = MagicMock()
            tts.synthesize.return_value = b"mp3"
            MockTTS.return_value = tts
            with TestClient(app) as client:
                resp = client.post(
                    "/chat",
                    files={"audio": ("a.wav", b"RIFFdata", "audio/wav")},
                    data={"session_id": "s1"},
                )
        assert resp.status_code == 200
        assert "transcribe" in calls
        assert "_run_llm_pipeline" in calls


# ---------------------------------------------------------------------------
# _sentence_stream() error branch
# ---------------------------------------------------------------------------

class TestSentenceStreamErrorBranch:
    def test_llm_exception_in_producer_does_not_update_session(self):
        """When the LLM stream raises, full_parts is empty → no session history written."""
        limiter._storage.reset()
        with patch("server.main.SpeechToText") as MockSTT, \
             patch("server.main.LLMInterface") as MockLLM, \
             patch("server.main.TextToSpeech") as MockTTS, \
             patch("server.main._sessions") as mock_sessions:

            MockSTT.return_value = MagicMock()
            llm = MagicMock()
            llm.respond_stream.side_effect = RuntimeError("LLM exploded")
            MockLLM.return_value = llm

            tts = MagicMock()
            tts.synthesize.return_value = b"fakemp3"
            MockTTS.return_value = tts
            mock_sessions.get_history.return_value = []

            with TestClient(app) as client:
                resp = client.post("/chat_text_stream",
                                   data={"text": "hi", "session_id": "err-test"})

        assert resp.status_code == 200
        mock_sessions.add_exchange.assert_not_called()

    def test_one_sentence_tts_failure_does_not_abort_stream(self):
        """A single TTS failure mid-stream is skipped; later sentences still play."""
        limiter._storage.reset()
        with patch("server.main.SpeechToText") as MockSTT, \
             patch("server.main.LLMInterface") as MockLLM, \
             patch("server.main.TextToSpeech") as MockTTS, \
             patch("server.main._sessions"):

            MockSTT.return_value = MagicMock()
            llm = MagicMock()
            llm.respond_stream.return_value = iter(
                ["First sentence here.", "Second sentence here."])
            MockLLM.return_value = llm

            tts = MagicMock()
            # First sentence raises, second succeeds.
            tts.synthesize.side_effect = [RuntimeError("ffmpeg boom"), b"mp3two"]
            MockTTS.return_value = tts

            with TestClient(app) as client:
                resp = client.post("/chat_text_stream",
                                   data={"text": "hi", "session_id": "s1"})

        assert resp.status_code == 200
        assert resp.content == b"mp3two"  # only the surviving sentence

    def test_llm_exception_stream_returns_empty_body(self):
        """Stream body should be empty (no sentences synthesised) on error."""
        limiter._storage.reset()
        with patch("server.main.SpeechToText") as MockSTT, \
             patch("server.main.LLMInterface") as MockLLM, \
             patch("server.main.TextToSpeech") as MockTTS:

            MockSTT.return_value = MagicMock()
            llm = MagicMock()
            llm.respond_stream.side_effect = RuntimeError("boom")
            MockLLM.return_value = llm

            tts = MagicMock()
            tts.synthesize.return_value = b"fakemp3"
            MockTTS.return_value = tts

            with TestClient(app) as client:
                resp = client.post("/chat_text_stream",
                                   data={"text": "hi", "session_id": "s1"})

        assert len(resp.content) == 0
