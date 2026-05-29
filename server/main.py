import asyncio
import logging
import logging.handlers
import math
import os
import re
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .config import (API_KEY, BOT_NAME, LOG_BACKUP_COUNT, LOG_FILE, LOG_MAX_BYTES,
                     PERSIST_SESSIONS, SERVER_HOST, SERVER_PORT, SESSION_DB_PATH, TEMP_DIR)
from .image_search import fetch_image_url
from .llm import LLMInterface
from .session import SessionStore
from .stt import SpeechToText
from .tts import TextToSpeech

def _configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if LOG_FILE:
        from pathlib import Path
        Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        rh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
        )
        rh.setFormatter(fmt)
        root.addHandler(rh)

_configure_logging()
logger = logging.getLogger(__name__)

_stt: SpeechToText | None = None
_llm: LLMInterface | None = None
_tts: TextToSpeech | None = None
_sessions = SessionStore(db_path=SESSION_DB_PATH if PERSIST_SESSIONS else None)

SORRY_TRY_AGAIN  = "Hmm, I didn't quite catch that! Could you try saying it again?"
SORRY_CANT_THINK = "Oops, I got a bit muddled! Give me a moment and try again."


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _stt, _llm, _tts
    logger.info("Loading models - this may take a moment...")
    _stt = SpeechToText()
    _llm = LLMInterface()
    _tts = TextToSpeech()
    logger.info("All models loaded. %s server is ready.", BOT_NAME)
    yield
    logger.info("Shutting down.")


limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title=f"{BOT_NAME} Server", version="0.4.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def _api_key_middleware(request: Request, call_next):
    # /health is always exempt so the Pi can confirm connectivity before auth.
    if API_KEY and request.url.path != "/health":
        if request.headers.get("X-API-Key") != API_KEY:
            return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
    return await call_next(request)


_IMAGE_TAG_RE = re.compile(r'\[IMAGE:\s*([^\]]+)\]', re.IGNORECASE)

# Detect when the user explicitly asks to see a picture of something,
# and extract the subject so we can search even if the model forgets the tag.
_IMAGE_REQUEST_RE = re.compile(
    r'\b(show me|see|picture|image|photo)\b',
    re.IGNORECASE,
)
_IMAGE_SUBJECT_RE = re.compile(
    r'(?:picture|image|photo)\s+of\s+(.+?)[\?\.\!]?\s*$|'
    r'see\s+(?:a\s+|an\s+)?(?:picture|image|photo)\s+of\s+(.+?)[\?\.\!]?\s*$|'
    r'show\s+me\s+(?:a\s+|an\s+)?(?:picture|image|photo)\s+of\s+(.+?)[\?\.\!]?\s*$|'
    r'what does\s+(.+?)\s+look like',
    re.IGNORECASE,
)


def _fallback_image_term(text: str) -> str | None:
    """
    If the user explicitly asked for a picture, extract the subject.
    Used when the model response contained no [IMAGE: ...] tag.
    Returns a search term string, or None if no image was requested.
    """
    if not _IMAGE_REQUEST_RE.search(text):
        return None
    m = _IMAGE_SUBJECT_RE.search(text)
    if m:
        # Return the first non-empty capture group
        term = next((g for g in m.groups() if g), None)
        return term.strip() if term else None
    return None


def _extract_image(text: str) -> tuple[str, str | None]:
    """Remove [IMAGE: term] tag from text. Returns (clean_text, term_or_None)."""
    m = _IMAGE_TAG_RE.search(text)
    if m:
        term = m.group(1).strip()
        clean = _IMAGE_TAG_RE.sub("", text).strip()
        return clean, term
    return text, None


def _safe_header(text: str) -> str:
    """Make text safe for HTTP headers — no non-ASCII, no newlines or control chars."""
    text = text.replace("\r", " ").replace("\n", " ")
    # Normalise common Unicode punctuation before ASCII encoding
    text = text.replace("‘", "'").replace("’", "'")  # curly apostrophes
    text = text.replace("“", '"').replace("”", '"')  # curly double quotes
    text = text.replace("—", "-").replace("–", "-")  # em/en dash
    text = text.encode("ascii", errors="replace").decode("ascii")
    return " ".join(text.split())  # collapse any resulting multiple spaces


