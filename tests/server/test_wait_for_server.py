"""Tests for scripts/wait_for_server.py (loaded by path — scripts/ isn't a package)."""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "wait_for_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("wait_for_server", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load()


def test_returns_true_when_healthy(mod):
    ok = MagicMock()
    ok.ok = True
    with patch("requests.get", return_value=ok), patch("time.sleep"):
        assert mod.wait_for_server("http://x/health", timeout_s=30, interval_s=5) is True


def test_retries_then_succeeds(mod):
    bad, good = MagicMock(ok=False), MagicMock(ok=True)
    with patch("requests.get", side_effect=[bad, good]), patch("time.sleep") as sleep:
        assert mod.wait_for_server("http://x/health", timeout_s=30, interval_s=5) is True
    sleep.assert_called_once()


def test_tolerates_connection_errors(mod):
    good = MagicMock(ok=True)
    with patch("requests.get", side_effect=[requests.ConnectionError(), good]), \
         patch("time.sleep"):
        assert mod.wait_for_server("http://x/health", timeout_s=30, interval_s=5) is True


def test_returns_false_on_timeout(mod):
    with patch("requests.get", return_value=MagicMock(ok=False)), patch("time.sleep"):
        assert mod.wait_for_server("http://x/health", timeout_s=10, interval_s=5) is False


def test_default_timeout_is_generous(mod):
    # First-deploy Whisper download + model load can take minutes.
    assert mod.TIMEOUT_S >= 300
