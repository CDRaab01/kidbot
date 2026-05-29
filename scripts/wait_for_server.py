#!/usr/bin/env python3
"""Poll /health until the server is ready or the timeout expires.

On a first deploy the server downloads the Whisper model (~250 MB) and loads
all three models before /health returns 200, which can take several minutes,
so the default timeout is generous and overridable via WAIT_FOR_SERVER_TIMEOUT.
"""
import os
import sys
import time

import requests

URL        = os.getenv("KIDBOT_HEALTH_URL", "http://localhost:8765/health")
TIMEOUT_S  = int(os.getenv("WAIT_FOR_SERVER_TIMEOUT", "300"))
INTERVAL_S = int(os.getenv("WAIT_FOR_SERVER_INTERVAL", "5"))


def wait_for_server(url: str = URL, timeout_s: int = TIMEOUT_S,
                    interval_s: int = INTERVAL_S) -> bool:
    """Return True once the server reports healthy, False on timeout."""
    for attempt in range(max(1, timeout_s // interval_s)):
        try:
            if requests.get(url, timeout=5).ok:
                print(f"Server ready ({attempt * interval_s}s elapsed)")
                return True
        except Exception:
            pass
        print(f"Waiting... ({attempt * interval_s}s elapsed)")
        time.sleep(interval_s)
    print(f"Server did not become healthy within {timeout_s}s")
    return False


def main() -> None:
    sys.exit(0 if wait_for_server() else 1)


if __name__ == "__main__":
    main()
