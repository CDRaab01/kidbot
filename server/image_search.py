import logging
import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "CooperBot/1.0 (educational child chatbot; contact via github)"}


def fetch_image_url(term: str, size: int = 500) -> str | None:
    """
    Search Wikipedia for an image of `term`.
    Uses the Wikipedia pageimages API — no API key, always child-appropriate.
    Returns a thumbnail URL or None if nothing found.
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
            timeout=6,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {}).values()
        # Sort by search index so we pick the most relevant result
        for page in sorted(pages, key=lambda p: p.get("index", 99)):
            url = page.get("thumbnail", {}).get("source", "")
            if url:
                logger.info("Image found for %r: %s", term, url)
                return url
    except Exception as exc:
        logger.warning("Image search failed for %r: %s", term, exc)
    return None
