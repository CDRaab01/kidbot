#!/usr/bin/env python3
"""
Send text to KidBot and print the reply.

Usage:
  python scripts/send_text.py "hello there"   # one-shot
  python scripts/send_text.py                 # interactive loop

Env vars:
  KIDBOT_URL      default http://localhost:8765
  KIDBOT_SESSION  default cli-test
  KIDBOT_API_KEY  default empty
"""
import os
import sys

import requests

SERVER  = os.getenv("KIDBOT_URL", "http://localhost:8765")
SESSION = os.getenv("KIDBOT_SESSION", "cli-test")
API_KEY = os.getenv("KIDBOT_API_KEY", "")

_headers = {"X-API-Key": API_KEY} if API_KEY else {}


def send(text: str) -> None:
    try:
        resp = requests.post(
            f"{SERVER}/chat_text",
            data={"text": text, "session_id": SESSION},
            headers=_headers,
            timeout=90,
        )
        resp.raise_for_status()
    except requests.ConnectionError:
        print(f"[error] Cannot reach {SERVER} — is the server running?")
        return
    except requests.HTTPError as exc:
        print(f"[error] HTTP {exc.response.status_code}: {exc.response.text[:200]}")
        return

    reply = resp.headers.get("X-Reply", "")
    image = resp.headers.get("X-Image-Url", "")

    print(f"You : {text}")
    print(f"Bot : {reply or '(empty reply)'}")
    if image:
        print(f"Img : {image}")
    print()


def main() -> None:
    if len(sys.argv) > 1:
        send(" ".join(sys.argv[1:]))
        return

    print(f"KidBot CLI  server={SERVER}  session={SESSION}")
    print("Type a message and press Enter. Ctrl+C to quit.\n")
    while True:
        try:
            text = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break
        if text:
            send(text)


if __name__ == "__main__":
    main()
