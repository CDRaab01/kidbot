# KidBot — Claude Code Guide

## Project in one sentence
KidBot is a FastAPI voice chatbot for a young child: a Raspberry Pi Zero WH captures audio, sends it to a Windows PC server, which runs Whisper (STT) + Gemma 4 via LM Studio (LLM) + Kokoro (TTS) and streams back MP3 audio with an image URL.

## Repo layout

```
server/         FastAPI server — all production Python
pi_client/      Raspberry Pi client — runs on the Pi only
tests/          Pytest suite — 243 tests, 10 skipped (GUI/display need $DISPLAY)
scripts/        CLI tools (not part of the server, not tested in CI)
requirements/   server_requirements.txt  |  pi_requirements.txt
docs/           System and software manuals
.github/workflows/  tests.yml (on PR)  |  deploy.yml (on push to main)
```

## Key files to know

| File | What it does |
|---|---|
| `server/main.py` | All endpoints, middleware, lifespan, `_extract_image()`, `_safe_header()`, `_run_llm_pipeline()` |
| `server/config.py` | Every env var with its default. Edit this first when adding config. |
| `server/llm.py` | `LLMInterface` — wraps LM Studio via OpenAI SDK, strips Gemma 4 chain-of-thought, yields sentences |
| `server/tts.py` | `TextToSpeech` — Kokoro ONNX → WAV → ffmpeg → MP3 |
| `server/stt.py` | `SpeechToText` — Faster-Whisper |
| `server/image_search.py` | 5-source parallel fetch (OpenVerse → Commons → Wikipedia → NASA → iNaturalist) |
| `server/session.py` | `SessionStore` — in-memory dict + optional SQLite, per-session history + latest image/reply |
| `server/guardrails.py` | `get_system_prompt()`, `is_input_safe()`, `is_output_safe()` |
| `tests/conftest.py` | Stubs for all non-stdlib packages (RPi.GPIO, PIL, tkinter, kokoro_onnx, etc.) — loaded automatically by pytest |

## Running tests

```bash
# Install once
pip install "fastapi>=0.111.0" "python-multipart>=0.0.9" httpx numpy requests slowapi pytest openai

# All tests
python -m pytest tests/ -v

# By area (mirrors CI jobs)
python -m pytest tests/test_api.py tests/test_guardrails.py tests/test_llm.py \
    tests/test_main_helpers.py tests/test_session.py tests/test_stt.py tests/test_tts.py
python -m pytest tests/test_image_search.py
python -m pytest tests/test_display_volume.py tests/test_gui_logic.py tests/test_volume.py
```

Never run the full `requirements/server_requirements.txt` in CI — it installs kokoro-onnx, faster-whisper, etc. which are stubbed out by conftest.py and would be wasted.

## CI pipeline

```
tests.yml  (on PR to main):
  test-server | test-image-search | test-pi-client   ← parallel

deploy.yml  (on push to main):
  test-server | test-image-search | test-pi-client → deploy → smoke-test
```

The `smoke-test` job runs `docker compose exec -T kidbot python scripts/test_images.py --no-vision` on the live server after deploy. It fails if any of the 10 image topics returns no URL.

## Adding a new API endpoint

1. Add the route to `server/main.py`
2. Add tests to `tests/test_api.py` — follow the existing `TestChat` / `TestChatText` pattern
3. The test fixture in `test_api.py` mocks `_stt`, `_llm`, `_tts` as MagicMocks via `server.main`

## Adding a new config value

1. Add `FOO = os.getenv("FOO", "default")` to `server/config.py`
2. Import it where needed
3. Add to `.env.example` with a comment
4. Document in README.md config table

## Image search pipeline

LLM response → `_extract_image(text)` → strips `[IMAGE: term]` tag → `fetch_image_url(term)` → 5 parallel HTTP calls → highest-priority non-None result → `X-Image-Url` response header (sanitized via `_safe_header()`).

Streaming responses store the image via `_fetch_and_store_image()` (asyncio background task) → Pi polls `/session/{id}/latest_image`.

## Session flow

- Session ID is a string passed by the client (Pi sends its hostname, CLI sends `cli-test`)
- `SessionStore.add_exchange(session_id, user_text, reply_text)` appends to history and trims to `LLM_MAX_HISTORY_EXCHANGES` (default 8) pairs
- Sessions expire after 30 min of inactivity and are purged on next access
- `PERSIST_SESSIONS=1` saves to SQLite; on restart, history is restored for sessions not yet expired

## Important constraints

- **LM Studio must be running** on the host before starting the server — the server does NOT start LM Studio and will log a warning if the model is unreachable but won't crash
- **Model files are never committed** — `.gitignore` excludes `server/models/*.onnx`, `*.bin`, `*.gguf`
- **`host.docker.internal`** resolves automatically on Docker Desktop (Windows/Mac). The `extra_hosts` line in `docker-compose.yml` is commented out — uncomment only for bare Linux
- **ffmpeg must be installed system-wide** on the server machine (in the Docker image it's installed via apt; bare Python installs need it separately)
- **Whisper downloads ~250 MB** on first run into `~/.cache/huggingface/` — in Docker this is persisted in the `whisper-cache` named volume

## Common gotchas

- `_safe_header()` must be applied to anything going into response headers (including image URLs) — prevents HTTP response splitting
- `asyncio.create_task()` not `ensure_future()` — exceptions from background tasks must be caught inside the task function
- Tests that call `fetch_image_url()` must mock ALL five sources (`_search_openverse`, `_search_commons`, `_search_wikipedia`, `_search_nasa`, `_search_inaturalist`) — leaving any unmocked causes real network calls in CI
- Pi client tests need the full `TEST_DEPS` install (not just `pytest numpy`) — the conftest stubs platform packages but some test files import `server.config` and `numpy` at module level

## Deployment

Branch `claude/review-improvements-LijA9` is the active development branch. Merge to `main` triggers the deploy pipeline.

Server lives on a Windows machine. Docker Desktop runs the container. LM Studio runs natively on Windows and is reachable from the container at `http://host.docker.internal:1234`.

```
# Manual deploy (from the server)
cd kidbot
git pull origin main
docker compose up -d --build --remove-orphans
```
