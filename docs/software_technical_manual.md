# KidBot — Software Technical Manual

**Version:** 0.5  
**Audience:** Developers maintaining or extending the KidBot codebase

---

## Table of Contents

1. [Repository Layout](#1-repository-layout)
2. [Software Architecture Overview](#2-software-architecture-overview)
3. [Server Software](#3-server-software)
   - 3.1 [FastAPI Application](#31-fastapi-application)
   - 3.2 [Speech-to-Text (STT)](#32-speech-to-text-stt)
   - 3.3 [Large Language Model (LLM)](#33-large-language-model-llm)
   - 3.4 [Text-to-Speech (TTS)](#34-text-to-speech-tts)
   - 3.5 [Guardrails](#35-guardrails)
   - 3.6 [Session Store](#36-session-store)
   - 3.7 [Image Search](#37-image-search)
   - 3.8 [Configuration](#38-server-configuration)
4. [Pi Client Software](#4-pi-client-software)
   - 4.1 [Entry Point & State Machine](#41-entry-point--state-machine)
   - 4.2 [Server Client](#42-server-client)
   - 4.3 [Audio Manager](#43-audio-manager)
   - 4.4 [Button Handler](#44-button-handler)
   - 4.5 [Volume Rocker](#45-volume-rocker)
   - 4.6 [Display Manager](#46-display-manager)
   - 4.7 [Configuration](#47-pi-client-configuration)
5. [Request & Data Flows](#5-request--data-flows)
   - 5.1 [Non-Streaming Voice Pipeline](#51-non-streaming-voice-pipeline)
   - 5.2 [Streaming Voice Pipeline](#52-streaming-voice-pipeline)
   - 5.3 [Image Tag Flow](#53-image-tag-flow)
   - 5.4 [Session Lifecycle](#54-session-lifecycle)
6. [API Reference](#6-api-reference)
7. [Content Safety](#7-content-safety)
8. [Test GUI](#8-test-gui)
9. [Test Suite](#9-test-suite)
10. [Dependencies & Requirements](#10-dependencies--requirements)
11. [Environment Variables](#11-environment-variables)

---

## 1. Repository Layout

```
kidbot/
├── server/                     # FastAPI server (PC / server machine)
│   ├── __init__.py
│   ├── main.py                 # Application, endpoints, streaming pipeline
│   ├── config.py               # All server config / env vars
│   ├── stt.py                  # Speech-to-Text (Faster-Whisper)
│   ├── llm.py                  # LLM interface (LM Studio via OpenAI SDK)
│   ├── tts.py                  # Text-to-Speech (Kokoro ONNX)
│   ├── guardrails.py           # Content safety + system prompt
│   ├── session.py              # Conversation history + SQLite persistence
│   ├── image_search.py         # 5-source parallel image search
│   └── models/                 # Model files (not in git)
│       ├── kokoro-v1.0.onnx
│       └── voices-v1.0.bin
│
├── pi_client/                  # Raspberry Pi client
│   ├── __init__.py
│   ├── __main__.py             # Enables python3 -m pi_client
│   ├── main.py                 # Entry point, button callbacks, state machine
│   ├── client.py               # HTTP client (requests + retry)
│   ├── audio.py                # PyAudio recording, mpg123 playback, chimes, volume blip
│   ├── button.py               # GPIO push-to-talk + LED
│   ├── volume.py               # GPIO volume rocker + ALSA control (use_gpio=False for keyboard mode)
│   ├── display.py              # ILI9341 LCD face animation
│   └── config.py               # Pi-side config / env vars
│
├── pi_setup/                   # Pi configuration and service files
│   ├── kidbot.service          # systemd unit file — copy to /etc/systemd/system/
│   └── setup_2w.sh             # Full automated setup script for Pi Zero 2W
│
├── scripts/                    # CLI tools and test harnesses
│   ├── keyboard_test.py        # Keyboard-driven Pi client (no physical buttons needed)
│   ├── send_text.py            # Send text to server and print reply
│   └── test_images.py         # Image search relevance tester
│
├── test_gui.py                 # Desktop test console (tkinter)
│
├── requirements/
│   ├── server_requirements.txt
│   └── pi_requirements.txt
│
├── tests/                      # pytest suite
│   ├── conftest.py             # Module stubs (openai, whisper, kokoro, tkinter)
│   ├── test_api.py
│   ├── test_guardrails.py
│   ├── test_llm.py
│   ├── test_session.py
│   ├── test_stt.py
│   ├── test_tts.py
│   ├── test_image_search.py
│   ├── test_volume.py
│   ├── test_display_volume.py
│   └── test_gui_logic.py
│
└── .github/workflows/
    ├── tests.yml               # CI — runs tests on PR to main
    └── deploy.yml              # CI/CD — parallel tests → deploy → smoke-test
```

---

## 2. Software Architecture Overview

```
╔══════════════════════════════════════════════════════════════════════╗
║                       KidBot Software Stack                          ║
╠═══════════════════════════╦══════════════════════════════════════════╣
║    PI CLIENT              ║    SERVER (PC / LAN host)                ║
║                           ║                                          ║
║  ┌─────────────────────┐  ║  ┌────────────────────────────────────┐  ║
║  │    pi_client/main   │  ║  │        FastAPI  (port 8765)        │  ║
║  │  Button callbacks   │  ║  │                                    │  ║
║  │  Conversation loop  │  ║  │  /chat_stream  /chat_text_stream   │  ║
║  └────────┬────────────┘  ║  │  /chat         /chat_text          │  ║
║           │               ║  │  /speak        /health             │  ║
║  ┌────────▼────────────┐  ║  │  /session/{id}/latest_image        │  ║
║  │   ServerClient      │◄─╬──►  /settings/voices  /settings       │  ║
║  │   HTTP + retry      │  ║  └────┬──────┬──────┬──────┬──────────┘  ║
║  └─────────────────────┘  ║       │      │      │      │             ║
║                           ║  ┌────▼──┐ ┌─▼───┐ ┌▼───┐ ┌▼─────────┐ ║
║  ┌─────────────────────┐  ║  │  STT  │ │ LLM │ │TTS │ │ Session  │ ║
║  │   AudioManager      │  ║  │Whisper│ │  LM │ │Kokoro│ │  Store  │ ║
║  │   PyAudio + mpg123  │  ║  └───────┘ └─────┘ └────┘ └──────────┘ ║
║  └─────────────────────┘  ║                 │                        ║
║                           ║  ┌──────────────▼──────────────────────┐ ║
║  ┌─────────────────────┐  ║  │           Guardrails                │ ║
║  │  DisplayManager     │  ║  │   Input filter → LLM → Output filter│ ║
║  │  PIL + luma.lcd     │  ║  └─────────────────────────────────────┘ ║
║  └─────────────────────┘  ║                                          ║
║                           ║  ┌─────────────────────────────────────┐ ║
║  ┌─────────────────────┐  ║  │         image_search.py             │ ║
║  │  PushToTalkButton   │  ║  │  5-source parallel image search     │ ║
║  │  RPi.GPIO           │  ║  └─────────────────────────────────────┘ ║
║  └─────────────────────┘  ║                                          ║
║                           ║                                          ║
║  ┌─────────────────────┐  ║                                          ║
║  │  VolumeRocker       │  ║                                          ║
║  │  RPi.GPIO + amixer  │  ║                                          ║
║  └─────────────────────┘  ║                                          ║
╚═══════════════════════════╩══════════════════════════════════════════╝

     Test Console (desktop, no Pi hardware)
  ┌──────────────────────────────────────┐
  │           test_gui.py                │
  │  FacePanel  │  YourChildBotGUI          │
  │  (emulated  │  (mic, chat, send)     │
  │   LCD face) │                        │
  └──────────────────────────────────────┘
```

---

## 3. Server Software

### 3.1 FastAPI Application

**File:** `server/main.py`

The server is a FastAPI application that loads all ML models once at startup via a `lifespan` context manager and holds them as module-level globals.

#### Model Initialisation

```
Server startup
     │
     ▼
lifespan()
     ├── _stt = SpeechToText()     ← loads Whisper model
     ├── _llm = LLMInterface()     ← validates LM Studio connection
     └── _tts = TextToSpeech()     ← loads Kokoro ONNX model
```

#### Middleware Stack

```
Incoming request
       │
       ▼
┌──────────────────────┐
│  _api_key_middleware │  Rejects if X-API-Key header missing/wrong
│  (skips /health)     │  Returns 401 on failure
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  slowapi rate limiter│  5/min (chat), 20/min (speak)
│  (per remote address)│  Returns 429 on excess
└──────────┬───────────┘
           │
           ▼
       endpoint
```

#### Shared Pipeline Helpers

| Function | Purpose |
|---|---|
| `_extract_image(text)` | Strips `[IMAGE: term]` tag, returns `(clean_text, term\|None)` |
| `_safe_header(text)` | Encodes text as ASCII, removes newlines — for HTTP headers |
| `_mp3_response(reply, transcription, image_url)` | Calls TTS, builds `Response` with metadata headers |
| `_run_llm_pipeline(text, session_id)` | Orchestrates LLM call + image fetch, returns `(reply, image_url)` |
| `_fetch_and_store_image(session_id, term)` | Async background task: fetches image URL and writes to session |

#### Streaming Pipeline

```
POST /chat_stream  (or /chat_text_stream)
         │
         ├── STT transcription (for /chat_stream only)
         │
         ▼
_sentence_stream(text, session_id)              ← async generator
         │
         ├── Creates asyncio.Queue
         │
         ├── Spawns producer thread ──────────────────────────────────►
         │                                  llm.respond_stream(text)
         │                                  yields sentences one-by-one
         │                                  loop.call_soon_threadsafe(queue.put)
         │◄────────────────────────────────────────────────────────────
         │
         │   ┌─── queue.get() ──► "s" (sentence)
         │   │                         │
         │   │                 extract [IMAGE:] tag
         │   │                         │
         │   │                 run_in_threadpool(tts.synthesize)
         │   │                         │
         │   └──────────────── yield mp3_chunk ──► StreamingResponse
         │
         │   (on "done" or "err"):
         │       t.join()
         │       _sessions.add_exchange(...)
         │       asyncio.create_task(_fetch_and_store_image(...))
         │
         ▼
  StreamingResponse(media_type="audio/mpeg")
```

---

### 3.2 Speech-to-Text (STT)

**File:** `server/stt.py`  
**Library:** `faster-whisper`

```
transcribe(audio_path)
      │
      ├── model.transcribe(
      │       audio_path,
      │       beam_size=1,          ← fastest (greedy-ish)
      │       language="en",
      │       vad_filter=True,      ← skip silence segments
      │       vad_parameters={"threshold": 0.2}  ← catch quiet speech
      │   )
      │
      └── join all segments → strip → return text
```

**Model:** `small` (en) on CPU with `int8` compute. Balance of speed vs accuracy for a child's voice. Configurable via `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`.

---

### 3.3 Large Language Model (LLM)

**File:** `server/llm.py`  
**Runtime:** LM Studio (OpenAI-compatible API on port 1234)

#### Class: `LLMInterface`

```
respond(user_text, history)
      │
      ├── is_input_safe(user_text)  ──► FAIL → return REDIRECT_RESPONSE
      │
      ├── _build_messages():
      │       [system_prompt] + history + [{"role":"user","content":user_text}]
      │
      ├── openai_client.chat.completions.create(model, messages, temperature, max_tokens)
      │
      ├── extract content string
      │
      ├── is_output_safe(reply)  ──► FAIL → return OUTPUT_BLOCKED_RESPONSE
      │
      └── return reply
```

#### respond_stream() — sentence-level generator

```
respond_stream(user_text, history)
      │
      ├── is_input_safe()  ──► FAIL → yield REDIRECT_RESPONSE; return
      │
      ├── openai_client.chat.completions.create(..., stream=True)
      │
      │   for chunk in stream:
      │       buffer += chunk.choices[0].delta.content or ""
      │
      │       while _SENT_BOUNDARY matches buffer:
      │           sentence = buffer[:match.start()+1]
      │           buffer   = buffer[match.end():]
      │
      │           if len(sentence) < 8:         ← merge short fragments
      │               buffer = sentence + " " + buffer; continue
      │
      │           if not is_output_safe(sentence):
      │               yield OUTPUT_BLOCKED_RESPONSE; return
      │
      │           yield sentence
      │
      └── flush remainder (final fragment without trailing punctuation)
```

**Sentence boundary regex:** `(?<=[.!?])\s+`  
**Min sentence length:** 8 characters  
**Config:** `LM_STUDIO_MODEL`, `LLM_TEMPERATURE` (0.7), `LLM_MAX_TOKENS` (700)

---

### 3.4 Text-to-Speech (TTS)

**File:** `server/tts.py`  
**Library:** `kokoro-onnx`

```
synthesize(text)
      │
      ├── clean_for_speech(text):
      │       strip emoji, curly quotes, markdown (* # _ ``)
      │       em/en dashes → ", "
      │       parenthetical asides → ""
      │       collapse whitespace
      │
      ├── Kokoro.create(text, voice, speed, lang="en-gb")
      │       → samples (float32 numpy array at 24 kHz)
      │
      ├── soundfile.write(tmp_wav, samples, 24000)
      │
      ├── ffmpeg -i tmp_wav -codec:a libmp3lame -q:a 4 tmp_mp3
      │
      └── return mp3_bytes
```

**Voice:** `bm_lewis` (British male) — configurable via `KOKORO_VOICE`  
**Speed:** 1.2× — configurable via `KOKORO_SPEED`  
**Runtime voices:** `available_voices()`, `set_voice()`, `set_speed()`

---

### 3.5 Guardrails

**File:** `server/guardrails.py`

#### System Prompt

```
get_system_prompt()
      │
      ├── _time_context()  →  "It is currently morning. ..."
      │                       (based on datetime.now().hour)
      │
      └── f"{time_context}\n\n{_BASE_PROMPT}"
```

`_BASE_PROMPT` (77 lines) covers:
- Tone and language rules (smart 7–10 year old, natural speech, no lists/bullets)
- Safety absolutes (violence, adult content, personal info)
- YourChild's favourite topics (engineering, space, Spider-Man, science)
- Special modes: **Story**, **Quiz**, **Joke/Riddle**, **Song/Poem**, **Math**
- Image tagging rules (explicit request only)

#### Content Filtering

```
is_input_safe(text)
      │
      ├── text.lower() contains any BLOCKED_INPUT_KEYWORDS?
      │   (kill, murder, sex, drugs, bomb, weapon, hate, ...)
      │
      └── False → caller returns REDIRECT_RESPONSE (never reaches LLM)

is_output_safe(text)
      │
      ├── text.lower() contains any BLOCKED_OUTPUT_KEYWORDS?
      │
      ├── _PERSONAL_INFO_RE matches? (share your address, what's your phone, ...)
      │
      └── len(text) > 900?
              → (False, reason)
              ← (True, "") if all pass
```

---

### 3.6 Session Store

**File:** `server/session.py`

```
┌─────────────────────────────────────────────────────────┐
│                    SessionStore                          │
│                                                          │
│  _sessions: dict[str, Session]                          │
│                                                          │
│  Session dataclass:                                      │
│    messages:          list[{role, content}]              │
│    last_active:       float (unix timestamp)             │
│    latest_image_url:  str   (one-shot, cleared on read)  │
│    latest_reply:      str   (one-shot, cleared on read)  │
└─────────────────────────────────────────────────────────┘

get_history(session_id)
      ├── _purge_expired()   ← removes sessions idle > 30 min
      ├── create Session if new
      └── return copy of messages[]

add_exchange(session_id, user_text, assistant_text)
      ├── append {role:user} and {role:assistant}
      ├── trim to last MAX_TURNS * 2 = 20 messages
      └── persist to SQLite if db_path configured

SQLite schema:
  CREATE TABLE sessions (
      session_id  TEXT PRIMARY KEY,
      messages    TEXT NOT NULL,   -- JSON array
      last_active REAL NOT NULL
  )
```

`latest_image_url` and `latest_reply` are **not** persisted to SQLite — they are transient, cleared once polled.

---

### 3.7 Image Search

**File:** `server/image_search.py`

```
fetch_image_url(term, size=500)
      │
      ├── Run 5 searches in parallel (asyncio.gather):
      │     _search_openverse(term)    → openverse.org  (CC-licensed)
      │     _search_commons(term)      → commons.wikimedia.org
      │     _search_wikipedia(term)    → en.wikipedia.org/w/api.php
      │     _search_nasa(term)         → images-api.nasa.gov
      │     _search_inaturalist(term)  → api.inaturalist.org
      │
      ├── Each returns a URL or None
      │
      └── Return highest-priority non-None result
            (OpenVerse > Commons > Wikipedia > NASA > iNaturalist)
```

Five sources are searched in parallel and the highest-priority result is returned. All sources require no API key and provide freely licensed images suitable for a child audience. The priority order favours general encyclopaedic coverage (OpenVerse/Commons/Wikipedia) over specialist sources (NASA, iNaturalist).

---

### 3.8 Server Configuration

**File:** `server/config.py`

| Variable | Default | Env Override |
|---|---|---|
| `SERVER_HOST` | `0.0.0.0` | `SERVER_HOST` |
| `SERVER_PORT` | `8765` | `SERVER_PORT` |
| `WHISPER_MODEL` | `small` | `WHISPER_MODEL` |
| `WHISPER_DEVICE` | `cpu` | `WHISPER_DEVICE` |
| `WHISPER_COMPUTE_TYPE` | `int8` | `WHISPER_COMPUTE_TYPE` |
| `LM_STUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` | `LM_STUDIO_URL` |
| `LM_STUDIO_MODEL` | `google/gemma-4-e4b` | `LM_STUDIO_MODEL` |
| `LLM_TEMPERATURE` | `0.7` | — |
| `LLM_MAX_TOKENS` | `700` | — |
| `LLM_MAX_HISTORY_EXCHANGES` | `8` | `LLM_MAX_HISTORY` |
| `KOKORO_VOICE` | `bm_lewis` | `KOKORO_VOICE` |
| `KOKORO_SPEED` | `1.2` | `KOKORO_SPEED` |
| `PERSIST_SESSIONS` | `False` | `PERSIST_SESSIONS=1` |
| `SESSION_DB_PATH` | `server/sessions.db` | `SESSION_DB_PATH` |
| `API_KEY` | `""` (disabled) | `KIDBOT_API_KEY` |
| `LOG_FILE` | `""` (stdout) | `LOG_FILE` |
| `LOG_MAX_BYTES` | `10 MB` | `LOG_MAX_BYTES` |
| `LOG_BACKUP_COUNT` | `5` | `LOG_BACKUP_COUNT` |

---

## 4. Pi Client Software

### 4.1 Entry Point & State Machine

**File:** `pi_client/main.py`

```
main()
  │
  ├── configure logging
  ├── button = PushToTalkButton()
  ├── audio  = AudioManager()
  ├── client = ServerClient()
  ├── display = DisplayManager()
  ├── volume_rocker = VolumeRocker(on_change=_on_volume_change)
  │       _on_volume_change(pct):
  │           display.show_volume(pct)   ← cyan bar overlay on LCD
  │           audio.play_volume_blip(pct) ← pitch-scaled confirmation tone
  │
  ├── ping server
  │     ├── success → prefetch_audio() + display.set_state("IDLE")
  │     └── fail    → log warning, continue (retry on next press)
  │
  ├── button.on_press(on_press)
  ├── button.on_release(on_release)
  ├── button.blink(3)           ← 3 slow blinks = "ready"
  │
  └── while True: time.sleep(0.1)   ← main loop (event-driven)


Button State Machine:
─────────────────────────────────────────────────────────────────
      ┌──────────────┐
      │     IDLE     │◄──────────────────────────────────────────┐
      └──────┬───────┘                                           │
    [press]  │                                               [done/err]
             ▼                                                   │
      ┌──────────────┐   LED on    ┌───────────────────────────┐ │
      │  LISTENING   │────────────►│  audio.start_recording()  │ │
      └──────┬───────┘             └───────────────────────────┘ │
    [release]│                                                    │
             ▼                                                    │
      ┌──────────────┐             ┌───────────────────────────┐ │
      │  THINKING    │────────────►│  audio.stop_recording()   │ │
      └──────┬───────┘             │  client.send_audio_stream │ │
             │                     └───────────────────────────┘ │
             ▼                                                    │
      ┌──────────────┐             ┌───────────────────────────┐ │
      │  SPEAKING    │────────────►│  audio.play_mp3_stream()  │ │
      └──────┬───────┘             └───────────────────────────┘ │
             │                                                    │
             ├── image_url found? ─►  DISPLAY IMAGE (8 s) ───────┘
             │
             └── no image? ──────►  HAPPY (1.5 s) ──────────────┘

[any failure] ──────────────────►  ERROR (2 s) ─────────────────┘
─────────────────────────────────────────────────────────────────
```

`_busy_lock` (non-blocking acquire) prevents a second press from starting while a session is in progress.

---

### 4.2 Server Client

**File:** `pi_client/client.py`

```
ServerClient
  │
  ├── session_id: UUID4 (fixed per process lifetime)
  │
  ├── ping()
  │     └── GET /health  (timeout 5 s)
  │
  ├── prefetch_audio()
  │     ├── POST /speak  "I can't reach my brain..."  → offline_audio
  │     └── POST /speak  "Something went wrong..."    → error_audio
  │
  ├── send_audio_stream(wav_path)
  │     └── POST /chat_stream
  │             files={"audio": wav}
  │             data={"session_id": uuid}
  │             stream=True
  │           → iter_content(4096) or None on failure
  │
  ├── get_latest_image()
  │     └── GET /session/{session_id}/latest_image
  │           → image_url string or None
  │
  └── _post_with_retry(url, ...)
        ├── attempt 0 immediately
        ├── attempt 1 after 1 s (ConnectionError only)
        └── attempt 2 after 2 s
              → None on all failures (Timeout returns None immediately)
```

The `_headers` property automatically adds `X-API-Key` if `API_KEY` is configured.

---

### 4.3 Audio Manager

**File:** `pi_client/audio.py`

```
Initialisation:
─────────────────────────────────────────────────────────────────
__init__()
  │
  ├── _find_mic() → search PyAudio devices for MIC_DEVICE_HINT ("aic3104")
  │       → stores _device_index (int) or None (fallback to default)
  │
  ├── if _device_index is not None:
  │       open PyAudio InputStream immediately (16 kHz, int16, mono)
  │       spawn _idle_loop() daemon thread  ← drains ADC buffer continuously
  │       (AIC3104 sigma-delta HPF takes ~2 s to settle — pre-opening means
  │        first button press records clean audio immediately)
  │
  └── _playback_proc = None  (tracked for stop_playback)

Recording path:
─────────────────────────────────────────────────────────────────
start_recording()
  │
  ├── _frames = []
  ├── if _stream is None (fallback mic / device_index=None):
  │       open PyAudio InputStream
  │       spawn _idle_loop() daemon thread
  └── _recording = True  (_idle_loop starts appending frames)

_idle_loop() — daemon thread
  │
  └── while _stream is not None:
        data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        if _recording: _frames.append(data)
        enforce MAX_RECORD_SECONDS cap

stop_recording() → wav_path
  │
  ├── _recording = False
  ├── if _device_index is None (fallback mic):
  │       close and discard stream  ← frees ALSA device for aplay/mpg123
  │       (ReSpeaker/AIC3104 keeps stream open for ADC warmup)
  ├── write _frames to temp .wav file
  └── return path

Playback path:
─────────────────────────────────────────────────────────────────
play_mp3_stream(chunk_iter)
  │
  ├── Popen(["mpg123", "-q", "-"])   ← reads from stdin
  ├── _playback_proc = proc          ← tracked for stop_playback()
  ├── for chunk in chunk_iter:
  │       if proc.poll() is not None: break  ← killed externally
  │       proc.stdin.write(chunk)
  └── proc.stdin.close(); proc.wait()

play_mp3(mp3_bytes)
  │
  ├── write to temp file
  ├── _playback_proc = proc
  └── Popen(["mpg123", "-q", tmp_path])

stop_playback()
  │
  ├── atomically take _playback_proc (set to None)
  └── kill proc if still running  ← used by shutdown handler / quit key

Volume blip and chimes:
─────────────────────────────────────────────────────────────────
play_volume_blip(pct)
  │
  ├── re-assert PCM level via amixer (mpg123 can reset it on exit)
  ├── generate 80 ms sine burst in memory
  │       freq = 300 Hz × 4^(pct/100)  ← log scale: 300 Hz (0%) → 1200 Hz (100%)
  │       8 ms attack / 25 ms release envelope
  ├── wrap PCM samples in WAV header (io.BytesIO + wave module)
  ├── write to temp .wav file
  └── play via paplay (PulseAudio API)
      NOTE: PipeWire holds hw:1,0 after first client connects.
            Direct aplay/plughw fails with "device busy".
            paplay routes through PipeWire's PulseAudio compatibility layer.

_chime_volume() — context manager
  │
  ├── save current PCM %
  ├── set PCM to STARTUP_VOLUME (default 45%)
  ├── yield  ← sound plays here
  └── restore previous PCM %

play_startup_sound() / play_shutdown_sound()
  │
  ├── generate 8-bit chime WAV once (stored in pi_client/startup.wav / shutdown.wav)
  └── with _chime_volume(): aplay -D plughw:1,0 <wav>
      NOTE: startup/shutdown sounds play before/after PipeWire has a client,
            so direct aplay succeeds. Volume is capped at STARTUP_VOLUME.
─────────────────────────────────────────────────────────────────
```

---

### 4.4 Button Handler

**File:** `pi_client/button.py`

```
GPIO layout (BCM numbering):
  Pin 17  ──── BUTTON (IN, pull-up, active-low)
  Pin 27  ──── LED    (OUT, active-high)

PushToTalkButton
  │
  ├── GPIO.setup(BUTTON_PIN, IN, pull_up_down=PUD_UP)
  ├── GPIO.setup(LED_PIN, OUT)
  │
  ├── on_press(cb)
  │     └── GPIO.add_event_detect(FALLING, bouncetime=50)
  │             → daemon thread: cb()
  │
  ├── on_release(cb)
  │     └── GPIO.add_event_detect(RISING, bouncetime=50)
  │             → daemon thread: cb()
  │
  ├── led(state)  → GPIO.output(LED_PIN, HIGH|LOW)
  │
  ├── blink(count, interval)
  │     └── loop: led(on) → sleep → led(off) → sleep
  │
  └── cleanup() → GPIO.remove_event_detect(BUTTON_PIN)
                  (global GPIO.cleanup() is called by main.shutdown())
```

---

### 4.5 Volume Rocker

**File:** `pi_client/volume.py`

```
GPIO layout (BCM numbering):
  Pin 5  ──── VOL_UP   (IN, pull-up, active-low)
  Pin 6  ──── VOL_DOWN (IN, pull-up, active-low)

VolumeRocker(on_change=None, use_gpio=True)
  │
  ├── if use_gpio=True (default — Pi hardware):
  │     GPIO.setup(VOL_UP_PIN/VOL_DOWN_PIN, IN, pull_up_down=PUD_UP)
  │     GPIO.add_event_detect(FALLING, bouncetime=150)
  │     _on_up(channel)   → daemon thread: _adjust(+VOL_STEP)
  │     _on_down(channel) → daemon thread: _adjust(-VOL_STEP)
  │
  ├── if use_gpio=False (keyboard test mode — no GPIO access):
  │     step_up()   → daemon thread: _adjust(+VOL_STEP)
  │     step_down() → daemon thread: _adjust(-VOL_STEP)
  │
  ├── _adjust(delta)
  │     ├── _get_volume(ALSA_CONTROL)  → amixer sget → regex [(\d+)%]
  │     ├── new_pct = clamp(current + delta, VOL_MIN, VOL_MAX)
  │     ├── if new_pct == current → return  (no callback at limit)
  │     ├── _set_volume(new_pct)  → amixer sset PCM X%
  │     ├── read back actual hardware level  ← AIC3104 PCM control has 128
  │     │   steps; requested % may quantise; read-back reports true value
  │     ├── if actual == current → return  (hardware didn't move)
  │     └── on_change(actual_pct)
  │
  └── cleanup()
        if use_gpio: GPIO.remove_event_detect(VOL_UP_PIN/VOL_DOWN_PIN)

Volume overlay:
  DisplayManager.show_volume(pct)
    └── sets _vol_pct, _vol_expiry = now + 2 s
  _animate() reads _vol_pct and draws:
    _draw_volume_overlay(draw, pct)
      └── bottom-centre bar 180×18 px
          cyan fill proportional to pct
          auto-clears after 2 s
```

**ALSA control:** defaults to `"PCM"` (AIC3104 DAC, 0–127 range, 63.5 dB). The older `"Master"` / `"Line"` control only has 10 hardware steps (9 dB range) — insufficient for meaningful volume control. Run `amixer scontrols` to list all available names on your hardware.

**VOL_MAX:** capped at `85` (%) by default. The NS4150 Class-D amp on the ReSpeaker HAT clips audibly above ~85% PCM level.

**Shutdown ordering** in `main.py`:
```
audio.stop_playback()      # kill any active mpg123 immediately
audio.play_shutdown_sound()
display.cleanup()          # stop render thread
volume_rocker.cleanup()    # remove_event_detect on GPIO 5, 6
button.cleanup()           # remove_event_detect on GPIO 17
GPIO.cleanup()             # single global cleanup at end
audio.cleanup()
```

---

### 4.6 Display Manager

**File:** `pi_client/display.py`  
**Library:** `luma.lcd` (ILI9341 SPI driver) + `Pillow`

#### Architecture

```
DisplayManager
  │
  ├── _device: luma.lcd ili9341  (or None on non-Pi)
  ├── _state:  str               (current face state)
  ├── _battery: int | None       (% from sysfs)
  ├── _image_override: PIL.Image (active during IMAGE state)
  ├── _image_expiry: float       (auto-revert timestamp)
  │
  ├── _render_thread  ─────────────────────────────────────────►
  │   _animate():                                               │
  │     while running:                                         │
  │       check image expiry → revert to IDLE                  │
  │       img = _render_face(state, frame, battery)            │
  │       device.display(img)                                  │
  │       frame = (frame+1) % 1000                             │
  │       sleep(0.1)          ← 10 fps                         │
  │◄────────────────────────────────────────────────────────────
  │
  ├── _battery_thread ─────────────────────────────────────────►
  │   _poll_battery():                                         │
  │     while running:                                         │
  │       read /sys/class/power_supply/*/capacity              │
  │       sleep(30)                                            │
  │◄────────────────────────────────────────────────────────────
  │
  ├── set_state(state)    → thread-safe, respects IMAGE lock
  ├── show_image_url(url) → spawns download thread
  └── cleanup()           → stop threads, device.cleanup()
```

#### Face Rendering

```
_render_face(state, frame, battery) → PIL.Image (320×240)
  │
  ├── Image.new("RGB", (320, 240), BG=(20,20,40))
  ├── _draw_battery(draw, battery)
  │
  └── dispatch on state:
        IDLE      → _draw_idle_eyes()     + _draw_mouth_smile(small)
        LISTENING → _draw_circle_eyes()   + _draw_eyebrows_raised()
                                          + _draw_mouth_open_o()
        THINKING  → _draw_rect_eyes()     + _draw_mouth_flat()
                                          + _draw_thinking_dots()
        SPEAKING  → _draw_idle_eyes()     + _draw_mouth_speaking()
        HAPPY     → _draw_happy_eyes()    + _draw_cheeks()
                                          + _draw_mouth_smile(large)
        ERROR     → _draw_x_eyes()        + _draw_mouth_frown()
```

#### Battery Indicator (top-right corner)

```
x=264, y=5   width=44  height=18  nub=4×8

┌────────────────────────┬──┐
│ ████████               │  │
└────────────────────────┴──┘
 fill colour: green >50%  yellow >20%  red ≤20%
```

#### Image Display Flow

```
show_image_url(url)
  │
  └── thread: _load_image(url)
        │
        ├── requests.get(url, timeout=8)
        ├── PIL.Image.open(...).convert("RGB")
        ├── thumbnail to fit (320 × 216, leaving 24 px for battery)
        ├── paste centred on 320×240 navy canvas
        ├── _image_override = canvas
        ├── _face_state = "IMAGE"
        └── _image_expiry = now + IMAGE_DISPLAY_SECONDS (8)
```

---

### 4.7 Pi Client Configuration

**File:** `pi_client/config.py`

| Variable | Default | Env Override |
|---|---|---|
| `SERVER_URL` | `http://192.168.1.100:8765` | `KIDBOT_SERVER` |
| `BUTTON_PIN` | `17` | — |
| `LED_PIN` | `27` | — |
| `SAMPLE_RATE` | `16000` | — |
| `MAX_RECORD_SECONDS` | `10` | — |
| `MIC_DEVICE_HINT` | `"aic3104"` | — |
| `API_KEY` | `""` | `KIDBOT_API_KEY` |
| `LOG_FILE` | `""` | `KIDBOT_LOG_FILE` |
| `DISPLAY_DC` | `25` | `DISPLAY_DC` |
| `DISPLAY_BL` | `24` | `DISPLAY_BL` |
| `DISPLAY_SPI_PORT` | `0` | `DISPLAY_SPI_PORT` |
| `DISPLAY_RST` | `None` | `DISPLAY_RST` |
| `IMAGE_DISPLAY_SECONDS` | `8` | `IMAGE_DISPLAY_SECONDS` |
| `VOL_UP_PIN` | `5` | `VOL_UP_PIN` |
| `VOL_DOWN_PIN` | `6` | `VOL_DOWN_PIN` |
| `VOL_STEP` | `5` | `VOL_STEP` |
| `VOL_MIN` | `0` | `VOL_MIN` |
| `VOL_MAX` | `85` | `VOL_MAX` |
| `ALSA_CONTROL` | `"PCM"` | `ALSA_CONTROL` |
| `STARTUP_VOLUME` | `45` | `STARTUP_VOLUME` |

`DISPLAY_RST` defaults to `None` to avoid a GPIO conflict with `LED_PIN=27`.

`MIC_DEVICE_HINT` matches the TLV320AIC3104 codec name reported by the mainline `snd_soc_tlv320aic3x` driver (kernel 6.18+). The older seeed-voicecard DKMS driver reported `"seeed"`.

`ALSA_CONTROL=PCM` targets the AIC3104 DAC volume (0–127 steps, 63.5 dB range). The legacy `"Master"` / `"Line"` control has only 10 hardware steps — insufficient range.

`VOL_MAX=85` caps the PCM level to avoid clipping on the NS4150 Class-D amp on the ReSpeaker HAT.

`STARTUP_VOLUME=45` is the PCM % used during boot and shutdown chimes; the level is restored to its previous value after the chime plays.

---

## 5. Request & Data Flows

### 5.1 Non-Streaming Voice Pipeline

```
Pi                        Network                    Server
──────────────────────────────────────────────────────────────
button.press()
  └─ audio.start_recording()

button.release()
  └─ audio.stop_recording() → audio.wav
        │
        └─ POST /chat_stream ─────────────────────────────────►
                               multipart/form-data
                               audio: audio.wav
                               session_id: <uuid>
                                                ┌─────────────┐
                                                │ STT.transcribe│
                                                │   Whisper    │
                                                └──────┬──────┘
                                                       │ text
                                                ┌──────▼──────┐
                                                │  guardrails  │
                                                │ input check  │
                                                └──────┬──────┘
                                                       │
                                                ┌──────▼──────┐
                                                │ LLM.respond  │
                                                │  LM Studio   │
                                                └──────┬──────┘
                                                       │ reply
                                                ┌──────▼──────┐
                                                │  guardrails  │
                                                │ output check │
                                                └──────┬──────┘
                                                       │
                                                ┌──────▼──────┐
                                                │ TTS.synthesize│
                                                │   Kokoro     │
                                                └──────┬──────┘
                                                       │ mp3
        ◄───────────────── 200 OK ─────────────────────┘
           Content-Type: audio/mpeg
           X-Transcription: <heard text>
           X-Reply: <bot reply>
           [X-Image-Url: <url>]
  │
  └─ audio.play_mp3_stream(mp3)
```

### 5.2 Streaming Voice Pipeline

```
Pi                        Network                    Server
──────────────────────────────────────────────────────────────
  POST /chat_stream ────────────────────────────────────────►
                                               STT ──► text
                                                         │
                                               LLM starts streaming
                                                         │
                                    ┌── producer thread: llm.respond_stream()
                                    │        │  sentence 1
                                    │        ▼
                                    │   asyncio.Queue
                                    │        │
                                    │   TTS(sentence 1)  ←─ parallel synthesis
                                    │        │ mp3 chunk 1
◄────── chunk 1 ────────────────────┘        │  sentence 2
  play begins                           TTS(sentence 2)
◄────── chunk 2 ──────────────────────────── │ mp3 chunk 2
◄────── chunk 3 ──────────────────────────── │ mp3 chunk 3
◄────── 200 complete ───────────────────────►│
  play finishes                       add_exchange()
                                      _fetch_and_store_image()  ← background

  GET /session/{id}/latest_image ─────────────────────────►
◄──────────── {"image_url": "..."} ──────────────────────────
  show_image_url() on display
```

### 5.3 Image Tag Flow

```
LLM reply contains: "Did you know that Tyrannosaurus Rex ... [IMAGE: Tyrannosaurus Rex dinosaur]"

Server (_sentence_stream):
  1. Detect [IMAGE: term] in sentence value
  2. Strip tag → yield clean text to TTS
  3. After streaming complete → _spawn_image_fetch() marks the session image
     "pending" and starts _fetch_and_store_image() via asyncio.create_task(),
     keeping a strong reference so the task can't be garbage-collected

_fetch_and_store_image():
  1. fetch_image_url("Tyrannosaurus Rex dinosaur")
     → 5-source parallel search → highest-priority (SSRF-checked) URL
  2. _sessions.set_latest_image(session_id, url)
  3. clears the "pending" flag (in a finally) whether or not a URL was found

Pi (after audio playback):
  1. Poll GET /session/{id}/latest_image until a URL arrives or pending=False
     → {"image_url": "https://upload.wikimedia.org/...", "pending": false}
  2. display.show_image_url(url)      → download → show 8 s → IDLE
  3. (test GUI also shows inline in chat)
```

### 5.4 Session Lifecycle

```
Pi startup
  └── ServerClient.__init__() → session_id = uuid4()   [fixed for process lifetime]

First chat
  └── _sessions._touch(session_id) → new Session()

Each turn
  └── add_exchange(session_id, user_text, reply_text)
        └── trim to 20 messages (10 turns)
        └── if PERSIST_SESSIONS: write to SQLite

30 min idle
  └── _purge_expired() → del session from memory + DB

DELETE /session/{id}
  └── explicit clear (test GUI "Clear History" button)

Server restart (with PERSIST_SESSIONS=1)
  └── SessionStore.__init__() → _load_from_db()
        └── discard sessions older than SESSION_TIMEOUT
        └── restore recent sessions to _sessions dict
```

---

## 6. API Reference

All endpoints except `/health` require `X-API-Key: <key>` when `KIDBOT_API_KEY` is set.

### GET `/health`
Returns server readiness. No authentication required.

**Response:**
```json
{"status": "ok"}      // 200 — models loaded
{"status": "loading"} // 503 — startup in progress
```

### POST `/chat`
Full voice pipeline. Returns MP3.

| Field | Type | Description |
|---|---|---|
| `audio` | file | WAV audio recording |
| `session_id` | form | Session identifier (default: `"default"`) |

**Response headers:** `X-Transcription`, `X-Reply`, `X-Image-Url` (if image found)

### POST `/chat_text`
Text input → LLM → TTS. Returns MP3. Bypasses STT.

| Field | Type | Description |
|---|---|---|
| `text` | form | User message |
| `session_id` | form | Session identifier |

**Response headers:** `X-Transcription`, `X-Reply`, `X-Image-Url` (if image found)

### POST `/chat_stream`
Streaming voice pipeline. Returns chunked MP3.

Same form fields as `/chat`. Rate: 5/min.

**Response headers:** `X-Transcription` (set immediately, before stream begins)

### POST `/chat_text_stream`
Streaming text pipeline. Returns chunked MP3.

Same form fields as `/chat_text`. Rate: 5/min.

### POST `/speak`
Text → TTS → MP3 only (no LLM). Rate: 20/min.

| Field | Type | Description |
|---|---|---|
| `text` | form | Text to speak |

### DELETE `/session/{session_id}`
Clear conversation history for a session.

```json
{"status": "cleared", "session_id": "..."}
```

### GET `/session/{session_id}/latest_image`
One-shot: returns and clears the image URL generated during the last exchange.
`pending` is `true` when a background image fetch is still running and no URL
is ready yet, so the client should poll again rather than give up.

```json
{"image_url": "https://...", "pending": false}  // image_url "" if none
```

### GET `/settings/voices`
List available Kokoro TTS voices.

```json
{"voices": ["af_bella", "bm_lewis", ...]}
```

### POST `/settings`
Update voice and/or speed at runtime (no restart required).

| Field | Type | Description |
|---|---|---|
| `voice` | form (optional) | Voice name |
| `speed` | form (optional) | Float 0.5–2.0 |

---

## 7. Content Safety

KidBot implements a two-stage content filter to protect children from harmful content.

```
User speech
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 1: Input Filter (is_input_safe)                   │
│                                                          │
│  ~40 blocked keywords: violence, sexual, drugs, weapons, │
│  hate speech, personal info solicitation                 │
│                                                          │
│  PASS ──────────────────────────────────────────────────►│
│  FAIL → REDIRECT_RESPONSE ("That's a great question      │
│          for a grown-up!")  [LLM never called]           │
└─────────────────────────────────────────────────────────┘
     │ (safe)
     ▼
   LLM generates reply
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 2: Output Filter (is_output_safe)                 │
│                                                          │
│  ~30 blocked keywords (stricter subset)                  │
│  Personal info solicitation patterns (regex)             │
│  Max response length: 900 chars                          │
│                                                          │
│  PASS → reply delivered                                  │
│  FAIL → OUTPUT_BLOCKED_RESPONSE ("Oops, I need to        │
│          rephrase that!")  [TTS uses fallback]           │
└─────────────────────────────────────────────────────────┘
```

In streaming mode, output filtering is applied per-sentence. On first blocked sentence, streaming stops immediately and the blocked response is yielded.

---

## 8. Test GUI

**File:** `test_gui.py`

The test GUI enables full end-to-end testing without Pi hardware.

```
┌─────────────────────────────────────────────────────────┐
│           YourChildBot  Test Console                        │
│─────────────────────────────────────────────────────────│
│  Mic: [ReSpeaker 2-Mic Array ▼    ]                     │
│─────────────────────────────────────────────────────────│
│                                                          │
│  ┌──────────────────────────────────┐  ┌──────────────┐ │
│  │                                  │  │ KidBot Screen│ │
│  │  System: Connected to YourChildBot. │  │  ┌────────┐  │ │
│  │  You: tell me about dinosaurs    │  │  │  ^  ^  │  │ │
│  │  System: Captured 24000 frames.. │  │  │   ◡◡   │  │ │
│  │  YourChildBot: Dinosaurs were...    │  │  └────────┘  │ │
│  │                                  │  │320×240(scaled│ │
│  │                                  │  └──────────────┘ │
│  └──────────────────────────────────┘                   │
│─────────────────────────────────────────────────────────│
│  Status: IDLE ████  Mic level: ░░░░░░░░░░░░░░░░         │
│─────────────────────────────────────────────────────────│
│  [type a message...                  ] [Send]           │
│─────────────────────────────────────────────────────────│
│  SPACE (hold): Record  |  ENTER: Send text              │
└─────────────────────────────────────────────────────────┘
```

#### FacePanel
Renders the identical PIL face from `pi_client/display.py` inside a 240×180 tkinter Canvas at 10 fps. All 7 states animate correctly. Image display and HAPPY/IDLE transitions match Pi behaviour exactly.

#### Thread Architecture
```
Main thread (tkinter mainloop)
  ├── _poll_queue() every 40 ms   ← processes all UI updates
  ├── FacePanel._tick() every 100 ms  ← face animation (root.after)
  └── key bindings → event handlers

Background threads (daemon):
  ├── process()         ← HTTP request + playback
  ├── _feed()           ← pipes MP3 chunks to ffmpeg stdin
  ├── meter()           ← mic level sampling
  ├── _after_play_check() ← polls /latest_image, sets face state
  └── _show_image()     ← downloads and inserts chat image
```

---

## 9. Test Suite

**Run:** `python -m pytest tests/ -v`

```
tests/
├── conftest.py         Stubs: openai, faster_whisper, kokoro_onnx,
│                              soundfile, sounddevice, PIL, tkinter
│
├── test_api.py         55 tests — all HTTP endpoints, rate limiting,
│                       auth, streaming, session management, image URL
│
├── test_guardrails.py  48 tests — keyword blocking, output length,
│                       personal info patterns, system prompt content
│
├── test_llm.py         14 tests — respond(), respond_stream(),
│                       sentence chunking, safety integration
│
├── test_session.py     20 tests — in-memory + SQLite persistence,
│                       trimming, expiry, image/reply one-shot fields
│
├── test_stt.py          6 tests — transcription output, kwargs
│
├── test_tts.py         12 tests — clean_for_speech(), synthesis,
│                       ffmpeg invocation, temp file cleanup
│
├── test_image_search.py  46 tests — 5-source search, priority/fallback logic (all sources mocked)
│
├── test_volume.py       30 tests — _get_volume parsing, _set_volume args,
│                        VolumeRocker._adjust (clamp, no-op, on_change),
│                        GPIO pin setup and cleanup
│
├── test_display_volume.py 10 tests — show_volume(), overlay expiry,
│                        _draw_volume_overlay() pixel/call assertions
│
└── test_gui_logic.py   17 tests — mic device filtering, WAV writing,
                        GUI state machine (tkinter skipped headless)
```

**CI:** GitHub Actions runs 3 parallel test jobs on every PR to `main` using Python 3.11 (Ubuntu latest). On push to `main`, tests must pass before the deploy job runs, followed by a smoke test on the live server.

---

## 10. Dependencies & Requirements

### Server (`requirements/server_requirements.txt`)

| Package | Purpose |
|---|---|
| `fastapi>=0.111` | HTTP framework |
| `python-multipart>=0.0.9` | Multipart form parsing |
| `uvicorn[standard]>=0.30` | ASGI server |
| `faster-whisper>=1.0` | Speech recognition |
| `openai>=1.0` | LM Studio API client (OpenAI-compatible) |
| `kokoro-onnx>=0.4` | Text-to-speech |
| `soundfile>=0.12` | WAV file I/O |
| `requests>=2.32` | Image search HTTP |
| `slowapi>=0.1.9` | Rate limiting |
| `numpy>=1.24` | Audio array handling |

**System packages required:**
- `ffmpeg` — MP3 encoding
- LM Studio desktop app — LLM inference (must be running with a model loaded)

### Pi Client (`requirements/pi_requirements.txt`)

| Package | Purpose |
|---|---|
| `RPi.GPIO>=0.7.1` | GPIO button + LED |
| `pyaudio>=0.2.14` | Audio capture |
| `requests>=2.31` | Server HTTP client |
| `luma.lcd>=2.9` | ILI9341 LCD driver |
| `Pillow>=10` | Face image rendering |

**System packages required:**
- `mpg123` — MP3 playback (`sudo apt install mpg123`)
- `pulseaudio-utils` — provides `paplay` for volume blip sounds (`sudo apt install pulseaudio-utils`)
- SPI enabled in `raspi-config`

> **PipeWire note:** Raspberry Pi OS Bookworm runs PipeWire as the audio server. PipeWire holds the ALSA hardware device (`hw:1,0`) after the first audio client connects. Direct `aplay -D plughw:1,0` calls will fail with "device busy" while PipeWire is active. `paplay` (via the PulseAudio compatibility layer) routes through PipeWire and works correctly. The startup/shutdown chimes use `aplay` directly because they play at boot/shutdown before PipeWire has acquired the device.

---

## 11. Environment Variables

### Server

```bash
# Network
SERVER_HOST=0.0.0.0
SERVER_PORT=8765

# Speech-to-Text
WHISPER_MODEL=small          # tiny | base | small | medium | large-v3
WHISPER_DEVICE=cpu           # cpu | cuda
WHISPER_COMPUTE_TYPE=int8    # int8 | float16 | float32

# LLM (LM Studio)
LM_STUDIO_URL=http://127.0.0.1:1234/v1  # bare Python; use host.docker.internal for Docker
LM_STUDIO_MODEL=google/gemma-4-e4b      # must match model ID in LM Studio
LLM_MAX_HISTORY=8
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=700
CHILD_NAME=YourChild            # injected into system prompt

# Text-to-Speech
KOKORO_MODEL=server/models/kokoro-v1.0.onnx
KOKORO_VOICES=server/models/voices-v1.0.bin
KOKORO_VOICE=bm_lewis        # any voice from available_voices()
KOKORO_SPEED=1.2             # 0.5 – 2.0

# Session persistence
PERSIST_SESSIONS=1           # omit or set to 0 to disable
SESSION_DB_PATH=server/sessions.db

# Security
KIDBOT_API_KEY=              # empty = auth disabled

# Logging
LOG_FILE=                    # empty = stdout only
LOG_MAX_BYTES=10485760       # 10 MB
LOG_BACKUP_COUNT=5
```

### Pi Client

```bash
KIDBOT_SERVER=http://192.168.1.100:8765
KIDBOT_API_KEY=              # must match server
KIDBOT_LOG_FILE=             # path or empty for stdout

# Display (Waveshare 2.4" ILI9341)
DISPLAY_DC=25
DISPLAY_BL=24
DISPLAY_SPI_PORT=0
DISPLAY_RST=                 # empty = no hardware reset (avoids GPIO 27 conflict)
IMAGE_DISPLAY_SECONDS=8

# Volume rocker
VOL_UP_PIN=5                 # BCM GPIO for vol-up button (physical pin 29)
VOL_DOWN_PIN=6               # BCM GPIO for vol-down button (physical pin 31)
VOL_STEP=5                   # % change per press (~3 dB on AIC3104 PCM control)
VOL_MIN=0
VOL_MAX=85                   # NS4150 amp clips above ~85% PCM — do not raise
ALSA_CONTROL=PCM             # AIC3104 DAC control; run 'amixer scontrols' to list
STARTUP_VOLUME=45            # PCM % for boot/shutdown chimes
```
