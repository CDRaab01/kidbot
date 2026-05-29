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
