import os
from pathlib import Path

SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8765"))

# Faster-Whisper STT
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")  # tiny/base/small/medium
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")  # int8 is fastest on CPU

# LLM via LM Studio (OpenAI-compatible)
LM_STUDIO_BASE_URL = os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234/v1")
LM_STUDIO_MODEL    = os.getenv("LM_STUDIO_MODEL", "google/gemma-4-e4b")
LLM_MAX_TOKENS     = 700
# Maximum number of past exchanges (user+assistant pairs) to include in context.
# Keeps the prompt from growing unbounded and crowding out the response budget.
LLM_MAX_HISTORY_EXCHANGES = int(os.getenv("LLM_MAX_HISTORY", "8"))
LLM_TEMPERATURE    = 0.7
# Request timeout (seconds) for LM Studio calls. Without it the OpenAI SDK
# default (~10 min) lets a hung LM Studio stall a request — or the streaming
# producer thread — for minutes. For streaming this bounds the gap between
# tokens; for non-streaming it bounds the whole response.
LLM_TIMEOUT        = float(os.getenv("LLM_TIMEOUT", "120"))

# Child's name — set CHILD_NAME in .env to personalise the bot.
# Falls back to "Kid" (so BOT_NAME = "KidBot") when unset.
CHILD = os.getenv("CHILD_NAME", "Kid")
BOT_NAME = f"{CHILD}Bot"

# Kokoro ONNX TTS
KOKORO_MODEL_PATH = os.getenv("KOKORO_MODEL", "server/models/kokoro-v1.0.onnx")
KOKORO_VOICES_PATH = os.getenv("KOKORO_VOICES", "server/models/voices-v1.0.bin")
KOKORO_VOICE = os.getenv("KOKORO_VOICE", "bm_lewis")   # bm_lewis = GB Lewis
_raw_speed = os.getenv("KOKORO_SPEED", "1.2")
try:
    KOKORO_SPEED = float(_raw_speed)
except ValueError:
    raise ValueError(f"KOKORO_SPEED must be a number, got: {_raw_speed!r}")

TEMP_DIR = Path("server/temp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Session persistence (optional SQLite backend).
# Default lives inside server/sessions/ so it matches the Docker volume mount
# (docker-compose mounts ./server/sessions) and the .gitignore entry — the old
# server/sessions.db sat at the repo root, so it neither persisted across
# container rebuilds nor was ignored by git.
PERSIST_SESSIONS = os.getenv("PERSIST_SESSIONS", "").lower() in ("1", "true", "yes")
SESSION_DB_PATH = os.getenv("SESSION_DB_PATH", "server/sessions/sessions.db")
# How long a conversation is remembered after the last message. The Pi now uses
# a stable (hostname-based) session id, so this also governs how long memory
# survives a reboot or a gap. Default 7 days so the bot remembers across the day
# and overnight rather than forgetting after 30 minutes idle.
SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT_HOURS", "168")) * 3600

# API key authentication — set on both server and Pi; empty = disabled (dev mode)
API_KEY = os.getenv("KIDBOT_API_KEY", "")

# Logging
LOG_FILE = os.getenv("LOG_FILE", "")   # path to log file; empty = stdout only
LOG_MAX_BYTES = 10 * 1024 * 1024       # 10 MB per log file
LOG_BACKUP_COUNT = 5                    # keep 5 rotated files
