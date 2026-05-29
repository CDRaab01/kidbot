"""Tests for server/config.py env overrides."""
import importlib

import server.config as cfg


def test_server_host_port_env_override(monkeypatch):
    monkeypatch.setenv("SERVER_PORT", "9999")
    monkeypatch.setenv("SERVER_HOST", "127.0.0.1")
    importlib.reload(cfg)
    try:
        assert cfg.SERVER_PORT == 9999
        assert cfg.SERVER_HOST == "127.0.0.1"
    finally:
        monkeypatch.undo()
        importlib.reload(cfg)  # restore defaults for other tests


def test_server_defaults(monkeypatch):
    monkeypatch.delenv("SERVER_PORT", raising=False)
    monkeypatch.delenv("SERVER_HOST", raising=False)
    importlib.reload(cfg)
    assert cfg.SERVER_HOST == "0.0.0.0"
    assert cfg.SERVER_PORT == 8765
    assert isinstance(cfg.SERVER_PORT, int)


def test_session_timeout_default_remembers_across_the_day(monkeypatch):
    monkeypatch.delenv("SESSION_TIMEOUT_HOURS", raising=False)
    importlib.reload(cfg)
    # Default must comfortably exceed a 30-minute gap (the old behaviour).
    assert cfg.SESSION_TIMEOUT >= 24 * 3600


def test_session_timeout_env_override(monkeypatch):
    monkeypatch.setenv("SESSION_TIMEOUT_HOURS", "2")
    importlib.reload(cfg)
    try:
        assert cfg.SESSION_TIMEOUT == 2 * 3600
    finally:
        monkeypatch.undo()
        importlib.reload(cfg)
