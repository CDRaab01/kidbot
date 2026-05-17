import time
from unittest.mock import patch

import pytest
from server.session import MAX_TURNS, SESSION_TIMEOUT, SessionStore


class TestSessionStore:
    def setup_method(self):
        self.store = SessionStore()

    def test_new_session_returns_empty_history(self):
        history = self.store.get_history("s1")
        assert history == []

    def test_add_exchange_appends_messages(self):
        self.store.add_exchange("s1", "hello", "hi there")
        history = self.store.get_history("s1")
        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "hello"}
        assert history[1] == {"role": "assistant", "content": "hi there"}

    def test_history_trims_to_max_turns(self):
        for i in range(MAX_TURNS + 5):
            self.store.add_exchange("s1", f"q{i}", f"a{i}")
        history = self.store.get_history("s1")
        assert len(history) == MAX_TURNS * 2

    def test_history_trims_oldest_messages(self):
        for i in range(MAX_TURNS + 2):
            self.store.add_exchange("s1", f"q{i}", f"a{i}")
        history = self.store.get_history("s1")
        # The first kept message should be q2 (oldest trimmed away)
        assert history[0]["content"] == "q2"

    def test_clear_removes_session(self):
        self.store.add_exchange("s1", "hello", "hi")
        self.store.clear("s1")
        history = self.store.get_history("s1")
        assert history == []

    def test_clear_nonexistent_session_is_safe(self):
        self.store.clear("does-not-exist")  # should not raise

    def test_multiple_sessions_are_independent(self):
        self.store.add_exchange("s1", "hello", "hi")
        self.store.add_exchange("s2", "yo", "hey")
        assert len(self.store.get_history("s1")) == 2
        assert len(self.store.get_history("s2")) == 2

    def test_expired_sessions_are_purged(self):
        self.store.add_exchange("s1", "hello", "hi")

        future = time.time() + SESSION_TIMEOUT + 1
        with patch("server.session.time.time", return_value=future):
            # Accessing any session triggers _purge_expired
            self.store.get_history("s2")

        # s1 should now be gone; getting its history creates a fresh empty one
        history = self.store.get_history("s1")
        assert history == []

    def test_get_history_returns_copy(self):
        self.store.add_exchange("s1", "q", "a")
        h1 = self.store.get_history("s1")
        h1.append({"role": "user", "content": "injected"})
        h2 = self.store.get_history("s1")
        assert len(h2) == 2  # internal state unchanged
