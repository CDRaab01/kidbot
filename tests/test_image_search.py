"""
Tests for server/image_search.fetch_image_url().
All HTTP calls are mocked via unittest.mock.patch — no network required.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from server.image_search import _HEADERS, fetch_image_url


def _make_response(pages: dict, status_code: int = 200) -> MagicMock:
    """Build a minimal mock resembling a requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests import HTTPError
        resp.raise_for_status.side_effect = HTTPError(f"{status_code}")
    resp.json.return_value = {"query": {"pages": pages}}
    return resp


def _page(index: int, url: str = "") -> dict:
    p = {"index": index, "pageid": index * 100}
    if url:
        p["thumbnail"] = {"source": url, "width": 500, "height": 400}
    return p


class TestFetchImageUrl:
    def test_returns_url_for_first_matching_page(self):
        pages = {
            "1": _page(1, "https://upload.wikimedia.org/cat.jpg"),
            "2": _page(2, "https://upload.wikimedia.org/dog.jpg"),
        }
        with patch("server.image_search.requests.get", return_value=_make_response(pages)) as mock_get:
            result = fetch_image_url("cat")
        assert result == "https://upload.wikimedia.org/cat.jpg"

    def test_picks_lowest_index_when_multiple_pages(self):
        pages = {
            "a": _page(3, "https://example.com/third.jpg"),
            "b": _page(1, "https://example.com/first.jpg"),
            "c": _page(2, "https://example.com/second.jpg"),
        }
        with patch("server.image_search.requests.get", return_value=_make_response(pages)):
            result = fetch_image_url("dog")
        assert result == "https://example.com/first.jpg"

    def test_returns_none_when_no_pages_have_thumbnail(self):
        pages = {"1": _page(1, ""), "2": _page(2, "")}
        with patch("server.image_search.requests.get", return_value=_make_response(pages)):
            result = fetch_image_url("obscure term xyz")
        assert result is None

    def test_returns_none_when_query_key_missing(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}  # no "query" key
        with patch("server.image_search.requests.get", return_value=resp):
            result = fetch_image_url("something")
        assert result is None

    def test_returns_none_on_http_error(self):
        resp = _make_response({}, status_code=404)
        with patch("server.image_search.requests.get", return_value=resp):
            result = fetch_image_url("cat")
        assert result is None

    def test_returns_none_on_connection_error(self):
        from requests import ConnectionError as ReqConnErr
        with patch("server.image_search.requests.get", side_effect=ReqConnErr("no network")):
            result = fetch_image_url("cat")
        assert result is None

    def test_returns_none_on_timeout(self):
        from requests import Timeout
        with patch("server.image_search.requests.get", side_effect=Timeout("timed out")):
            result = fetch_image_url("cat")
        assert result is None

    def test_returns_none_on_json_decode_error(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("bad json")
        with patch("server.image_search.requests.get", return_value=resp):
            result = fetch_image_url("cat")
        assert result is None

    def test_custom_size_passed_to_api(self):
        pages = {"1": _page(1, "https://example.com/img.jpg")}
        with patch("server.image_search.requests.get", return_value=_make_response(pages)) as mock_get:
            fetch_image_url("lion", size=300)
        call_params = mock_get.call_args[1]["params"]
        assert call_params["pithumbsize"] == 300

    def test_default_size_is_500(self):
        pages = {"1": _page(1, "https://example.com/img.jpg")}
        with patch("server.image_search.requests.get", return_value=_make_response(pages)) as mock_get:
            fetch_image_url("lion")
        call_params = mock_get.call_args[1]["params"]
        assert call_params["pithumbsize"] == 500

    def test_correct_user_agent_header_sent(self):
        pages = {"1": _page(1, "https://example.com/img.jpg")}
        with patch("server.image_search.requests.get", return_value=_make_response(pages)) as mock_get:
            fetch_image_url("elephant")
        call_headers = mock_get.call_args[1]["headers"]
        assert "CooperBot" in call_headers["User-Agent"]

    def test_timeout_parameter_set(self):
        pages = {"1": _page(1, "https://example.com/img.jpg")}
        with patch("server.image_search.requests.get", return_value=_make_response(pages)) as mock_get:
            fetch_image_url("cat")
        assert mock_get.call_args[1]["timeout"] == 6

    def test_search_term_included_in_request(self):
        pages = {"1": _page(1, "https://example.com/img.jpg")}
        with patch("server.image_search.requests.get", return_value=_make_response(pages)) as mock_get:
            fetch_image_url("blue whale")
        params = mock_get.call_args[1]["params"]
        assert params["gsrsearch"] == "blue whale"

    def test_skips_pages_without_thumbnail_key(self):
        pages = {
            "1": {"index": 1, "pageid": 100},  # no thumbnail at all
            "2": _page(2, "https://example.com/second.jpg"),
        }
        with patch("server.image_search.requests.get", return_value=_make_response(pages)):
            result = fetch_image_url("tiger")
        assert result == "https://example.com/second.jpg"
