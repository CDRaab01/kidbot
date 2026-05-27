"""
Fixtures for live integration tests.

Tests in this directory make real HTTP requests to a running KidBot server.
They are skipped automatically if the server is not reachable.

Set KIDBOT_URL to target a non-default server (default: http://localhost:8765).
Set KIDBOT_API_KEY if the server requires authentication.
"""
import os
import pytest
import requests


_SERVER = os.getenv("KIDBOT_URL", "http://localhost:8765")
_API_KEY = os.getenv("KIDBOT_API_KEY", "")


def _headers() -> dict:
    return {"X-API-Key": _API_KEY} if _API_KEY else {}


def _server_running() -> bool:
    try:
        r = requests.get(f"{_SERVER}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def require_server():
    if not _server_running():
        pytest.skip(f"KidBot server not reachable at {_SERVER} — skipping live tests")


@pytest.fixture(scope="session")
def server():
    return _SERVER


@pytest.fixture(scope="session")
def headers():
    return _headers()


@pytest.fixture
def session_id(request):
    """Unique session ID per test so history never bleeds between tests."""
    return f"live-test-{request.node.name}"