def _mp3_response(reply_text: str, transcription: str = "", image_url: str = "") -> Response:
    """Synthesise reply and return MP3 with conversation text in headers."""
    if _tts is None:
        raise HTTPException(status_code=503, detail="Models not ready")
    mp3 = _tts.synthesize(reply_text)
    headers = {
        "X-Transcription": _safe_header(transcription),
        "X-Reply":         _safe_header(reply_text),
    }
    if image_url:
        headers["X-Image-Url"] = _safe_header(image_url)
    return Response(content=mp3, media_type="audio/mpeg", headers=headers)


def _run_llm_pipeline(text: str, session_id: str) -> tuple[str, str]:
    """Run LLM, extract image tag, fetch image URL. Returns (reply_text, image_url)."""
    if _llm is None:
        raise HTTPException(status_code=503, detail="Models not ready")
    history = _sessions.get_history(session_id)
    raw_reply = _llm.respond(text, history=history)
    reply_text, image_term = _extract_image(raw_reply)
    _sessions.add_exchange(session_id, text, reply_text)
    if not image_term:
        # Model omitted the [IMAGE: ...] tag — if the child explicitly asked to
        # see a picture, search from their message (mirrors the streaming path).
        image_term = _fallback_image_term(text)
    image_url = ""
    if image_term:
        logger.info("[%s] Fetching image for: %r", session_id, image_term)
        shown = _sessions.get_shown_image_urls(session_id)
        image_url = fetch_image_url(image_term, exclude_urls=shown) or ""
    return reply_text, image_url


@app.get("/health")
async def health():
    ready = _stt is not None and _llm is not None and _tts is not None
    return JSONResponse(
        {"status": "ok" if ready else "loading"},
        status_code=200 if ready else 503,
    )


@app.post("/speak")
@limiter.limit("20/minute")
async def speak(request: Request, text: str = Form(...)):
    """Convert text to MP3. Used by the Pi to pre-fetch error audio clips."""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > 10_000:
        raise HTTPException(status_code=400, detail="Input too long")
    return Response(content=_tts.synthesize(text), media_type="audio/mpeg")


@app.post("/chat")
@limiter.limit("5/minute")
async def chat(
    request: Request,
    audio: UploadFile = File(...),
    session_id: str = Form(default="default"),
):
    """
    Voice pipeline: WAV -> STT -> LLM -> TTS -> MP3.
    Response headers include X-Transcription and X-Reply for the GUI.
    """
    if _stt is None or _llm is None or _tts is None:
        raise HTTPException(status_code=503, detail="Models not ready")
    audio_data = await audio.read()
    if not audio_data:
        raise HTTPException(status_code=400, detail="Empty audio file")

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav", dir=TEMP_DIR)
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(audio_data)

        # STT, LLM and TTS are all synchronous/CPU-bound; run them in the
        # threadpool so a multi-second turn doesn't block the event loop (and
        # stall /health, /latest_image, etc.).
        t0 = time.perf_counter()
        text = await run_in_threadpool(_stt.transcribe, tmp_path)
        t_stt = time.perf_counter() - t0

        if not text:
            logger.info("[%s] No speech detected.", session_id)
            return await run_in_threadpool(_mp3_response, SORRY_TRY_AGAIN)

        logger.info("[%s] STT: %.2fs  heard: %r", session_id, t_stt, text)

        t1 = time.perf_counter()
        reply_text, image_url = await run_in_threadpool(_run_llm_pipeline, text, session_id)
        t_llm = time.perf_counter() - t1

        t2 = time.perf_counter()
        mp3_response = await run_in_threadpool(_mp3_response, reply_text, text, image_url)
        t_tts = time.perf_counter() - t2

        logger.info("[%s] LLM: %.2fs  TTS: %.2fs  total: %.2fs",
                    session_id, t_llm, t_tts, t_stt + t_llm + t_tts)
        return mp3_response

    except Exception as exc:
        logger.error("[%s] Pipeline error: %s", session_id, exc, exc_info=True)
        return await run_in_threadpool(_mp3_response, SORRY_CANT_THINK)

    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


