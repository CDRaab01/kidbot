"""
Tests for server/image_search — all HTTP calls mocked, no network required.
"""
from unittest.mock import MagicMock, call, patch

import pytest

import server.image_search as image_search
from server.image_search import (
    _HEADERS,
    _search_inaturalist,
    _search_nasa,
    _search_wikipedia,
    fetch_image_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wiki_response(pages: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests import HTTPError
        resp.raise_for_status.side_effect = HTTPError(str(status_code))
    resp.json.return_value = {"query": {"pages": pages}}
    return resp


def _wiki_page(index: int, url: str = "") -> dict:
    p = {"index": index, "pageid": index * 100}
    if url:
        p["thumbnail"] = {"source": url, "width": 500, "height": 400}
    return p


def _nasa_response(items: list, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests import HTTPError
        resp.raise_for_status.side_effect = HTTPError(str(status_code))
    resp.json.return_value = {"collection": {"items": items}}
    return resp


def _nasa_item(href: str) -> dict:
    return {"links": [{"href": href, "rel": "preview"}], "data": []}


def _inat_response(results: list, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests import HTTPError
        resp.raise_for_status.side_effect = HTTPError(str(status_code))
    resp.json.return_value = {"results": results}
    return resp


def _inat_taxon(medium_url: str = "", square_url: str = "") -> dict:
    photo: dict = {}
    if medium_url:
        photo["medium_url"] = medium_url
    if square_url:
        photo["square_url"] = square_url
    return {"default_photo": photo} if photo else {"default_photo": None}


# ---------------------------------------------------------------------------
# _search_wikipedia
# ---------------------------------------------------------------------------

class TestSearchWikipedia:
    def test_returns_url_for_first_matching_page(self):
        pages = {
            "1": _wiki_page(1, "https://upload.wikimedia.org/cat.jpg"),
            "2": _wiki_page(2, "https://upload.wikimedia.org/dog.jpg"),
        }
        with patch("server.image_search.requests.get", return_value=_wiki_response(pages)):
            assert _search_wikipedia("cat", 500, frozenset()) == "https://upload.wikimedia.org/cat.jpg"

    def test_picks_lowest_index_when_multiple_pages(self):
        pages = {
            "a": _wiki_page(3, "https://example.com/third.jpg"),
            "b": _wiki_page(1, "https://example.com/first.jpg"),
            "c": _wiki_page(2, "https://example.com/second.jpg"),
        }
        with patch("server.image_search.requests.get", return_value=_wiki_response(pages)):
            assert _search_wikipedia("dog", 500, frozenset()) == "https://example.com/first.jpg"

    def test_skips_pages_without_thumbnail(self):
        pages = {
            "1": {"index": 1, "pageid": 100},
            "2": _wiki_page(2, "https://example.com/second.jpg"),
        }
        with patch("server.image_search.requests.get", return_value=_wiki_response(pages)):
            assert _search_wikipedia("tiger", 500, frozenset()) == "https://example.com/second.jpg"

    def test_returns_none_when_no_thumbnails(self):
        pages = {"1": _wiki_page(1, ""), "2": _wiki_page(2, "")}
        with patch("server.image_search.requests.get", return_value=_wiki_response(pages)):
            assert _search_wikipedia("obscure", 500, frozenset()) is None

    def test_returns_none_on_missing_query_key(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        with patch("server.image_search.requests.get", return_value=resp):
            assert _search_wikipedia("something", 500, frozenset()) is None

    def test_returns_none_on_http_error(self):
        with patch("server.image_search.requests.get", return_value=_wiki_response({}, 404)):
            assert _search_wikipedia("cat", 500, frozenset()) is None

    def test_returns_none_on_connection_error(self):
        from requests import ConnectionError as ReqConn
        with patch("server.image_search.requests.get", side_effect=ReqConn):
            assert _search_wikipedia("cat", 500, frozenset()) is None

    def test_returns_none_on_timeout(self):
        from requests import Timeout
        with patch("server.image_search.requests.get", side_effect=Timeout):
            assert _search_wikipedia("cat", 500, frozenset()) is None

    def test_size_param_forwarded(self):
        pages = {"1": _wiki_page(1, "https://example.com/img.jpg")}
        with patch("server.image_search.requests.get", return_value=_wiki_response(pages)) as m:
            _search_wikipedia("lion", 300, frozenset())
        assert m.call_args[1]["params"]["pithumbsize"] == 300

    def test_term_included_in_params(self):
        pages = {"1": _wiki_page(1, "https://example.com/img.jpg")}
        with patch("server.image_search.requests.get", return_value=_wiki_response(pages)) as m:
            _search_wikipedia("blue whale", 500, frozenset())
        assert m.call_args[1]["params"]["gsrsearch"] == "blue whale"

    def test_user_agent_header_sent(self):
        pages = {"1": _wiki_page(1, "https://example.com/img.jpg")}
        with patch("server.image_search.requests.get", return_value=_wiki_response(pages)) as m:
            _search_wikipedia("elephant", 500, frozenset())
        from server.config import BOT_NAME
        assert BOT_NAME in m.call_args[1]["headers"]["User-Agent"]

    def test_timeout_set(self):
        pages = {"1": _wiki_page(1, "https://example.com/img.jpg")}
        with patch("server.image_search.requests.get", return_value=_wiki_response(pages)) as m:
            _search_wikipedia("cat", 500, frozenset())
        assert m.call_args[1]["timeout"] == image_search._TIMEOUT

    def test_skips_excluded_url_and_returns_next(self):
        pages = {
            "1": _wiki_page(1, "https://upload.wikimedia.org/first.jpg"),
            "2": _wiki_page(2, "https://upload.wikimedia.org/second.jpg"),
        }
        exclude = frozenset({"https://upload.wikimedia.org/first.jpg"})
        with patch("server.image_search.requests.get", return_value=_wiki_response(pages)):
            assert _search_wikipedia("cat", 500, exclude) == "https://upload.wikimedia.org/second.jpg"

    def test_returns_none_when_all_urls_excluded(self):
        pages = {"1": _wiki_page(1, "https://upload.wikimedia.org/only.jpg")}
        exclude = frozenset({"https://upload.wikimedia.org/only.jpg"})
        with patch("server.image_search.requests.get", return_value=_wiki_response(pages)):
            assert _search_wikipedia("cat", 500, exclude) is None


# ---------------------------------------------------------------------------
# _search_nasa
# ---------------------------------------------------------------------------

class TestSearchNasa:
    def test_returns_jpg_url_from_first_item(self):
        items = [_nasa_item("https://images-assets.nasa.gov/image/PIA123/PIA123~thumb.jpg")]
        with patch("server.image_search.requests.get", return_value=_nasa_response(items)):
            assert _search_nasa("saturn", 500, frozenset()) == \
                "https://images-assets.nasa.gov/image/PIA123/PIA123~thumb.jpg"

    def test_skips_non_image_links(self):
        item = {
            "links": [
                {"href": "https://example.com/metadata.json"},
                {"href": "https://example.com/image.jpg"},
            ],
            "data": [],
        }
        with patch("server.image_search.requests.get", return_value=_nasa_response([item])):
            assert _search_nasa("moon", 500, frozenset()) == "https://example.com/image.jpg"

    def test_returns_none_when_no_items(self):
        with patch("server.image_search.requests.get", return_value=_nasa_response([])):
            assert _search_nasa("nothing", 500, frozenset()) is None

    def test_returns_none_on_http_error(self):
        with patch("server.image_search.requests.get", return_value=_nasa_response([], 500)):
            assert _search_nasa("mars", 500, frozenset()) is None

    def test_returns_none_on_timeout(self):
        from requests import Timeout
        with patch("server.image_search.requests.get", side_effect=Timeout):
            assert _search_nasa("rocket", 500, frozenset()) is None

    def test_term_included_in_params(self):
        items = [_nasa_item("https://example.com/img.jpg")]
        with patch("server.image_search.requests.get", return_value=_nasa_response(items)) as m:
            _search_nasa("Jupiter planet", 500, frozenset())
        assert m.call_args[1]["params"]["q"] == "Jupiter planet"

    def test_media_type_is_image(self):
        items = [_nasa_item("https://example.com/img.jpg")]
        with patch("server.image_search.requests.get", return_value=_nasa_response(items)) as m:
            _search_nasa("nebula", 500, frozenset())
        assert m.call_args[1]["params"]["media_type"] == "image"

    def test_skips_excluded_url_and_returns_next(self):
        items = [
            _nasa_item("https://example.com/first.jpg"),
            _nasa_item("https://example.com/second.jpg"),
        ]
        exclude = frozenset({"https://example.com/first.jpg"})
        with patch("server.image_search.requests.get", return_value=_nasa_response(items)):
            assert _search_nasa("space", 500, exclude) == "https://example.com/second.jpg"

    def test_returns_none_when_all_urls_excluded(self):
        items = [_nasa_item("https://example.com/only.jpg")]
        exclude = frozenset({"https://example.com/only.jpg"})
        with patch("server.image_search.requests.get", return_value=_nasa_response(items)):
            assert _search_nasa("space", 500, exclude) is None


# ---------------------------------------------------------------------------
# _search_inaturalist
# ---------------------------------------------------------------------------

class TestSearchiNaturalist:
    def test_returns_medium_url(self):
        taxa = [_inat_taxon(medium_url="https://inaturalist.org/photos/1/medium.jpg")]
        with patch("server.image_search.requests.get", return_value=_inat_response(taxa)):
            assert _search_inaturalist("tiger", 500, frozenset()) == \
                "https://inaturalist.org/photos/1/medium.jpg"

    def test_falls_back_to_square_url(self):
        taxa = [_inat_taxon(square_url="https://inaturalist.org/photos/1/square.jpg")]
        with patch("server.image_search.requests.get", return_value=_inat_response(taxa)):
            assert _search_inaturalist("frog", 500, frozenset()) == \
                "https://inaturalist.org/photos/1/square.jpg"

    def test_skips_taxa_without_photo(self):
        taxa = [
            {"default_photo": None},
            _inat_taxon(medium_url="https://inaturalist.org/photos/2/medium.jpg"),
        ]
        with patch("server.image_search.requests.get", return_value=_inat_response(taxa)):
            assert _search_inaturalist("shark", 500, frozenset()) == \
                "https://inaturalist.org/photos/2/medium.jpg"

    def test_returns_none_when_no_results(self):
        with patch("server.image_search.requests.get", return_value=_inat_response([])):
            assert _search_inaturalist("xyzzy", 500, frozenset()) is None

    def test_returns_none_on_http_error(self):
        with patch("server.image_search.requests.get", return_value=_inat_response([], 503)):
            assert _search_inaturalist("eagle", 500, frozenset()) is None

    def test_returns_none_on_timeout(self):
        from requests import Timeout
        with patch("server.image_search.requests.get", side_effect=Timeout):
            assert _search_inaturalist("whale", 500, frozenset()) is None

    def test_term_included_in_params(self):
        taxa = [_inat_taxon(medium_url="https://inaturalist.org/p.jpg")]
        with patch("server.image_search.requests.get", return_value=_inat_response(taxa)) as m:
            _search_inaturalist("red panda", 500, frozenset())
        assert m.call_args[1]["params"]["q"] == "red panda"

    def test_skips_excluded_url_and_returns_next(self):
        taxa = [
            _inat_taxon(medium_url="https://inaturalist.org/photos/1/medium.jpg"),
            _inat_taxon(medium_url="https://inaturalist.org/photos/2/medium.jpg"),
        ]
        exclude = frozenset({"https://inaturalist.org/photos/1/medium.jpg"})
        with patch("server.image_search.requests.get", return_value=_inat_response(taxa)):
            assert _search_inaturalist("frog", 500, exclude) == \
                "https://inaturalist.org/photos/2/medium.jpg"

    def test_returns_none_when_all_urls_excluded(self):
        taxa = [_inat_taxon(medium_url="https://inaturalist.org/photos/1/medium.jpg")]
        exclude = frozenset({"https://inaturalist.org/photos/1/medium.jpg"})
        with patch("server.image_search.requests.get", return_value=_inat_response(taxa)):
            assert _search_inaturalist("frog", 500, exclude) is None


# ---------------------------------------------------------------------------
# fetch_image_url — priority / fallback logic
# ---------------------------------------------------------------------------

class TestFetchImageUrl:
    # Patch all five sources so no real network calls are made.
    # Tests only override the sources relevant to the scenario being tested.
    def _all_none(self):
        """Context managers that silence every source."""
        return (
            patch.object(image_search, "_search_openverse",   return_value=None),
            patch.object(image_search, "_search_commons",     return_value=None),
            patch.object(image_search, "_search_wikipedia",   return_value=None),
            patch.object(image_search, "_search_nasa",        return_value=None),
            patch.object(image_search, "_search_inaturalist", return_value=None),
        )

    def test_returns_openverse_url_when_all_succeed(self):
        with patch.object(image_search, "_search_openverse",   return_value="openverse.jpg"), \
             patch.object(image_search, "_search_commons",     return_value="commons.jpg"), \
             patch.object(image_search, "_search_wikipedia",   return_value="wiki.jpg"), \
             patch.object(image_search, "_search_nasa",        return_value="nasa.jpg"), \
             patch.object(image_search, "_search_inaturalist", return_value="inat.jpg"):
            assert fetch_image_url("cat") == "openverse.jpg"

    def test_falls_back_to_commons_when_openverse_fails(self):
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value="commons.jpg"), \
             patch.object(image_search, "_search_wikipedia",   return_value="wiki.jpg"), \
             patch.object(image_search, "_search_nasa",        return_value="nasa.jpg"), \
             patch.object(image_search, "_search_inaturalist", return_value="inat.jpg"):
            assert fetch_image_url("cat") == "commons.jpg"

    def test_falls_back_to_wikipedia_when_openverse_and_commons_fail(self):
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_wikipedia",   return_value="wiki.jpg"), \
             patch.object(image_search, "_search_nasa",        return_value="nasa.jpg"), \
             patch.object(image_search, "_search_inaturalist", return_value="inat.jpg"):
            assert fetch_image_url("cat") == "wiki.jpg"

    def test_falls_back_to_nasa_when_wikipedia_fails(self):
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_wikipedia",   return_value=None), \
             patch.object(image_search, "_search_nasa",        return_value="nasa.jpg"), \
             patch.object(image_search, "_search_inaturalist", return_value="inat.jpg"):
            assert fetch_image_url("saturn") == "nasa.jpg"

    def test_falls_back_to_inaturalist_when_all_others_fail(self):
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_wikipedia",   return_value=None), \
             patch.object(image_search, "_search_nasa",        return_value=None), \
             patch.object(image_search, "_search_inaturalist", return_value="inat.jpg"):
            assert fetch_image_url("tiger") == "inat.jpg"

    def test_returns_none_when_all_sources_fail(self):
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_wikipedia",   return_value=None), \
             patch.object(image_search, "_search_nasa",        return_value=None), \
             patch.object(image_search, "_search_inaturalist", return_value=None):
            assert fetch_image_url("xyzzy") is None

    def test_openverse_preferred_over_wikipedia(self):
        with patch.object(image_search, "_search_openverse",   return_value="openverse.jpg"), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_wikipedia",   return_value="wiki.jpg"), \
             patch.object(image_search, "_search_nasa",        return_value=None), \
             patch.object(image_search, "_search_inaturalist", return_value=None):
            assert fetch_image_url("lion") == "openverse.jpg"

    def test_nasa_preferred_over_inaturalist(self):
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_wikipedia",   return_value=None), \
             patch.object(image_search, "_search_nasa",        return_value="nasa.jpg"), \
             patch.object(image_search, "_search_inaturalist", return_value="inat.jpg"):
            assert fetch_image_url("moon") == "nasa.jpg"

    def test_size_forwarded_to_wikipedia(self):
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_wikipedia")   as mock_wiki, \
             patch.object(image_search, "_search_nasa",        return_value=None), \
             patch.object(image_search, "_search_inaturalist", return_value=None):
            mock_wiki.return_value = "wiki.jpg"
            fetch_image_url("elephant", size=300)
        mock_wiki.assert_called_once_with("elephant", 300, frozenset())

    def test_default_size_is_500(self):
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_wikipedia")   as mock_wiki, \
             patch.object(image_search, "_search_nasa",        return_value=None), \
             patch.object(image_search, "_search_inaturalist", return_value=None):
            mock_wiki.return_value = "wiki.jpg"
            fetch_image_url("elephant")
        mock_wiki.assert_called_once_with("elephant", 500, frozenset())

    def test_source_exception_does_not_propagate(self):
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_wikipedia",   side_effect=RuntimeError("boom")), \
             patch.object(image_search, "_search_nasa",        return_value="nasa.jpg"), \
             patch.object(image_search, "_search_inaturalist", return_value=None):
            result = fetch_image_url("test")
        assert result == "nasa.jpg"

    def test_exclude_urls_forwarded_as_frozenset(self):
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_wikipedia")   as mock_wiki, \
             patch.object(image_search, "_search_nasa",        return_value=None), \
             patch.object(image_search, "_search_inaturalist", return_value=None):
            mock_wiki.return_value = "wiki.jpg"
            fetch_image_url("black hole", exclude_urls=["https://example.com/old.jpg"])
        _, _, called_exclude = mock_wiki.call_args[0]
        assert isinstance(called_exclude, frozenset)
        assert "https://example.com/old.jpg" in called_exclude

    def test_none_exclude_urls_treated_as_empty(self):
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_wikipedia")   as mock_wiki, \
             patch.object(image_search, "_search_nasa",        return_value=None), \
             patch.object(image_search, "_search_inaturalist", return_value=None):
            mock_wiki.return_value = "wiki.jpg"
            fetch_image_url("black hole", exclude_urls=None)
        _, _, called_exclude = mock_wiki.call_args[0]
        assert called_exclude == frozenset()

    def test_skips_excluded_wikipedia_url_via_fetch(self):
        pages_second = {
            "1": _wiki_page(1, "https://upload.wikimedia.org/first.jpg"),
            "2": _wiki_page(2, "https://upload.wikimedia.org/second.jpg"),
        }
        exclude = frozenset({"https://upload.wikimedia.org/first.jpg"})
        with patch.object(image_search, "_search_openverse",   return_value=None), \
             patch.object(image_search, "_search_commons",     return_value=None), \
             patch.object(image_search, "_search_nasa",        return_value=None), \
             patch.object(image_search, "_search_inaturalist", return_value=None), \
             patch("server.image_search.requests.get", return_value=_wiki_response(pages_second)):
            result = fetch_image_url("galaxy", exclude_urls=list(exclude))
        assert result == "https://upload.wikimedia.org/second.jpg"


# ---------------------------------------------------------------------------
# _is_nasa_topic()
# ---------------------------------------------------------------------------

from server.image_search import _is_nasa_topic, _search_openverse, _search_commons


class TestIsNasaTopic:
    def test_space_term_matches(self):
        assert _is_nasa_topic("moon landing") is True

    def test_planet_term_matches(self):
        assert _is_nasa_topic("planet Mars") is True

    def test_astronaut_term_matches(self):
        assert _is_nasa_topic("astronaut spacewalk") is True

    def test_fictional_character_does_not_match(self):
        assert _is_nasa_topic("Spider-Man") is False

    def test_animal_does_not_match(self):
        assert _is_nasa_topic("blue whale") is False

    def test_case_insensitive(self):
        assert _is_nasa_topic("Hubble Telescope") is True

    def test_partial_word_does_not_match(self):
        # "planetary" contains "planet" — should still match because `in` substring check
        assert _is_nasa_topic("planetary science") is True


# ---------------------------------------------------------------------------
# _search_openverse()
# ---------------------------------------------------------------------------

def _openverse_response(results: list, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests import HTTPError
        resp.raise_for_status.side_effect = HTTPError(str(status_code))
    resp.json.return_value = {"results": results}
    return resp


class TestSearchOpenverse:
    def test_returns_thumbnail_url(self):
        results = [{"thumbnail": "https://openverse.org/thumb.jpg", "url": ""}]
        with patch("server.image_search.requests.get",
                   return_value=_openverse_response(results)):
            url = _search_openverse("blue whale", 500, frozenset())
        assert url == "https://openverse.org/thumb.jpg"

    def test_falls_back_to_url_when_no_thumbnail(self):
        results = [{"thumbnail": "", "url": "https://openverse.org/full.jpg"}]
        with patch("server.image_search.requests.get",
                   return_value=_openverse_response(results)):
            url = _search_openverse("cat", 500, frozenset())
        assert url == "https://openverse.org/full.jpg"

    def test_skips_svg_urls(self):
        results = [
            {"thumbnail": "https://openverse.org/icon.svg", "url": ""},
            {"thumbnail": "https://openverse.org/photo.jpg", "url": ""},
        ]
        with patch("server.image_search.requests.get",
                   return_value=_openverse_response(results)):
            url = _search_openverse("cat", 500, frozenset())
        assert url == "https://openverse.org/photo.jpg"

    def test_skips_excluded_urls(self):
        img = "https://openverse.org/already-shown.jpg"
        results = [
            {"thumbnail": img, "url": ""},
            {"thumbnail": "https://openverse.org/new.jpg", "url": ""},
        ]
        with patch("server.image_search.requests.get",
                   return_value=_openverse_response(results)):
            url = _search_openverse("cat", 500, frozenset({img}))
        assert url == "https://openverse.org/new.jpg"

    def test_returns_none_on_empty_results(self):
        with patch("server.image_search.requests.get",
                   return_value=_openverse_response([])):
            url = _search_openverse("obscure thing", 500, frozenset())
        assert url is None

    def test_returns_none_on_request_exception(self):
        from requests import ConnectionError as ReqConn
        with patch("server.image_search.requests.get", side_effect=ReqConn()):
            url = _search_openverse("cat", 500, frozenset())
        assert url is None


# ---------------------------------------------------------------------------
# _search_commons()
# ---------------------------------------------------------------------------

def _commons_response(pages: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests import HTTPError
        resp.raise_for_status.side_effect = HTTPError(str(status_code))
    resp.json.return_value = {"query": {"pages": pages}}
    return resp


def _commons_page(index: int, title: str, thumburl: str = "", url: str = "") -> dict:
    page = {"index": index, "title": title, "imageinfo": []}
    if thumburl or url:
        page["imageinfo"] = [{"thumburl": thumburl, "url": url}]
    return page


class TestSearchCommons:
    def test_returns_thumburl(self):
        pages = {"1": _commons_page(1, "File:Blue whale.jpg",
                                    thumburl="https://commons.org/thumb.jpg")}
        with patch("server.image_search.requests.get",
                   return_value=_commons_response(pages)):
            url = _search_commons("blue whale", 500, frozenset())
        assert url == "https://commons.org/thumb.jpg"

    def test_falls_back_to_url_when_no_thumburl(self):
        pages = {"1": _commons_page(1, "File:Whale.jpg",
                                    url="https://commons.org/full.jpg")}
        with patch("server.image_search.requests.get",
                   return_value=_commons_response(pages)):
            url = _search_commons("whale", 500, frozenset())
        assert url == "https://commons.org/full.jpg"

    def test_skips_pages_with_skip_words_in_title(self):
        pages = {
            "1": _commons_page(1, "File:Batman logo.png",
                               thumburl="https://commons.org/logo.jpg"),
            "2": _commons_page(2, "File:Batman character.jpg",
                               thumburl="https://commons.org/char.jpg"),
        }
        with patch("server.image_search.requests.get",
                   return_value=_commons_response(pages)):
            url = _search_commons("Batman", 500, frozenset())
        assert url == "https://commons.org/char.jpg"

    def test_skips_svg_urls(self):
        pages = {
            "1": _commons_page(1, "File:Cat.svg", thumburl="https://commons.org/cat.svg"),
            "2": _commons_page(2, "File:Cat.jpg", thumburl="https://commons.org/cat.jpg"),
        }
        with patch("server.image_search.requests.get",
                   return_value=_commons_response(pages)):
            url = _search_commons("cat", 500, frozenset())
        assert url == "https://commons.org/cat.jpg"

    def test_skips_excluded_urls(self):
        shown = "https://commons.org/shown.jpg"
        pages = {
            "1": _commons_page(1, "File:A.jpg", thumburl=shown),
            "2": _commons_page(2, "File:B.jpg", thumburl="https://commons.org/new.jpg"),
        }
        with patch("server.image_search.requests.get",
                   return_value=_commons_response(pages)):
            url = _search_commons("thing", 500, frozenset({shown}))
        assert url == "https://commons.org/new.jpg"

    def test_returns_none_on_empty_pages(self):
        with patch("server.image_search.requests.get",
                   return_value=_commons_response({})):
            url = _search_commons("obscure thing", 500, frozenset())
        assert url is None

    def test_returns_none_on_request_exception(self):
        from requests import Timeout
        with patch("server.image_search.requests.get", side_effect=Timeout()):
            url = _search_commons("cat", 500, frozenset())
        assert url is None


# ---------------------------------------------------------------------------
# _fetch_and_store_image() — variant retry logic
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock


class TestFetchAndStoreImageVariants:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_stores_url_on_primary_success(self):
        async def _go():
            with patch("server.main._sessions") as mock_sessions, \
                 patch("server.main.run_in_threadpool",
                       AsyncMock(return_value="https://example.com/img.jpg")):
                mock_sessions.get_shown_image_urls.return_value = frozenset()
                from server.main import _fetch_and_store_image
                await _fetch_and_store_image("s1", "blue whale")
                mock_sessions.set_latest_image.assert_called_once_with(
                    "s1", "https://example.com/img.jpg")
        self._run(_go())

    def test_retries_with_variants_on_primary_failure(self):
        async def _go():
            # primary returns "", first variant ("character") returns a URL
            returns = ["", "https://example.com/char.jpg"]
            mock_threadpool = AsyncMock(side_effect=returns)
            with patch("server.main._sessions") as mock_sessions, \
                 patch("server.main.run_in_threadpool", mock_threadpool):
                mock_sessions.get_shown_image_urls.return_value = frozenset()
                from server.main import _fetch_and_store_image
                await _fetch_and_store_image("s1", "Spider-Man")
                mock_sessions.set_latest_image.assert_called_once()
                assert mock_threadpool.call_count == 2  # primary + 1 variant
        self._run(_go())

    def test_no_image_stored_when_all_variants_fail(self):
        async def _go():
            mock_threadpool = AsyncMock(return_value="")
            with patch("server.main._sessions") as mock_sessions, \
                 patch("server.main.run_in_threadpool", mock_threadpool):
                mock_sessions.get_shown_image_urls.return_value = frozenset()
                from server.main import _fetch_and_store_image
                await _fetch_and_store_image("s1", "obscure term xyz")
                mock_sessions.set_latest_image.assert_not_called()
                # primary + 4 variants = 5 total calls
                assert mock_threadpool.call_count == 5
        self._run(_go())

    def test_exception_is_caught_and_does_not_propagate(self):
        async def _go():
            with patch("server.main._sessions") as mock_sessions, \
                 patch("server.main.run_in_threadpool",
                       AsyncMock(side_effect=RuntimeError("network error"))):
                mock_sessions.get_shown_image_urls.return_value = frozenset()
                from server.main import _fetch_and_store_image
                # Should not raise
                await _fetch_and_store_image("s1", "blue whale")
                mock_sessions.set_latest_image.assert_not_called()
        self._run(_go())
