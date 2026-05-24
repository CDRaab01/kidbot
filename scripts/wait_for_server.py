#!/usr/bin/env python3
"""Poll /health until the server is ready or the timeout expires."""
import sys
import time

import requests

URL        = "http://localhost:8765/health"
TIMEOUT_S  = 120
INTERVAL_S = 5

for attempt in range(TIMEOUT_S // INTERVAL_S):
    try:
        if requests.get(URL, timeout=5).ok:
            print(f"Server ready ({attempt * INTERVAL_S}s elapsed)")
            sys.exit(0)
    except Exception:
        pass
    print(f"Waiting... ({attempt * INTERVAL_S}s elapsed)")
    time.sleep(INTERVAL_S)

print(f"Server did not become healthy within {TIMEOUT_S}s")
sys.exit(1)
