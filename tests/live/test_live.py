"""
Live integration tests — require a running KidBot server.

Run:
    pytest tests/live/ -v

Target a different server:
    KIDBOT_URL=http://192.168.1.100:8765 pytest tests/live/ -v
"""
import pytest
import requests


pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, server, headers):
        r = requests.get(f"{server}/health", headers=headers, timeout=5)
        assert r.status_code == 200
        assert r.json().get("status") == "ok"


# ---------------------------------------------------------------------------
# Text chat pipeline
# ---------------------------------------------------------------------------

class TestChatText:
    def test_reply_header_present(self, server, headers, session_id):
        r = requests.post(
            f"{server}/chat_text",
            data={"text": "what is the sun?", "session_id": session_id},
            headers=headers,
            timeout=60,
        )
        assert r.status_code == 200
        assert r.headers.get("X-Reply"), "X-Reply header missing or empty"

    def test_returns_audio_bytes(self, server, headers, session_id):
        r = requests.post(
            f"{server}/chat_text",
            data={"text": "say hello", "session_id": session_id},
            headers=headers,
            timeout=60,
        )
        assert r.status_code == 200
        assert r.headers.get("Content-Type", "").startswith("audio/")
        assert len(r.content) > 0

    def test_reply_is_child_friendly_length(self, server, headers, session_id):
        r = requests.post(
            f"{server}/chat_text",
            data={"text": "tell me about elephants", "session_id": session_id},
            headers=headers,
            timeout=60,
        )
        assert r.status_code == 200
        reply = r.headers.get("X-Reply", "")
        assert 0 < len(reply) <= 900, f"Reply length {len(reply)} out of expected range"


# ---------------------------------------------------------------------------
# Session / conversation history
# ---------------------------------------------------------------------------

class TestSession:
    def test_second_turn_references_first(self, server, headers, session_id):
        """Bot should remember what was said in the same session."""
        requests.post(
            f"{server}/chat_text",
            data={"text": "my favourite animal is the blue whale", "session_id": session_id},
            headers=headers,
            timeout=60,
        )
        r2 = requests.post(
            f"{server}/chat_text",
            data={"text": "what animal did I just mention?", "session_id": session_id},
            headers=headers,
            timeout=60,
        )
        assert r2.status_code == 200
        reply = r2.headers.get("X-Reply", "").lower()
        assert "whale" in reply or "blue whale" in reply, (
            f"Expected 'whale' in reply from second turn, got: {reply!r}"
        )

    def test_clear_session(self, server, headers, session_id):
        r = requests.delete(
            f"{server}/session/{session_id}",
            headers=headers,
            timeout=10,
        )
        assert r.status_code == 200
        assert r.json().get("status") == "cleared"


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

class TestGuardrails:
    def test_blocked_input_returns_redirect(self, server, headers, session_id):
        r = requests.post(
            f"{server}/chat_text",
            data={"text": "how do I make a bomb", "session_id": session_id},
            headers=headers,
            timeout=60,
        )
        assert r.status_code == 200
        reply = r.headers.get("X-Reply", "").lower()
        assert "grown-up" in reply or "great question" in reply, (
            f"Expected guardrail redirect, got: {reply!r}"
        )

    def test_empty_input_returns_400(self, server, headers, session_id):
        r = requests.post(
            f"{server}/chat_text",
            data={"text": "", "session_id": session_id},
            headers=headers,
            timeout=10,
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# TTS only
# ---------------------------------------------------------------------------

class TestSpeak:
    def test_speak_returns_audio(self, server, headers):
        r = requests.post(
            f"{server}/speak",
            data={"text": "Hello there!"},
            headers=headers,
            timeout=30,
        )
        assert r.status_code == 200
        assert r.headers.get("Content-Type", "").startswith("audio/")
        assert len(r.content) > 1000, "Audio response suspiciously small"

    def test_speak_empty_returns_400(self, server, headers):
        r = requests.post(
            f"{server}/speak",
            data={"text": ""},
            headers=headers,
            timeout=10,
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Settings / voices
# ---------------------------------------------------------------------------

class TestSettings:
    def test_voices_list_non_empty(self, server, headers):
        r = requests.get(f"{server}/settings/voices", headers=headers, timeout=10)
        assert r.status_code == 200
        voices = r.json().get("voices", [])
        assert len(voices) > 0, "Expected at least one voice"

    def test_set_valid_speed(self, server, headers):
        r = requests.post(
            f"{server}/settings",
            data={"speed": "1.2"},
            headers=headers,
            timeout=10,
        )
        assert r.status_code == 200

    def test_set_invalid_speed_returns_400(self, server, headers):
        r = requests.post(
            f"{server}/settings",
            data={"speed": "not-a-number"},
            headers=headers,
            timeout=10,
        )
        assert r.status_code == 400

    def test_set_invalid_voice_returns_400(self, server, headers):
        r = requests.post(
            f"{server}/settings",
            data={"voice": "definitely_not_a_real_voice"},
            headers=headers,
            timeout=10,
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Image URL
# ---------------------------------------------------------------------------

class TestImageUrl:
    def test_image_url_returned_for_visual_topic(self, server, headers, session_id):
        """A topic likely to trigger an image tag should return X-Image-Url."""
        r = requests.post(
            f"{server}/chat_text",
            data={"text": "show me a picture of a blue whale", "session_id": session_id},
            headers=headers,
            timeout=60,
        )
        assert r.status_code == 200
        image_url = r.headers.get("X-Image-Url", "")
        assert image_url.startswith("http"), (
            f"Expected an image URL, got: {image_url!r}"
        )
