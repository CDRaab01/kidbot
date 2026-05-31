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


# ---------------------------------------------------------------------------
# _head_check — the --no-vision gate now actually verifies the URL is alive
# ---------------------------------------------------------------------------

class TestHeadCheck:
    def test_200_with_image_content_type_is_ok(self, smoke):
        r = MagicMock(status_code=200,
                      headers={"Content-Type": "image/jpeg"})
        with patch("requests.head", return_value=r):
            ok, explanation = smoke._head_check("http://x/cat.jpg")
        assert ok is True
        assert "image/jpeg" in explanation

    def test_handles_content_type_with_charset_suffix(self, smoke):
        # Some CDNs append "; charset=binary" — strip before matching.
        r = MagicMock(status_code=200,
                      headers={"Content-Type": "image/png; charset=binary"})
        with patch("requests.head", return_value=r):
            ok, _ = smoke._head_check("http://x/p.png")
        assert ok is True

    def test_404_is_failure(self, smoke):
        r = MagicMock(status_code=404, headers={})
        with patch("requests.head", return_value=r):
            ok, explanation = smoke._head_check("http://x/dead.jpg")
        assert ok is False
        assert "404" in explanation

    def test_html_response_is_failure(self, smoke):
        # A 200 HTML "image removed" page should fail the gate — the URL is
        # technically alive but the Pi can't display it.
        r = MagicMock(status_code=200,
                      headers={"Content-Type": "text/html; charset=utf-8"})
        with patch("requests.head", return_value=r):
            ok, explanation = smoke._head_check("http://x/removed")
        assert ok is False
        assert "not an image" in explanation

    def test_missing_content_type_is_failure(self, smoke):
        r = MagicMock(status_code=200, headers={})
        with patch("requests.head", return_value=r):
            ok, explanation = smoke._head_check("http://x/y")
        assert ok is False
        assert "unknown" in explanation

    def test_network_failure_is_failure(self, smoke):
        with patch("requests.head", side_effect=requests.ConnectionError):
            ok, explanation = smoke._head_check("http://x/y.jpg")
        assert ok is False
        assert "HEAD failed" in explanation

    def test_follows_redirects(self, smoke):
        with patch("requests.head") as head:
            head.return_value = MagicMock(status_code=200,
                                          headers={"Content-Type": "image/jpeg"})
            smoke._head_check("http://x/redirected")
        # Important: many Wikimedia / NASA URLs 302 to a CDN. allow_redirects
        # is the difference between PASS and bogus FAIL on those.
        assert head.call_args.kwargs.get("allow_redirects") is True


# ---------------------------------------------------------------------------
# run_dedup_check — exclude_urls smoke coverage
# ---------------------------------------------------------------------------

class TestRunDedupCheck:
    def test_different_urls_passes(self, smoke):
        with patch.object(smoke, "_query_kidbot", side_effect=[
                ("ok", "http://x/elephant1.jpg"),
                ("ok", "http://x/elephant2.jpg"),
             ]), patch("time.sleep"):
            assert smoke.run_dedup_check() == 0

    def test_duplicate_url_fails(self, smoke):
        with patch.object(smoke, "_query_kidbot", side_effect=[
                ("ok", "http://x/elephant.jpg"),
                ("ok", "http://x/elephant.jpg"),  # exclude_urls didn't work
             ]), patch("time.sleep"):
            assert smoke.run_dedup_check() == 1

    def test_first_request_returns_no_url(self, smoke):
        with patch.object(smoke, "_query_kidbot", side_effect=[
                ("oops", ""),
             ]), patch("time.sleep"):
            assert smoke.run_dedup_check() == 1

    def test_second_request_returns_no_url(self, smoke):
        with patch.object(smoke, "_query_kidbot", side_effect=[
                ("ok", "http://x/elephant.jpg"),
                ("oops", ""),
             ]), patch("time.sleep"):
            assert smoke.run_dedup_check() == 1

    def test_session_id_constant_across_both_requests(self, smoke):
        seen: list[str] = []

        def fake_query(message, session_id):
            seen.append(session_id)
            return "ok", f"http://x/{len(seen)}.jpg"

        with patch.object(smoke, "_query_kidbot", side_effect=fake_query), \
             patch("time.sleep"):
            smoke.run_dedup_check()
        # Both requests must use the same session_id — otherwise the server
        # wouldn't have URL #1 in the session's exclude list when URL #2 is
        # requested, defeating the test entirely.
        assert len(seen) == 2 and seen[0] == seen[1]


# ---------------------------------------------------------------------------
# run() — --no-vision branch now wraps HEAD check, not blind PASS
# ---------------------------------------------------------------------------

class TestRunNoVisionBranch:
    _one_test = [("show me a picture of a cat", "a cat")]

    def test_alive_url_passes(self, smoke):
        with patch.object(smoke, "_query_kidbot",
                          return_value=("ok", "http://x/cat.jpg")), \
             patch.object(smoke, "_head_check",
                          return_value=(True, "reachable image/jpeg")), \
             patch("time.sleep"):
            failures = smoke.run(self._one_test, use_vision=False)
        assert failures == 0

    def test_dead_url_fails(self, smoke):
        with patch.object(smoke, "_query_kidbot",
                          return_value=("ok", "http://x/dead.jpg")), \
             patch.object(smoke, "_head_check",
                          return_value=(False, "HTTP 404")), \
             patch("time.sleep"):
            failures = smoke.run(self._one_test, use_vision=False)
        assert failures == 1

    def test_no_url_returned_still_counts_as_one_failure(self, smoke):
        # The blind "URL returned at all" check is the FIRST gate; HEAD is the
        # second. Ensure they don't double-count when the URL itself is empty.
        with patch.object(smoke, "_query_kidbot",
                          return_value=("ok", "")), \
             patch.object(smoke, "_head_check") as head, \
             patch("time.sleep"):
            failures = smoke.run(self._one_test, use_vision=False)
        assert failures == 1
        head.assert_not_called()  # didn't fall through to the HEAD branch