@app.post("/chat_text")
@limiter.limit("5/minute")
async def chat_text(
    request: Request,
    text: str = Form(...),
    session_id: str = Form(default="default"),
):
    """
    Text pipeline: typed text -> LLM -> TTS -> MP3.
    Bypasses STT — used by the test GUI for direct text injection.
    """
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > 10_000:
        raise HTTPException(status_code=400, detail="Input too long")
    logger.info("[%s] Text input: %r", session_id, text)
    try:
        # Offload the synchronous LLM + TTS work so the event loop stays free.
        t1 = time.perf_counter()
        reply_text, image_url = await run_in_threadpool(_run_llm_pipeline, text, session_id)
        t_llm = time.perf_counter() - t1
        t2 = time.perf_counter()
        mp3_response = await run_in_threadpool(_mp3_response, reply_text, text, image_url)
        t_tts = time.perf_counter() - t2
        logger.info("[%s] LLM: %.2fs  TTS: %.2fs  total: %.2fs",
                    session_id, t_llm, t_tts, t_llm + t_tts)
        return mp3_response
    except Exception as exc:
        logger.error("[%s] Pipeline error: %s", session_id, exc, exc_info=True)
        return await run_in_threadpool(_mp3_response, SORRY_CANT_THINK)


@app.delete("/session/{session_id}")
async def clear_session(session_id: str):
    """Reset conversation history for a session."""
    _sessions.clear(session_id)
    return {"status": "cleared", "session_id": session_id}


# ---------------------------------------------------------------------------
# Streaming endpoints — sentence-level TTS parallelism
# ---------------------------------------------------------------------------

async def _sentence_stream(text: str, session_id: str) -> AsyncGenerator[bytes, None]:
    """
    Async generator: yields one MP3 chunk per sentence.
    LLM runs in a producer thread; TTS synthesises each sentence in the
    thread pool as it arrives, so playback begins before the LLM finishes.
    """
    history = _sessions.get_history(session_id)
    # Discard any image left unpolled from a previous turn so a stale URL can't
    # surface on this one.
    _sessions.reset_image(session_id)
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _producer():
        try:
            for sentence in _llm.respond_stream(text, history):
                loop.call_soon_threadsafe(queue.put_nowait, ("s", sentence))
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("err", str(exc)))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

    t = threading.Thread(target=_producer, daemon=True)
    t.start()

    full_parts: list[str] = []
    image_term: str | None = None
    while True:
        kind, value = await queue.get()
        if kind == "done":
            break
        if kind == "err":
            logger.error("LLM stream error: %s", value)
            break
        m = _IMAGE_TAG_RE.search(value)
        if m and image_term is None:
            image_term = m.group(1).strip()
        clean = _IMAGE_TAG_RE.sub("", value).strip()
        if not clean:
            continue
        full_parts.append(clean)
        try:
            mp3 = await run_in_threadpool(_tts.synthesize, clean)
        except Exception as exc:
            # A single sentence failing to synthesise (e.g. a transient ffmpeg
            # error) must not abort the whole stream — skip it and keep going.
            logger.error("[%s] TTS failed for sentence %r: %s", session_id, clean, exc)
            continue
        yield mp3

    t.join()
    if full_parts:
        full_reply = " ".join(full_parts)
        _sessions.add_exchange(session_id, text, full_reply)
        _sessions.set_latest_reply(session_id, full_reply)
    if image_term:
        logger.info("[%s] Fetching stream image for: %r", session_id, image_term)
        _spawn_image_fetch(session_id, image_term)
    else:
        # Model didn't emit [IMAGE: ...] — check if the user explicitly asked
        # for a picture and trigger a fallback search from their message.
        fallback = _fallback_image_term(text)
        if fallback:
            logger.info("[%s] Model omitted image tag — fallback search for: %r",
                        session_id, fallback)
            _spawn_image_fetch(session_id, fallback)


# Strong references to in-flight background tasks. asyncio keeps only weak
# references to tasks created with create_task(), so without this set a fetch
# could be garbage-collected mid-execution.
_background_tasks: set[asyncio.Task] = set()


