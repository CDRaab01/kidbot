#!/usr/bin/env python3
"""
Test whether KidBot's image results are relevant to the topic requested.

For each test case, sends a message to the KidBot server, collects the image
URL, then asks a vision LLM "does this image show [topic]?" and reports
pass/fail.  Falls back to URL-only display if no vision model is available.

Usage:
  python scripts/test_images.py                        # run all built-in tests
  python scripts/test_images.py "Spiderman"            # test one specific topic
  python scripts/test_images.py --no-vision            # skip vision check
  python scripts/test_images.py --open                 # open each image in browser

Env vars:
  KIDBOT_URL       default http://localhost:8765
  KIDBOT_API_KEY   default empty (no auth)
  LM_STUDIO_URL    default http://localhost:1234/v1 (for vision check)
  VISION_MODEL     force a specific model ID (otherwise auto-detected)
"""
import os
import sys
import time
import uuid
import webbrowser

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SERVER       = os.getenv("KIDBOT_URL",    "http://localhost:8765")
API_KEY      = os.getenv("KIDBOT_API_KEY", "")
LM_URL       = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1")
VISION_MODEL = os.getenv("VISION_MODEL",  "")

_bot_headers = {"X-API-Key": API_KEY} if API_KEY else {}

# ---------------------------------------------------------------------------
# Built-in test cases: (message to send, plain-English topic to verify)
# ---------------------------------------------------------------------------
# Prompts explicitly request a picture: the system prompt only emits an
# [IMAGE: ...] tag on an explicit picture request, so "tell me about X" would
# (correctly) return no image and fail this test. "show me a picture of X"
# exercises the real image path.
DEFAULT_TESTS: list[tuple[str, str]] = [
    ("show me a picture of Spider-Man",        "Spider-Man (the Marvel superhero in a red and blue costume)"),
    ("show me a picture of Batman",            "Batman (the DC superhero in a dark cape and cowl)"),
    ("show me a picture of Elsa from Frozen",  "Elsa from Frozen (a blonde woman with ice powers)"),
    ("show me a picture of a blue whale",      "a blue whale (the largest animal on Earth)"),
    ("show me a picture of a T-Rex dinosaur",  "a Tyrannosaurus Rex dinosaur"),
    ("show me a picture of a volcano",         "a volcano (mountain with lava or eruption)"),
    ("show me a picture of the moon",          "the moon (Earth's natural satellite)"),
    ("show me a picture of a rainbow",         "a rainbow (colourful arc in the sky)"),
    ("show me a picture of an elephant",       "an elephant (large grey animal with a trunk)"),
    ("show me a picture of the Eiffel Tower",  "the Eiffel Tower (famous iron tower in Paris)"),
]


# ---------------------------------------------------------------------------
# KidBot query
# ---------------------------------------------------------------------------
def _query_kidbot(message: str, session_id: str) -> tuple[str, str]:
    """POST to /chat_text. Returns (bot_reply, image_url).

    The chat endpoint is rate-limited (5/min per IP) and slowapi keys on the
    source address, so firing the whole test batch from one host trips the
    limiter. On HTTP 429 we honour Retry-After and retry instead of counting
    the throttled request as a failure.
    """
    for _ in range(6):
        try:
            resp = requests.post(
                f"{SERVER}/chat_text",
                data={"text": message, "session_id": session_id},
                headers=_bot_headers,
                timeout=90,
            )
        except requests.ConnectionError:
            print(f"[error] Cannot reach {SERVER} — is the server running?")
            sys.exit(1)

        if resp.status_code == 429:
            try:
                wait = int(resp.headers.get("Retry-After", "")) + 1
            except (TypeError, ValueError):
                wait = 13
            print(f"  Rate limited — waiting {wait}s before retrying...")
            time.sleep(wait)
            continue

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            print(f"[error] HTTP {exc.response.status_code}: {exc.response.text[:200]}")
            return "", ""
        return resp.headers.get("X-Reply", ""), resp.headers.get("X-Image-Url", "")

    print("[error] Still rate limited after several retries.")
    return "", ""


# ---------------------------------------------------------------------------
# Vision check via LM Studio
# ---------------------------------------------------------------------------
# LM Studio's /v1/models lists every loaded model, not just vision-capable
# ones. Picking the first entry blindly broke when the first model returned
# was e.g. flux.2-klein-9b (image gen → 400 on chat/completions). This
# deny-list skips obvious non-chat models so auto-detect lands on a
# chat-completions model that can at least accept the vision-check payload.
# (If the chosen model can't process images, _check_with_vision surfaces a
# clear error — better than the silent 400 from picking Flux.)
_NON_CHAT_MODEL_PATTERNS = (
    "flux", "stable-diffusion", "sdxl", "sd-",
    "embed", "bge-", "e5-", "nomic-", "all-minilm",
    "whisper", "kokoro",
)
_NON_CHAT_MODEL_TYPES = ("embeddings", "embedding", "image", "diffusion",
                         "audio", "tts", "stt")


def _is_chat_model(model: dict) -> bool:
    """True if the LM Studio model entry looks like a chat-completions model
    (vision or text). Best-effort filter — prefers explicit type metadata,
    falls back to a name deny-list."""
    mtype = (model.get("type") or "").lower()
    if mtype in _NON_CHAT_MODEL_TYPES:
        return False
    model_id = (model.get("id") or "").lower()
    return not any(pat in model_id for pat in _NON_CHAT_MODEL_PATTERNS)


