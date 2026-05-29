"""Tests for pi_client/client.py — ServerClient HTTP, retry, and polling."""
import importlib
from unittest.mock import MagicMock, patch

import pytest
import requests


def _import_client():
    import pi_client.client as client
    importlib.reload(client)
    return client


@pytest.fixture
def client_mod():
    return _import_client()


# ---------------------------------------------------------------------------
# _headers
# ---------------------------------------------------------------------------

class TestHeaders:
    def test_no_key_means_empty_headers(self, client_mod):
        c = client_mod.ServerClient()
        with patch.object(client_mod, "API_KEY", ""):
            assert c._headers == {}

    def test_key_present_adds_header(self, client_mod):
        c = client_mod.ServerClient()
        with patch.object(client_mod, "API_KEY", "secret"):
            assert c._headers == {"X-API-Key": "secret"}


# ---------------------------------------------------------------------------
# _post_with_retry
# ---------------------------------------------------------------------------

class TestPostWithRetry:
    def test_returns_response_on_success(self, client_mod):
        c = client_mod.ServerClient()
        resp = MagicMock(status_code=200)
        with patch("requests.post", return_value=resp) as mock_post:
            out = c._post_with_retry("http://x/speak", data={})
        assert out is resp
        assert mock_post.call_count == 1

    def test_timeout_returns_none_immediately(self, client_mod):
        c = client_mod.ServerClient()
        with patch("requests.post", side_effect=requests.Timeout), \
             patch("time.sleep") as mock_sleep:
            out = c._post_with_retry("http://x/speak", data={})
        assert out is None
        mock_sleep.assert_not_called()  # no retry on timeout

    def test_connection_error_retries_then_gives_up(self, client_mod):
        c = client_mod.ServerClient()
        with patch("requests.post", side_effect=requests.ConnectionError), \
             patch("time.sleep") as mock_sleep:
            out = c._post_with_retry("http://x/speak", data={})
        assert out is None
        # _MAX_RETRIES extra waits between the (MAX_RETRIES + 1) attempts
        assert mock_sleep.call_count == client_mod._MAX_RETRIES

    def test_connection_error_then_success(self, client_mod):
        c = client_mod.ServerClient()
        resp = MagicMock(status_code=200)
        with patch("requests.post", side_effect=[requests.ConnectionError, resp]), \
             patch("time.sleep"):
            out = c._post_with_retry("http://x/speak", data={})
        assert out is resp


# ---------------------------------------------------------------------------
# get_latest_image
# ---------------------------------------------------------------------------

class TestGetLatestImage:
    def test_returns_url_and_pending(self, client_mod):
        c = client_mod.ServerClient()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"image_url": "https://x/y.jpg", "pending": False}
        with patch("requests.get", return_value=resp):
            assert c.get_latest_image() == ("https://x/y.jpg", False)

    def test_empty_url_pending_true(self, client_mod):
        c = client_mod.ServerClient()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"image_url": "", "pending": True}
        with patch("requests.get", return_value=resp):
            assert c.get_latest_image() == (None, True)

    def test_request_exception_returns_none_not_pending(self, client_mod):
        c = client_mod.ServerClient()
        with patch("requests.get", side_effect=requests.RequestException):
            assert c.get_latest_image() == (None, False)


# ---------------------------------------------------------------------------
# send_audio_stream
# ---------------------------------------------------------------------------

class TestSendAudioStream:
    def test_returns_iterator_on_200(self, client_mod, tmp_path):
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"RIFFdata")
        c = client_mod.ServerClient()
        resp = MagicMock(status_code=200)
        resp.iter_content.return_value = iter([b"chunk"])
        with patch("requests.post", return_value=resp):
            out = c.send_audio_stream(str(wav))
        assert out is not None
        assert list(out) == [b"chunk"]

    def test_non_200_returns_none(self, client_mod, tmp_path):
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"RIFFdata")
        c = client_mod.ServerClient()
        resp = MagicMock(status_code=500)
        with patch("requests.post", return_value=resp):
            assert c.send_audio_stream(str(wav)) is None

    def test_connection_error_returns_none(self, client_mod, tmp_path):
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"RIFFdata")
        c = client_mod.ServerClient()
        with patch("requests.post", side_effect=requests.RequestException):
            assert c.send_audio_stream(str(wav)) is None


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------

class TestPing:
    def test_ping_true_on_200(self, client_mod):
        c = client_mod.ServerClient()
        with patch("requests.get", return_value=MagicMock(status_code=200)):
            assert c.ping() is True

    def test_ping_false_on_exception(self, client_mod):
        c = client_mod.ServerClient()
        with patch("requests.get", side_effect=requests.RequestException):
            assert c.ping() is False
