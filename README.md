# KidBot

A voice-activated AI chatbot built for young children. Cooper presses a button on a Raspberry Pi, asks a question out loud, and gets a friendly spoken answer — plus a relevant image on the display. The Pi talks to a server running on a Windows PC that handles all the heavy AI work.

---

## How It Works

```
[Pi Zero WH]                          [Windows PC / Server]
  Button press
       │
  Records audio  ──── WAV over HTTP ──▶  Speech-to-Text  (Faster-Whisper)
                                               │
                                         Content guardrails
                                               │
                                        LLM  (Gemma 4 via LM Studio)
                                               │
                                         Image search  (OpenVerse / Wikipedia / NASA)
                                               │
                                        Text-to-Speech  (Kokoro ONNX)
                                               │
  Plays audio  ◀──── MP3 + headers ────  FastAPI server :8765
  Shows image
```

Conversation history is kept per session (up to 10 exchanges). Sessions persist across restarts via SQLite.

---

## Components

### Server (`server/`)

| File | Purpose |
|---|---|
| `main.py` | FastAPI app, endpoints, middleware, lifespan |
| `llm.py` | LM Studio client, prompt building, streaming, reasoning filter |
| `stt.py` | Faster-Whisper speech-to-text |
| `tts.py` | Kokoro ONNX text-to-speech, ffmpeg WAV→MP3 |
| `image_search.py` | 5-source parallel image search (OpenVerse, Commons, Wikipedia, NASA, iNaturalist) |
| `session.py` | In-memory + SQLite session store |
| `guardrails.py` | Input/output safety filtering and system prompt |
| `config.py` | All configuration via env vars |

### Pi Client (`pi_client/`)

| File | Purpose |
|---|---|
| `main.py` | Entry point, push-to-talk loop |
| `client.py` | HTTP client for the server |
| `audio.py` | PyAudio recording and mpg123 playback |
| `display.py` | Waveshare 2.4" ILI9341 animated display |
| `button.py` | GPIO push-to-talk button + LED |
| `volume.py` | GPIO volume rocker via amixer |
| `config.py` | Pi-side configuration |

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Readiness check |
| `/chat` | POST | Full pipeline: WAV → STT → LLM → TTS → MP3 |
| `/chat_stream` | POST | Streaming version of `/chat` |
| `/chat_text` | POST | Text input (skip STT): text → LLM → TTS → MP3 |
| `/chat_text_stream` | POST | Streaming version of `/chat_text` |
| `/speak` | POST | TTS only: text → MP3 |
| `/session/{id}` | DELETE | Clear conversation history |
| `/session/{id}/latest_image` | GET | Retrieve (and clear) latest image URL |
| `/session/{id}/latest_reply` | GET | Retrieve (and clear) latest reply text |
| `/settings/voices` | GET | List voices and current settings |
| `/settings` | POST | Update voice or speed at runtime |

Response headers on audio endpoints:
- `X-Transcription` — what the STT heard
- `X-Reply` — the bot's text reply
- `X-Image-Url` — image URL if the LLM suggested one

---

## Prerequisites

