import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_TURNS = 10       # keep last 10 back-and-forths (20 messages)
SESSION_TIMEOUT = 1800  # drop idle sessions after 30 minutes


@dataclass
class Session:
    messages: list = field(default_factory=list)
    last_active: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}

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
        # Trim to last MAX_TURNS exchanges
        if len(session.messages) > MAX_TURNS * 2:
            session.messages = session.messages[-(MAX_TURNS * 2):]

    def clear(self, session_id: str):
        self._sessions.pop(session_id, None)
        logger.info("Cleared session: %s", session_id)

    def _purge_expired(self):
        now = time.time()
        expired = [sid for sid, s in self._sessions.items()
                   if now - s.last_active > SESSION_TIMEOUT]
        for sid in expired:
            del self._sessions[sid]
            logger.info("Expired session: %s", sid)
