"""
Multi-source image search for kid-friendly content.

Sources (in priority order):
  1. OpenVerse       — Creative Commons image aggregator (Flickr, museums,
                        Wikimedia, etc.).  Best for pop culture, cosplay,
                        fan art, and educational content.  No API key needed
                        for home use (100 req/day); register free for more.
  2. Wikimedia Commons — freely licensed images for fictional characters,
                         places, science, nature.
  3. Wikipedia        — pageimages API only (free-licensed lead images).
                         Correctly returns nothing for fair-use articles
                         (fictional characters) so sources 1/2 fill in.
  4. NASA Images      — space/science only (guarded by topic keywords).
                         Never queried for non-space subjects.
  5. iNaturalist      — animals, plants, insects, nature photography.

All sources are free, require no API key, and serve educational content.
Sources run in parallel; the highest-priority result wins.

Pass `exclude_urls` to skip previously shown images within the same session,
so repeated requests for the same topic surface fresh content.
"""
import concurrent.futures
import logging
import requests

from .config import BOT_NAME

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": f"{BOT_NAME}/1.0 (educational child chatbot; contact via github)"}
_TIMEOUT = 5  # seconds per source

# Keywords that flag a search as space/science — NASA is only queried when
# at least one of these appears in the search term.  This prevents NASA's
# Marvel/NASA collaboration photos (astronauts in Spider-Man suits, etc.)
# from appearing for fictional-character searches.
_NASA_TOPIC_WORDS = frozenset({
    "space", "nasa", "planet", "star", "galaxy", "nebula", "rocket",
    "astronaut", "cosmonaut", "moon", "mars", "saturn", "jupiter", "venus",
    "mercury", "uranus", "neptune", "comet", "asteroid", "meteor",
    "shuttle", "iss", "hubble", "telescope", "solar", "orbit", "satellite",
    "spacecraft", "launch", "apollo", "artemis", "cosmic", "universe",
    "milky", "supernova", "exoplanet", "crater", "eclipse",
})


def _is_nasa_topic(term: str) -> bool:
    t = term.lower()
    return any(kw in t for kw in _NASA_TOPIC_WORDS)


# ---------------------------------------------------------------------------
# Source implementations
# ---------------------------------------------------------------------------

def _search_openverse(term: str, size: int, exclude: frozenset) -> str | None:
    """
    OpenVerse (Creative Commons image search) — aggregates Flickr, Wikimedia,
    museums and more.  Returns only CC-licensed images with mature=false.
    No API key required for up to 100 requests/day (home use).

    Uses the primary subject word so short, focused queries rank well
    (e.g. "Spider-Man" from "Spider-Man Marvel Comics character").
    """
    primary = term.split()[0] if term.strip() else term
    try:
        resp = requests.get(
            "https://api.openverse.org/v1/images/",
            params={
                "q": primary,
                "mature": "false",
                "page_size": 10,
            },
            headers={**_HEADERS, "Accept": "application/json"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        for result in resp.json().get("results", []):
            url = result.get("thumbnail") or result.get("url", "")
            if url and url not in exclude and not url.lower().endswith(".svg"):
                return url
    except Exception as exc:
        logger.warning("OpenVerse image search failed for %r: %s", term, exc)
    return None


def _search_commons(term: str, size: int, exclude: frozenset) -> str | None:
    """
    Wikimedia Commons — freely licensed images for fictional characters,
    places, science, and nature.
    """
    primary = term.split()[0] if term.strip() else term
    search_query = f"{primary} -logo -banner -text -svg -symbol -icon -wordmark"
    try:
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrnamespace": 6,          # File: namespace only
                "gsrsearch": search_query,
                "gsrlimit": 20,             # larger window → cosplay/photo files survive filtering
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": size,
                "format": "json",
            },
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        _SKIP_WORDS = frozenset({
            "logo", "banner", "text", "title", "wordmark", "symbol",
            "icon", "emblem", "badge", "seal", "flag", "insignia",
            "lettering", "font", "typography",
        })
        pages = resp.json().get("query", {}).get("pages", {}).values()
        for page in sorted(pages, key=lambda p: p.get("index", 99)):
            name = page.get("title", "").lower()
            if any(w in name for w in _SKIP_WORDS):
                continue
            for ii in page.get("imageinfo", []):
                url = ii.get("thumburl") or ii.get("url", "")
                if url and url not in exclude and not url.lower().endswith(".svg"):
                    return url
    except Exception as exc:
        logger.warning("Commons image search failed for %r: %s", term, exc)
    return None


def _search_wikipedia(term: str, size: int, exclude: frozenset) -> str | None:
    """
    Wikipedia pageimages search — returns free-licensed thumbnails only.

    Does NOT use the REST summary API (which returns fair-use thumbnails too)
    because for fictional characters that means actor/press photos rather than
    the character itself.  pageimages-only correctly returns nothing for
    fair-use articles, letting OpenVerse/Commons fill in.
    """
    try:
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": term,
                "gsrlimit": 5,
                "prop": "pageimages",
                "format": "json",
                "pithumbsize": size,
                "redirects": 1,
            },
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        pages = sorted(
            resp.json().get("query", {}).get("pages", {}).values(),
            key=lambda p: p.get("index", 99),
        )
        for page in pages:
            url = page.get("thumbnail", {}).get("source", "")
            if url and url not in exclude:
                return url
    except Exception as exc:
        logger.warning("Wikipedia image search failed for %r: %s", term, exc)
    return None


def _search_nasa(term: str, size: int, exclude: frozenset) -> str | None:
    """
    NASA Image and Video Library — space, science, engineering imagery.

    Only queried when the search term contains space/science keywords.
    This prevents NASA's Marvel/NASA collaboration content (astronauts in
    superhero costumes, themed exhibits) from appearing for character searches.
    """
    if not _is_nasa_topic(term):
        return None
    try:
        resp = requests.get(
            "https://images-api.nasa.gov/search",
            params={"q": term, "media_type": "image"},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        # Require the primary subject word in the image title.
        primary = term.lower().split()[0] if term.strip() else ""
        items = resp.json().get("collection", {}).get("items", [])
        for item in items:
            data = (item.get("data") or [{}])[0]
            title = data.get("title", "").lower()
            if primary and title and primary not in title:
                continue
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

    Priority: OpenVerse → Commons → Wikipedia → NASA (space only) → iNaturalist

    `exclude_urls` — URLs already shown in this session; each source skips
    them so the child gets fresh content when requesting more on the same topic.

    Sources are resolved by name at call time so they can be patched in tests.
    """
    exclude = frozenset(exclude_urls or [])
    sources = [_search_openverse, _search_commons, _search_wikipedia,
               _search_nasa, _search_inaturalist]

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
    logger.warning("No image found for %r from any source.", term)
    return None
