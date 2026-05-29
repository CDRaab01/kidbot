"""Tests for scripts/test_images.py — the deploy smoke-test query helper.

scripts/ is not a package, so load the module by file path.
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "test_images.py"


def _load():
    spec = importlib.util.spec_from_file_location("smoke_test_images", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def smoke():
    return _load()


def _resp(status, headers=None):
    r = MagicMock(status_code=status)
    r.headers = headers or {}
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(response=r)
    else:
        r.raise_for_status.return_value = None
    return r


class TestQueryKidbotRateLimit:
    def test_retries_on_429_then_succeeds(self, smoke):
        throttled = _resp(429, {"Retry-After": "2"})
        ok = _resp(200, {"X-Reply": "hi", "X-Image-Url": "http://x/y.jpg"})
        with patch("requests.post", side_effect=[throttled, ok]) as post, \
             patch("time.sleep") as sleep:
            reply, url = smoke._query_kidbot("show me a picture of a cat", "s1")
        assert (reply, url) == ("hi", "http://x/y.jpg")
        assert post.call_count == 2
        sleep.assert_called_once_with(3)  # Retry-After 2 + 1

    def test_missing_retry_after_uses_default(self, smoke):
        throttled = _resp(429, {})
        ok = _resp(200, {"X-Reply": "hi", "X-Image-Url": ""})
        with patch("requests.post", side_effect=[throttled, ok]), \
             patch("time.sleep") as sleep:
            smoke._query_kidbot("hello", "s1")
        sleep.assert_called_once_with(13)

    def test_gives_up_after_persistent_429(self, smoke):
        with patch("requests.post", return_value=_resp(429, {"Retry-After": "1"})), \
             patch("time.sleep"):
            reply, url = smoke._query_kidbot("hello", "s1")
        assert (reply, url) == ("", "")

    def test_success_returns_headers(self, smoke):
        ok = _resp(200, {"X-Reply": "a reply", "X-Image-Url": "http://img"})
        with patch("requests.post", return_value=ok):
            assert smoke._query_kidbot("hi", "s1") == ("a reply", "http://img")
