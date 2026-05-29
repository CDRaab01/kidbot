"""Per-test isolation for server tests.

server.main holds a single process-global SessionStore (_sessions). Without a
reset between tests, conversation history, latest replies and latest image URLs
written by one test leak into any later test that reuses the same session id —
including a real image URL stored by a late-running background fetch. Clear it
around every test so state can't bleed across the suite.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_session_store():
    from server.main import _sessions
    _sessions._sessions.clear()
    yield
    _sessions._sessions.clear()