def _detect_vision_model() -> str | None:
    """Return the first chat-capable model id from LM Studio, or None."""
    try:
        resp = requests.get(f"{LM_URL}/models", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("data", [])
        for m in models:
            if _is_chat_model(m):
                return m["id"]
        return None
    except Exception:
        return None


def _check_with_vision(image_url: str, topic: str, model: str) -> tuple[bool | None, str]:
    """
    Ask the vision model whether the image matches the topic.
    Returns (is_relevant, explanation).  is_relevant is None if the check fails.
    """
    try:
        from openai import OpenAI  # already a project dependency

        client = OpenAI(base_url=LM_URL, api_key="not-needed")
        rsp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {
                        "type": "text",
                        "text": (
                            f"Does this image clearly show {topic}?\n"
                            "Answer with YES or NO on the first line, then one sentence explaining why."
                        ),
                    },
                ],
            }],
            max_tokens=120,
            timeout=30,
        )
        answer = rsp.choices[0].message.content.strip()
        first = answer.split("\n")[0].upper()
        is_relevant = first.startswith("YES")
        return is_relevant, answer
    except Exception as exc:
        return None, f"vision check error: {exc}"


# ---------------------------------------------------------------------------
# HEAD check — used in --no-vision mode so CI catches dead URLs / HTML
# redirects / non-image content that the vision-LLM check would otherwise
# need to flag. Cheap: ~one round-trip per URL.
# ---------------------------------------------------------------------------
def _head_check(image_url: str) -> tuple[bool, str]:
    """HEAD-request the image URL. Returns (ok, explanation).

    Fails the gate on:
      - HEAD raises (DNS failure, timeout, refused)
      - non-200 status (404 dead Wikimedia node, 403 auth-walled redirect)
      - Content-Type that isn't image/* (HTML 'this page has moved' fallback)
    """
    try:
        r = requests.head(image_url, allow_redirects=True, timeout=5)
    except requests.RequestException as exc:
        return False, f"HEAD failed: {exc.__class__.__name__}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        return False, f"not an image (Content-Type: {ctype or 'unknown'})"
    return True, f"reachable {ctype}"


# ---------------------------------------------------------------------------
# Dedup check — verifies exclude_urls is wired through end to end. Asking for
# ANOTHER picture of the same topic in the same session must return a
# different URL. iNaturalist's elephant index has hundreds of photos, so the
# probability of a same-URL collision is negligible.
# ---------------------------------------------------------------------------
def run_dedup_check() -> int:
    """Returns 1 if exclude_urls is broken (same URL twice), else 0."""
    print("\nDedup check — 'another picture' must return a different URL")
    session = f"dedup-test-{uuid.uuid4().hex[:8]}"

    _, url1 = _query_kidbot("show me a picture of an elephant", session)
    if not url1:
        print("  Result   : FAIL (no URL on first request)\n")
        return 1
    print(f"  URL #1: {url1}")

    time.sleep(0.5)

    _, url2 = _query_kidbot("show me another picture of an elephant", session)
    if not url2:
        print("  Result   : FAIL (no URL on second request)\n")
        return 1
    print(f"  URL #2: {url2}")

    if url1 == url2:
        print("  Result   : FAIL (duplicate URL — exclude_urls not honoured)\n")
        return 1

    print("  Result   : PASS (URLs differ)\n")
    return 0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run(
    tests: list[tuple[str, str]],
    use_vision: bool = True,
    open_browser: bool = False,
) -> int:
    """Run tests. Returns number of failures (images graded as NOT relevant)."""

    print(f"\nKidBot Image Relevance Test")
    print(f"  Server : {SERVER}")
    print(f"  Vision : ", end="")

    vision_model: str | None = None
    if use_vision:
        forced = VISION_MODEL or None
        vision_model = forced or _detect_vision_model()
        if vision_model:
            print(f"{vision_model}")
        else:
            print("none detected — URLs only")
    else:
        print("disabled")

    print()

    col_w = 42
    failures = 0

    for i, (message, topic) in enumerate(tests, 1):
        session = f"img-test-{i}"
        print(f"[{i}/{len(tests)}] {message!r}")

        reply, image_url = _query_kidbot(message, session)

        if not image_url:
            print(f"  Image URL: (none returned)")
            print(f"  Bot said : {reply[:80]}")
            print(f"  Result   : FAIL\n")
            failures += 1
            continue

        print(f"  Image URL: {image_url}")

        if open_browser:
            webbrowser.open(image_url)
            time.sleep(0.3)

        if vision_model:
            relevant, explanation = _check_with_vision(image_url, topic, vision_model)
            if relevant is True:
                verdict = "PASS"
            elif relevant is False:
                verdict = "FAIL"
                failures += 1
            else:
                verdict = "?"
            print(f"  Vision   : {explanation}")
            print(f"  Result   : {verdict}\n")
        else:
            ok, explanation = _head_check(image_url)
            print(f"  HEAD     : {explanation}")
            if ok:
                print("  Result   : PASS\n")
            else:
                print("  Result   : FAIL\n")
                failures += 1

        time.sleep(0.5)

    return failures


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    args = sys.argv[1:]

    no_vision  = "--no-vision" in args
    open_flag  = "--open" in args
    topics     = [a for a in args if not a.startswith("--")]

    if topics:
        # Single ad-hoc topic: turn the bare topic into an explicit picture
        # request so the image path is exercised. A full message in quotes
        # (lowercase, >3 words) is sent verbatim.
        topic = " ".join(topics)
        if not topic[0].islower() or len(topic.split()) <= 3:
            message = f"show me a picture of {topic}"
        else:
            message = topic
        tests = [(message, topic)]
    else:
        tests = DEFAULT_TESTS

    failures = run(tests, use_vision=not no_vision, open_browser=open_flag)

    # Full-suite run only — the single-topic path is for ad-hoc debugging, not
    # the deploy gate. Dedup needs two queries against one session, which would
    # add unwanted noise to "tell me one image".
    if not topics:
        failures += run_dedup_check()

    if failures:
        print(f"{failures} image check(s) failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
