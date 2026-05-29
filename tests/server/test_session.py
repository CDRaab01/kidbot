import sqlite3
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


class TestSQLiteSessionStore:
    def test_add_exchange_persisted_and_reloaded(self, tmp_path):
        db = str(tmp_path / "sessions.db")
        store1 = SessionStore(db_path=db)
        store1.add_exchange("s1", "hello", "hi there")

        store2 = SessionStore(db_path=db)
        history = store2.get_history("s1")
        assert history == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

    def test_clear_removes_from_db(self, tmp_path):
        db = str(tmp_path / "sessions.db")
        store1 = SessionStore(db_path=db)
        store1.add_exchange("s1", "hi", "hello")
        store1.clear("s1")

        store2 = SessionStore(db_path=db)
        assert store2.get_history("s1") == []

    def test_expired_sessions_not_reloaded(self, tmp_path):
        db = str(tmp_path / "sessions.db")
        store1 = SessionStore(db_path=db)
        store1.add_exchange("s1", "hi", "hello")

        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE sessions SET last_active = ? WHERE session_id = 's1'",
                (time.time() - SESSION_TIMEOUT - 10,),
            )

        store2 = SessionStore(db_path=db)
        assert "s1" not in store2._sessions

    def test_multiple_sessions_all_persisted(self, tmp_path):
        db = str(tmp_path / "sessions.db")
        store1 = SessionStore(db_path=db)
        store1.add_exchange("alice", "hi", "hello alice")
        store1.add_exchange("bob", "hey", "hello bob")

        store2 = SessionStore(db_path=db)
        assert store2.get_history("alice")[0]["content"] == "hi"
        assert store2.get_history("bob")[0]["content"] == "hey"

    def test_db_file_created_in_subdirectory(self, tmp_path):
        db = str(tmp_path / "subdir" / "sessions.db")
        store = SessionStore(db_path=db)
        store.add_exchange("s1", "hi", "hello")
        import os
        assert os.path.exists(db)

    def test_no_db_path_uses_in_memory_only(self):
        store = SessionStore(db_path=None)
        store.add_exchange("s1", "hi", "hello")
        assert store._db_path is None


class TestLatestImage:
    def setup_method(self):
        self.store = SessionStore()

    def test_get_returns_empty_for_unknown_session(self):
        assert self.store.get_and_clear_latest_image("nope") == ""

    def test_set_then_get_returns_url(self):
        self.store.get_history("s1")  # create session
        self.store.set_latest_image("s1", "https://example.com/img.jpg")
        url = self.store.get_and_clear_latest_image("s1")
        assert url == "https://example.com/img.jpg"

    def test_get_clears_after_first_call(self):
        self.store.get_history("s1")
        self.store.set_latest_image("s1", "https://example.com/img.jpg")
        self.store.get_and_clear_latest_image("s1")
        assert self.store.get_and_clear_latest_image("s1") == ""

    def test_set_on_nonexistent_session_is_safe(self):
        self.store.set_latest_image("ghost", "https://x.com/img.jpg")  # no session created

    def test_default_latest_image_url_is_empty(self):
        self.store.get_history("s1")
        assert self.store.get_and_clear_latest_image("s1") == ""

    def test_set_latest_image_adds_to_shown_urls(self):
        self.store.get_history("s1")
        self.store.set_latest_image("s1", "https://example.com/a.jpg")
        assert "https://example.com/a.jpg" in self.store.get_shown_image_urls("s1")

    def test_shown_urls_accumulate_across_multiple_images(self):
        self.store.get_history("s1")
        self.store.set_latest_image("s1", "https://example.com/a.jpg")
        self.store.set_latest_image("s1", "https://example.com/b.jpg")
        shown = self.store.get_shown_image_urls("s1")
        assert "https://example.com/a.jpg" in shown
        assert "https://example.com/b.jpg" in shown

    def test_shown_urls_no_duplicates(self):
        self.store.get_history("s1")
        self.store.set_latest_image("s1", "https://example.com/a.jpg")
        self.store.set_latest_image("s1", "https://example.com/a.jpg")
        assert self.store.get_shown_image_urls("s1").count("https://example.com/a.jpg") == 1

    def test_get_shown_image_urls_returns_empty_for_unknown_session(self):
        assert self.store.get_shown_image_urls("nobody") == []

    def test_image_pending_defaults_false(self):
        self.store.get_history("s1")
        assert self.store.is_image_pending("s1") is False

    def test_set_and_read_image_pending(self):
        self.store.get_history("s1")
        self.store.set_image_pending("s1", True)
        assert self.store.is_image_pending("s1") is True

    def test_is_image_pending_unknown_session_false(self):
        assert self.store.is_image_pending("nobody") is False

    def test_reset_image_clears_url_and_pending(self):
        self.store.get_history("s1")
        self.store.set_latest_image("s1", "https://example.com/a.jpg")
        self.store.set_image_pending("s1", True)
        self.store.reset_image("s1")
        assert self.store.get_and_clear_latest_image("s1") == ""
        assert self.store.is_image_pending("s1") is False

    def test_get_shown_image_urls_returns_copy(self):
        self.store.get_history("s1")
        self.store.set_latest_image("s1", "https://example.com/a.jpg")
        shown = self.store.get_shown_image_urls("s1")
        shown.append("https://mutated.com/x.jpg")
        assert len(self.store.get_shown_image_urls("s1")) == 1  # original unchanged


