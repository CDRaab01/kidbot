"""
Multi-source image search for kid-friendly content.

Sources (in priority order):
  1. Wikipedia   — broad encyclopedic coverage, always child-appropriate
  2. NASA Images — space, planets, spacecraft, science
  3. iNaturalist — animals, plants, insects, nature

All three sources are free, require no API key, and serve educational content.
Sources run in parallel; the highest-priority result wins.

Pass `exclude_urls` to skip previously shown images within the same session,
so repeated requests for the same topic surface fresh content.
"""
import concurrent.futures
import logging
import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "CooperBot/1.0 (educational child chatbot; contact via github)"}
_TIMEOUT = 5  # seconds per source


# ---------------------------------------------------------------------------
# Source implementations
# ---------------------------------------------------------------------------

def _search_wikipedia(term: str, size: int, exclude: frozenset) -> str | None:
    """Wikipedia pageimages API — broad encyclopedic coverage."""
    try:
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": term,
                "gsrlimit": 10,
                "prop": "pageimages",
                "format": "json",
                "pithumbsize": size,
                "redirects": 1,
            },
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {}).values()
        for page in sorted(pages, key=lambda p: p.get("index", 99)):
            url = page.get("thumbnail", {}).get("source", "")
            if url and url not in exclude:
                return url
    except Exception as exc:
        logger.warning("Wikipedia image search failed for %r: %s", term, exc)
    return None


def _search_nasa(term: str, size: int, exclude: frozenset) -> str | None:
    """NASA Image and Video Library — space, science, engineering imagery."""
    try:
        resp = requests.get(
            "https://images-api.nasa.gov/search",
            params={"q": term, "media_type": "image"},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json().get("collection", {}).get("items", [])
        for item in items:
            for link in item.get("links", []):
                href = link.get("href", "")
                if (href
                        and href not in exclude
                        and any(href.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif"))):
                    return href
    except Exception as exc:
        logger.warning("NASA image search failed for %r: %s", term, exc)
    return None


def _search_inaturalist(term: str, size: int, exclude: frozenset) -> str | None:
    """iNaturalist taxa — animals, plants, insects, and nature photography."""
    try:
        resp = requests.get(
            "https://api.inaturalist.org/v1/taxa",
            params={"q": term, "per_page": 10},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        for result in resp.json().get("results", []):
            photo = result.get("default_photo") or {}
            url = photo.get("medium_url") or photo.get("square_url", "")
            if url and url not in exclude:
                return url
    except Exception as exc:
        logger.warning("iNaturalist image search failed for %r: %s", term, exc)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_image_url(
    term: str,
    size: int = 500,
    exclude_urls: list[str] | None = None,
) -> str | None:
    """
    Search all kid-friendly image sources in parallel.
    Returns the highest-priority source result, or None if all sources fail.

    Priority: Wikipedia → NASA → iNaturalist

    `exclude_urls` — URLs already shown in this session; each source skips
    them so the child gets fresh content when requesting more on the same topic.

    Sources are resolved by name at call time so they can be patched in tests.
    """
    exclude = frozenset(exclude_urls or [])
    sources = [_search_wikipedia, _search_nasa, _search_inaturalist]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sources)) as executor:
        indexed = {i: executor.submit(src, term, size, exclude) for i, src in enumerate(sources)}
        done, _ = concurrent.futures.wait(indexed.values(), timeout=8)

    for i, src in enumerate(sources):
        fut = indexed[i]
        if fut not in done:
            continue
        try:
            url = fut.result()
        except Exception:
            continue
        if url:
            logger.info("Image found for %r via %s: %s", term, getattr(src, '__name__', src), url)
            return url
    return None
