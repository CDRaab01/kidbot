import logging
import os
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from .config import SERVER_HOST, SERVER_PORT, TEMP_DIR
from .llm import LLMInterface
from .session import SessionStore
from .stt import SpeechToText
from .tts import TextToSpeech

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_stt: SpeechToText | None = None
_llm: LLMInterface | None = None
_tts: TextToSpeech | None = None
_sessions = SessionStore()

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


app = FastAPI(title="CooperBot Server", version="0.4.0", lifespan=lifespan)


def _safe_header(text: str) -> str:
    """Make text safe for HTTP headers — no non-ASCII, no newlines or control chars."""
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.encode("ascii", errors="replace").decode("ascii")
    return " ".join(text.split())  # collapse any resulting multiple spaces


def _mp3_response(reply_text: str, transcription: str = "") -> Response:
    """Synthesise reply and return MP3 with conversation text in headers."""
    mp3 = _tts.synthesize(reply_text)
    return Response(
        content=mp3,
        media_type="audio/mpeg",
        headers={
            "X-Transcription": _safe_header(transcription),
            "X-Reply":         _safe_header(reply_text),
        },
    )


def _process_text(text: str, session_id: str) -> Response:
    """Shared pipeline: text -> LLM -> TTS -> MP3 response."""
    history = _sessions.get_history(session_id)
    reply_text = _llm.respond(text, history=history)
    logger.info("[%s] CooperBot says: %r", session_id, reply_text)
    _sessions.add_exchange(session_id, text, reply_text)
    return _mp3_response(reply_text, transcription=text)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/speak")
async def speak(text: str = Form(...)):
    """Convert text to MP3. Used by the Pi to pre-fetch error audio clips."""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")
    return Response(content=_tts.synthesize(text), media_type="audio/mpeg")


@app.post("/chat")
async def chat(
    audio: UploadFile = File(...),
    session_id: str = Form(default="default"),
):
    """
    Voice pipeline: WAV -> STT -> LLM -> TTS -> MP3.
    Response headers include X-Transcription and X-Reply for the GUI.
    """
    audio_data = await audio.read()
    if not audio_data:
        raise HTTPException(status_code=400, detail="Empty audio file")

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav", dir=TEMP_DIR)
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(audio_data)

        text = _stt.transcribe(tmp_path)
        if not text:
            logger.info("[%s] No speech detected.", session_id)
            return _mp3_response(SORRY_TRY_AGAIN)

        logger.info("[%s] Child said: %r", session_id, text)
        return _process_text(text, session_id)

    except Exception as exc:
        logger.error("[%s] Pipeline error: %s", session_id, exc, exc_info=True)
        return _mp3_response(SORRY_CANT_THINK)

    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


@app.post("/chat_text")
async def chat_text(
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
        return _process_text(text, session_id)
    except Exception as exc:
        logger.error("[%s] Pipeline error: %s", session_id, exc, exc_info=True)
        return _mp3_response(SORRY_CANT_THINK)


@app.delete("/session/{session_id}")
async def clear_session(session_id: str):
    """Reset conversation history for a session."""
    _sessions.clear(session_id)
    return {"status": "cleared", "session_id": session_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)