**Server machine (Windows/Linux/Mac):**
- Python 3.11+
- [LM Studio](https://lmstudio.ai/) with Gemma 4 E4B loaded and Local Server enabled
- `ffmpeg` installed (`winget install ffmpeg` on Windows)
- Kokoro model files: `kokoro-v1.0.onnx` and `voices-v1.0.bin`

**Raspberry Pi Zero WH:**
- Raspbian Lite
- `mpg123` (`sudo apt install mpg123`)
- Waveshare 2.4" ILI9341 LCD

---

## Quick Start (Server — Docker)

```powershell
# Clone and enter the repo
git clone https://github.com/CDRaab01/kidbot.git
cd kidbot

# Configure
copy .env.example .env
notepad .env       # set CHILD_NAME, LM_STUDIO_MODEL, etc.

# Put model files in place
# server/models/kokoro-v1.0.onnx
# server/models/voices-v1.0.bin

# Create runtime directories
mkdir logs
mkdir server\sessions

# Start
docker compose up -d
docker compose logs -f kidbot
```

Once you see `Application startup complete`, the server is ready at `http://localhost:8765`.

## Quick Start (Server — bare Python)

```bash
pip install -r requirements/server_requirements.txt
python -m server.main
```

## Pi Client Setup

```bash
pip install -r requirements/pi_requirements.txt
python -m pi_client.main
```

Set `SERVER_URL` in `pi_client/config.py` (or via env var) to point at the server.

---

## Configuration

All server settings are env vars. Copy `.env.example` to `.env` to configure.

| Variable | Default | Description |
|---|---|---|
| `CHILD_NAME` | `Cooper` | Child's name — used in the bot's personality |
| `LM_STUDIO_URL` | `http://127.0.0.1:1234/v1` | LM Studio server URL |
| `LM_STUDIO_MODEL` | `google/gemma-4-e4b` | Model ID as shown in LM Studio |
| `LLM_MAX_HISTORY` | `8` | Conversation turns kept in context |
| `WHISPER_MODEL` | `small` | Whisper model size (`tiny`/`base`/`small`/`medium`) |
| `WHISPER_DEVICE` | `cpu` | `cpu` or `cuda` |
| `KOKORO_MODEL` | `server/models/kokoro-v1.0.onnx` | Path to Kokoro model |
| `KOKORO_VOICES` | `server/models/voices-v1.0.bin` | Path to Kokoro voices |
| `KOKORO_VOICE` | `bm_lewis` | Voice name |
| `KOKORO_SPEED` | `1.2` | Speech rate multiplier |
| `KIDBOT_API_KEY` | _(empty)_ | Optional shared secret — set on both server and Pi |
| `PERSIST_SESSIONS` | _(empty)_ | Set to `1` to enable SQLite session persistence |
| `SESSION_DB_PATH` | `server/sessions.db` | SQLite database path |
| `LOG_FILE` | _(empty)_ | Log file path — empty logs to stdout only |

---

## Development

```bash
# Install test dependencies
pip install "fastapi>=0.111.0" "python-multipart>=0.0.9" httpx numpy requests slowapi pytest openai

# Run all tests
python -m pytest tests/ -v

# Run a specific group
python -m pytest tests/test_api.py -v
python -m pytest tests/test_image_search.py -v
```

### Makefile shortcuts

```bash
make up          # docker compose up -d
make down        # docker compose down
make build       # docker compose build --no-cache
make logs        # tail container logs
make restart     # restart container
make shell       # bash inside container
make test        # run pytest locally
make send TEXT="hello"          # send a text message to the running server
make test-images                # run all 10 image relevance test cases
make test-images ARGS="Spiderman"  # test one topic
```

### CLI tools

```bash
# Send text and print the reply (no audio hardware needed)
python scripts/send_text.py "what is the biggest animal?"
python scripts/send_text.py              # interactive mode

# Test image relevance (requires running server, optional vision model)
python scripts/test_images.py            # all 10 built-in topics
python scripts/test_images.py "Batman"   # single topic
python scripts/test_images.py --open     # open each image in browser
python scripts/test_images.py --no-vision  # just check images are returned
```

---

## CI / CD Pipeline

Push to `main` triggers the full pipeline:

```
test-server ──┐
test-image  ──┼──▶ deploy ──▶ smoke-test
test-pi     ──┘
```

- **test-server** — API, LLM, session, STT, TTS, guardrails (160 tests)
- **test-image-search** — image search and priority/fallback logic (46 tests)
- **test-pi-client** — display, volume, GPIO (37 tests)
- **deploy** — SSH into server: `git pull && docker compose up -d --build`
- **smoke-test** — runs all 10 image test cases inside the container; fails if any topic returns no image

### Required GitHub Secrets

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | Server IP or hostname |
| `DEPLOY_USER` | SSH username |
| `DEPLOY_KEY` | SSH private key content |
| `DEPLOY_PORT` | SSH port (optional, defaults to 22) |