class TestLatestReply:
    def setup_method(self):
        self.store = SessionStore()

    def test_get_returns_empty_for_unknown_session(self):
        assert self.store.get_and_clear_latest_reply("nope") == ""

    def test_set_then_get_returns_reply(self):
        self.store.get_history("s1")
        self.store.set_latest_reply("s1", "Hello, world!")
        assert self.store.get_and_clear_latest_reply("s1") == "Hello, world!"

    def test_get_clears_after_first_call(self):
        self.store.get_history("s1")
        self.store.set_latest_reply("s1", "Hello!")
        self.store.get_and_clear_latest_reply("s1")
        assert self.store.get_and_clear_latest_reply("s1") == ""

    def test_set_on_nonexistent_session_is_safe(self):
        self.store.set_latest_reply("ghost", "Hi!")  # no session created yet

    def test_default_latest_reply_is_empty(self):
        self.store.get_history("s1")
        assert self.store.get_and_clear_latest_reply("s1") == ""


# ---------------------------------------------------------------------------
# _load_from_db() — corrupt JSON row handling
# ---------------------------------------------------------------------------

class TestLoadFromDbCorruption:
    def test_corrupt_json_row_loads_as_empty_history(self, tmp_path):
        """A session with unparseable JSON in the DB should load with empty history."""
        db = str(tmp_path / "sessions.db")
        # Manually create the DB and insert a corrupt row
        with sqlite3.connect(db) as conn:
            conn.execute("""
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    messages TEXT NOT NULL,
                    last_active REAL NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                ("corrupt-session", "this is not valid JSON {{{", time.time()),
            )

        store = SessionStore(db_path=db)
        history = store.get_history("corrupt-session")
        assert history == []  # corrupt row silently becomes empty history

    def test_valid_and_corrupt_rows_coexist(self, tmp_path):
        """Valid sessions load correctly alongside a corrupt one."""
        db = str(tmp_path / "sessions.db")
        store1 = SessionStore(db_path=db)
        store1.add_exchange("good-session", "hello", "hi there")

        # Inject a corrupt row directly
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                ("bad-session", "}{invalid", time.time()),
            )

        store2 = SessionStore(db_path=db)
        good_history = store2.get_history("good-session")
        bad_history = store2.get_history("bad-session")

        assert len(good_history) == 2  # good session intact
        assert bad_history == []       # corrupt session silently recovers
