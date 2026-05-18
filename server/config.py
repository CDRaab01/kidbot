import os
from pathlib import Path

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8765

# Faster-Whisper STT
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")  # tiny/base/small/medium
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")  # int8 is fastest on CPU

# Gemma 3 4B via Ollama
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "kidbot")   # name given when we import the GGUF
LLM_MAX_TOKENS = 200
LLM_TEMPERATURE = 0.7

# Child's name
CHILD_NAME = os.getenv("CHILD_NAME", "")  # set CHILD_NAME env var or edit here

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

# Session persistence (optional SQLite backend)
PERSIST_SESSIONS = os.getenv("PERSIST_SESSIONS", "").lower() in ("1", "true", "yes")
SESSION_DB_PATH = os.getenv("SESSION_DB_PATH", "server/sessions.db")

# API key authentication — set on both server and Pi; empty = disabled (dev mode)
API_KEY = os.getenv("KIDBOT_API_KEY", "")

# Logging
LOG_FILE = os.getenv("LOG_FILE", "")   # path to log file; empty = stdout only
LOG_MAX_BYTES = 10 * 1024 * 1024       # 10 MB per log file
LOG_BACKUP_COUNT = 5                    # keep 5 rotated files
