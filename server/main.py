import asyncio
import logging
import logging.handlers
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

from .config import (API_KEY, LOG_BACKUP_COUNT, LOG_FILE, LOG_MAX_BYTES,
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
    logger.info("All models loaded. CooperBot server is ready.")
    yield
    logger.info("Shutting down.")


limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="CooperBot Server", version="0.4.0", lifespan=lifespan)
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
        headers["X-Image-Url"] = image_url
    return Response(content=mp3, media_type="audio/mpeg", headers=headers)


def _run_llm_pipeline(text: str, session_id: str) -> tuple[str, str]:
    """Run LLM, extract image tag, fetch image URL. Returns (reply_text, image_url)."""
    if _llm is None:
        raise HTTPException(status_code=503, detail="Models not ready")
    history = _sessions.get_history(session_id)
    raw_reply = _llm.respond(text, history=history)
    reply_text, image_term = _extract_image(raw_reply)
    _sessions.add_exchange(session_id, text, reply_text)
    image_url = ""
    if image_term:
        logger.info("[%s] Fetching image for: %r", session_id, image_term)
        image_url = fetch_image_url(image_term) or ""
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

        t0 = time.perf_counter()
        text = _stt.transcribe(tmp_path)
        t_stt = time.perf_counter() - t0

        if not text:
            logger.info("[%s] No speech detected.", session_id)
            return _mp3_response(SORRY_TRY_AGAIN)

        logger.info("[%s] STT: %.2fs  heard: %r", session_id, t_stt, text)

        t1 = time.perf_counter()
        reply_text, image_url = _run_llm_pipeline(text, session_id)
        t_llm = time.perf_counter() - t1

        t2 = time.perf_counter()
        mp3_response = _mp3_response(reply_text, transcription=text, image_url=image_url)
        t_tts = time.perf_counter() - t2

        logger.info("[%s] LLM: %.2fs  TTS: %.2fs  total: %.2fs",
                    session_id, t_llm, t_tts, t_stt + t_llm + t_tts)
        return mp3_response

    except Exception as exc:
        logger.error("[%s] Pipeline error: %s", session_id, exc, exc_info=True)
        return _mp3_response(SORRY_CANT_THINK)

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
    logger.info("[%s] Text input: %r", session_id, text)
    try:
        t1 = time.perf_counter()
        reply_text, image_url = _run_llm_pipeline(text, session_id)
        t_llm = time.perf_counter() - t1
        t2 = time.perf_counter()
        mp3_response = _mp3_response(reply_text, transcription=text, image_url=image_url)
        t_tts = time.perf_counter() - t2
        logger.info("[%s] LLM: %.2fs  TTS: %.2fs  total: %.2fs",
                    session_id, t_llm, t_tts, t_llm + t_tts)
        return mp3_response
    except Exception as exc:
        logger.error("[%s] Pipeline error: %s", session_id, exc, exc_info=True)
        return _mp3_response(SORRY_CANT_THINK)


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
    while True:
        kind, value = await queue.get()
        if kind == "done":
            break
        if kind == "err":
            logger.error("LLM stream error: %s", value)
            break
        clean = _IMAGE_TAG_RE.sub("", value).strip()
        if not clean:
            continue
        full_parts.append(clean)
        mp3 = await run_in_threadpool(_tts.synthesize, clean)
        yield mp3

    t.join()
    if full_parts:
        _sessions.add_exchange(session_id, text, " ".join(full_parts))


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
