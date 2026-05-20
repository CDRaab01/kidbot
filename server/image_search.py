"""
Multi-source image search for kid-friendly content.

Sources (in priority order):
  1. Wikipedia        — three-tier: direct subject lookup → pageimages search
                         → REST summary fallback for fair-use lead images.
                         Tier 0 direct lookup is the fastest path for named
                         subjects (Spider-Man, Saturn, T-Rex, etc.).
  2. Wikimedia Commons — freely licensed images incl. fictional characters,
                         logos, promotional art — fills the gap Wikipedia
                         misses for copyrighted topics (superheroes, etc.)
  3. NASA Images      — space, planets, spacecraft, science
  4. iNaturalist      — animals, plants, insects, nature

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


# ---------------------------------------------------------------------------
# Source implementations
# ---------------------------------------------------------------------------

def _search_wikipedia(term: str, size: int, exclude: frozenset) -> str | None:
    """
    Wikipedia image search — three-tier approach:
    0. Direct REST summary on the primary subject (first word of the term).
       Fastest and most reliable — catches fair-use thumbnails immediately
       without relying on the search API finding the right article.
       "Spider-Man Marvel Comics character" → looks up "Spider-Man" directly.
    1. pageimages API (search-based, one call) — works for most real-world topics.
    2. REST summary API fallback — pageimages silently returns no thumbnail
       for articles whose lead image is a fair-use file (fictional characters,
       film/TV subjects, etc.).  The REST summary endpoint returns the
       thumbnail URL regardless of licence.
    """
    # --- Tier 0: direct REST summary on the primary subject --------------
    primary_subject = term.split()[0] if term.strip() else ""
    if primary_subject:
        try:
            r = requests.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{primary_subject}",
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                thumb = r.json().get("thumbnail", {}).get("source", "")
                if thumb and thumb not in exclude:
                    logger.debug("Wikipedia Tier 0 hit for %r via %r", term, primary_subject)
                    return thumb
        except Exception:
            pass

    # --- Tier 1 & 2: search-based ----------------------------------------
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
            # Tier 1: pageimages thumbnail (free-licence images only)
            url = page.get("thumbnail", {}).get("source", "")
            if url and url not in exclude:
                return url

        # Tier 2: REST summary API — catches fair-use lead images
        for page in pages:
            title = page.get("title", "").replace(" ", "_")
            if not title:
                continue
            try:
                r = requests.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
                    headers=_HEADERS,
                    timeout=_TIMEOUT,
                )
                if r.status_code == 200:
                    thumb = r.json().get("thumbnail", {}).get("source", "")
                    if thumb and thumb not in exclude:
                        return thumb
            except Exception:
                continue

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
        # Match only on the first/primary word to avoid false positives from
        # common English words in multi-word queries.  E.g. "Spider-Man Marvel
        # Comics character" previously matched NASA titles containing "marvel"
        # (used as an ordinary word like "marvel at this view of Saturn").
        primary = term.lower().split()[0] if term.strip() else ""
        items = resp.json().get("collection", {}).get("items", [])
        for item in items:
            # Only return images whose title actually contains the primary subject.
            data = item.get("data", [{}])[0]
            title = data.get("title", "").lower()
            if primary and primary not in title:
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


def _search_commons(term: str, size: int, exclude: frozenset) -> str | None:
    """
    Wikimedia Commons — freely licensed images for fictional characters,
    logos, promotional art and anything Wikipedia's pageimages API skips
    because the lead image is a fair-use/copyrighted file.
    """
    try:
        # Push logo/text-art exclusions into the query so the API ranks them
        # out before we even see the filenames.
        search_query = f"{term} -logo -banner -text -svg -symbol -icon -wordmark"
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrnamespace": 6,          # File: namespace only
                "gsrsearch": search_query,
                "gsrlimit": 10,
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
            # Skip decorative / text-art files that aren't actual images
            if any(w in name for w in _SKIP_WORDS):
                continue
            for ii in page.get("imageinfo", []):
                url = ii.get("thumburl") or ii.get("url", "")
                # Skip SVGs — they sometimes don't render in Tkinter
                if url and url not in exclude and not url.lower().endswith(".svg"):
                    return url
    except Exception as exc:
        logger.warning("Commons image search failed for %r: %s", term, exc)
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
    sources = [_search_wikipedia, _search_commons, _search_nasa, _search_inaturalist]

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