def _spawn_image_fetch(session_id: str, term: str) -> None:
    """Start a background image fetch, marking the session image as pending and
    retaining a strong reference to the task until it completes."""
    _sessions.set_image_pending(session_id, True)
    task = asyncio.create_task(_fetch_and_store_image(session_id, term))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _fetch_and_store_image(session_id: str, term: str) -> None:
    try:
        shown = _sessions.get_shown_image_urls(session_id)
        url = await run_in_threadpool(fetch_image_url, term, 500, shown) or ""

        if not url:
            # Primary search failed — retry with enriched variants.
            # Helps bare terms like "Spider-Man" find character/costume photos.
            for suffix in ("character", "photo", "illustration", "comic"):
                variant = f"{term} {suffix}"
                logger.info("[%s] Retrying image search with variant: %r", session_id, variant)
                url = await run_in_threadpool(fetch_image_url, variant, 500, shown) or ""
                if url:
                    break

        if url:
            _sessions.set_latest_image(session_id, url)
            logger.info("[%s] Stored image URL for %r", session_id, term)
        else:
            logger.warning("[%s] Image search returned no results for %r (all variants exhausted)", session_id, term)
    except Exception as exc:
        logger.error("[%s] Background image fetch failed for %r: %s", session_id, term, exc)
    finally:
        # Always clear pending so the Pi's poll loop stops waiting.
        _sessions.set_image_pending(session_id, False)


@app.get("/session/{session_id}/latest_image")
async def get_latest_image(session_id: str):
    """One-shot: returns and clears the latest image URL for a session.

    `pending` is True when a background image fetch is still running and no URL
    is ready yet, so the client knows to poll again rather than give up.
    """
    url = _sessions.get_and_clear_latest_image(session_id)
    pending = bool(not url and _sessions.is_image_pending(session_id))
    return {"image_url": url, "pending": pending}


@app.get("/session/{session_id}/latest_reply")
async def get_latest_reply(session_id: str):
    """One-shot: returns and clears the latest bot reply text for a session."""
    return {"reply": _sessions.get_and_clear_latest_reply(session_id)}


@app.get("/settings/voices")
async def list_voices():
    """Return available TTS voice names."""
    if _tts is None:
        raise HTTPException(status_code=503, detail="Models not ready")
    return {"voices": _tts.available_voices(), "current_voice": _tts.voice, "current_speed": _tts.speed}


@app.post("/settings")
async def update_settings(
    request: Request,
    voice: str = Form(default=""),
    speed: str = Form(default=""),
):
    """Update TTS voice and/or speed at runtime."""
    if _tts is None:
        raise HTTPException(status_code=503, detail="Models not ready")
    changes = {}
    if voice:
        if voice not in _tts.available_voices():
            raise HTTPException(status_code=400, detail=f"Unknown voice: {voice!r}")
        _tts.set_voice(voice)
        changes["voice"] = voice
    if speed:
        try:
            speed_val = float(speed)
            if not math.isfinite(speed_val) or not (0.5 <= speed_val <= 2.0):
                raise ValueError("out of range")
            _tts.set_speed(speed_val)
            changes["speed"] = _tts.speed
        except ValueError:
            raise HTTPException(status_code=400, detail="Speed must be a number between 0.5 and 2.0")
    return {"status": "ok", "changes": changes}


@app.post("/chat_text_stream")
@limiter.limit("5/minute")
async def chat_text_stream(
    request: Request,
    text: str = Form(...),
    session_id: str = Form(default="default"),
):
    """Text → streaming LLM → sentence-by-sentence TTS → chunked MP3."""
    if _llm is None or _tts is None:
        raise HTTPException(status_code=503, detail="Models not ready")
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > 10_000:
        raise HTTPException(status_code=400, detail="Input too long")
    logger.info("[%s] Stream text input: %r", session_id, text)
    return StreamingResponse(_sentence_stream(text, session_id), media_type="audio/mpeg")


@app.post("/chat_stream")
@limiter.limit("5/minute")
async def chat_stream(
    request: Request,
    audio: UploadFile = File(...),
    session_id: str = Form(default="default"),
):
    """WAV → STT → streaming LLM → sentence-by-sentence TTS → chunked MP3."""
    if _stt is None or _llm is None or _tts is None:
        raise HTTPException(status_code=503, detail="Models not ready")
    audio_data = await audio.read()
    if not audio_data:
        raise HTTPException(status_code=400, detail="Empty audio file")

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav", dir=TEMP_DIR)
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(audio_data)
        text = await run_in_threadpool(_stt.transcribe, tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass

    if not text:
        logger.info("[%s] Stream: no speech detected.", session_id)
        mp3 = await run_in_threadpool(_tts.synthesize, SORRY_TRY_AGAIN)
        return Response(content=mp3, media_type="audio/mpeg")

    logger.info("[%s] Stream STT: %r", session_id, text)
    return StreamingResponse(
        _sentence_stream(text, session_id),
        media_type="audio/mpeg",
        headers={"X-Transcription": _safe_header(text)},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)
