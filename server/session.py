import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_TURNS = 10       # keep last 10 back-and-forths (20 messages)
SESSION_TIMEOUT = 1800  # drop idle sessions after 30 minutes


@dataclass
class Session:
    messages: list = field(default_factory=list)
    last_active: float = field(default_factory=time.time)
    latest_image_url: str = ""
    latest_reply: str = ""
    shown_image_urls: list = field(default_factory=list)  # dedup across session, not persisted


class SessionStore:
    def __init__(self, db_path: str | None = None):
        self._sessions: dict[str, Session] = {}
        self._db_path = db_path
        if db_path:
            self._init_db(db_path)
            self._load_from_db()

    # ------------------------------------------------------------------
    # SQLite persistence
    # ------------------------------------------------------------------

    def _init_db(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    messages    TEXT NOT NULL,
                    last_active REAL NOT NULL
                )"""
            )

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_from_db(self) -> None:
        now = time.time()
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM sessions").fetchall()
        for row in rows:
            if now - row["last_active"] > SESSION_TIMEOUT:
                self._delete_from_db(row["session_id"])
                continue
            try:
                messages = json.loads(row["messages"])
            except (ValueError, TypeError):
                messages = []
            self._sessions[row["session_id"]] = Session(
                messages=messages,
                last_active=row["last_active"],
            )
        logger.info("Loaded %d session(s) from %s", len(self._sessions), self._db_path)

    def _persist_session(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        with self._db() as conn:
            conn.execute(
                """INSERT INTO sessions (session_id, messages, last_active)
                   VALUES (?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       messages    = excluded.messages,
                       last_active = excluded.last_active""",
                (session_id, json.dumps(session.messages), session.last_active),
            )

    def _delete_from_db(self, session_id: str) -> None:
        with self._db() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _touch(self, session_id: str) -> Session:
        """Return session, creating it if new, and update activity timestamp."""
        self._purge_expired()
        if session_id not in self._sessions:
            logger.info("New session: %s", session_id)
            self._sessions[session_id] = Session()
        session = self._sessions[session_id]
        session.last_active = time.time()
        return session

    def get_history(self, session_id: str) -> list:
        return list(self._touch(session_id).messages)

    def add_exchange(self, session_id: str, user_text: str, assistant_text: str):
        session = self._touch(session_id)
        session.messages.append({"role": "user", "content": user_text})
        session.messages.append({"role": "assistant", "content": assistant_text})
        if len(session.messages) > MAX_TURNS * 2:
            session.messages = session.messages[-(MAX_TURNS * 2):]
        if self._db_path:
            self._persist_session(session_id)

    def set_latest_image(self, session_id: str, url: str) -> None:
        s = self._sessions.get(session_id)
        if s:
            s.latest_image_url = url
            if url not in s.shown_image_urls:
                s.shown_image_urls.append(url)

    def get_shown_image_urls(self, session_id: str) -> list[str]:
        s = self._sessions.get(session_id)
        return list(s.shown_image_urls) if s else []

    def get_and_clear_latest_image(self, session_id: str) -> str:
        s = self._sessions.get(session_id)
        if not s:
            return ""
        url, s.latest_image_url = s.latest_image_url, ""
        return url

    def set_latest_reply(self, session_id: str, text: str) -> None:
        s = self._sessions.get(session_id)
        if s:
            s.latest_reply = text

    def get_and_clear_latest_reply(self, session_id: str) -> str:
        s = self._sessions.get(session_id)
        if not s:
            return ""
        text, s.latest_reply = s.latest_reply, ""
        return text

    def clear(self, session_id: str):
        self._sessions.pop(session_id, None)
        if self._db_path:
            self._delete_from_db(session_id)
        logger.info("Cleared session: %s", session_id)

    def _purge_expired(self):
        now = time.time()
        expired = [sid for sid, s in self._sessions.items()
                   if now - s.last_active > SESSION_TIMEOUT]
        for sid in expired:
            del self._sessions[sid]
            if self._db_path:
                self._delete_from_db(sid)
            logger.info("Expired session: %s", sid)
