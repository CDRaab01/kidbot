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
DEFAULT_TESTS: list[tuple[str, str]] = [
    ("tell me about Spiderman",            "Spider-Man (the Marvel superhero in a red and blue costume)"),
    ("tell me about Batman",               "Batman (the DC superhero in a dark cape and cowl)"),
    ("tell me about Elsa from Frozen",     "Elsa from Frozen (a blonde woman with ice powers)"),
    ("what is a blue whale?",              "a blue whale (the largest animal on Earth)"),
    ("tell me about a T-Rex dinosaur",     "a Tyrannosaurus Rex dinosaur"),
    ("what is a volcano?",                 "a volcano (mountain with lava or eruption)"),
    ("tell me about the moon",             "the moon (Earth's natural satellite)"),
    ("what is a rainbow?",                 "a rainbow (colourful arc in the sky)"),
    ("tell me about an elephant",          "an elephant (large grey animal with a trunk)"),
    ("what is the Eiffel Tower?",          "the Eiffel Tower (famous iron tower in Paris)"),
]


# ---------------------------------------------------------------------------
# KidBot query
# ---------------------------------------------------------------------------
def _query_kidbot(message: str, session_id: str) -> tuple[str, str]:
    """POST to /chat_text. Returns (bot_reply, image_url)."""
    try:
        resp = requests.post(
            f"{SERVER}/chat_text",
            data={"text": message, "session_id": session_id},
            headers=_bot_headers,
            timeout=90,
        )
        resp.raise_for_status()
    except requests.ConnectionError:
        print(f"[error] Cannot reach {SERVER} — is the server running?")
        sys.exit(1)
    except requests.HTTPError as exc:
        print(f"[error] HTTP {exc.response.status_code}: {exc.response.text[:200]}")
        return "", ""
    return resp.headers.get("X-Reply", ""), resp.headers.get("X-Image-Url", "")


# ---------------------------------------------------------------------------
# Vision check via LM Studio
# ---------------------------------------------------------------------------
def _detect_vision_model() -> str | None:
    """Return the first model ID from LM Studio, or None if none are loaded."""
    try:
        resp = requests.get(f"{LM_URL}/models", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("data", [])
        return models[0]["id"] if models else None
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
            print(f"  {'Image URL':<12}: (none returned)\n  Bot said : {reply[:80]}\n  Result   : SKIP\n")
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
            print(f"  Result   : (open URL above to verify manually)\n")

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
        # Single ad-hoc topic: turn the bare topic into a "tell me about X" query
        # The user can also pass the full message in quotes.
        topic = " ".join(topics)
        if not topic[0].islower() or len(topic.split()) <= 3:
            message = f"tell me about {topic}"
        else:
            message = topic
        tests = [(message, topic)]
    else:
        tests = DEFAULT_TESTS

    failures = run(tests, use_vision=not no_vision, open_browser=open_flag)

    if failures:
        print(f"{failures} image(s) graded as NOT relevant.")
        sys.exit(1)


if __name__ == "__main__":
    main()
