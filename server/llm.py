import logging
import re
from typing import Iterator

import ollama
from .config import OLLAMA_MODEL, LLM_MAX_TOKENS, LLM_TEMPERATURE
from .guardrails import (OUTPUT_BLOCKED_RESPONSE, REDIRECT_RESPONSE, get_system_prompt,
                         is_input_safe, is_output_safe)

logger = logging.getLogger(__name__)

_SENT_BOUNDARY = re.compile(r'(?<=[.!?])\s+')
_MIN_SENTENCE_LEN = 8  # merge fragments shorter than this with the next chunk


class LLMInterface:
    def __init__(self):
        models = [m.model for m in ollama.list().models]
        if not any(OLLAMA_MODEL in m for m in models):
            raise RuntimeError(
                f"Ollama model '{OLLAMA_MODEL}' not found. "
                f"Run: ollama list   to see available models."
            )
        logger.info("LLM ready. Model: %s", OLLAMA_MODEL)

    def _build_messages(self, user_text: str, history: list | None) -> list:
        messages = [{"role": "system", "content": get_system_prompt()}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        return messages

    def respond(self, user_text: str, history: list | None = None) -> str:
        if not is_input_safe(user_text):
            logger.warning("Blocked input: %r", user_text)
            return REDIRECT_RESPONSE

        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=self._build_messages(user_text, history),
            options={"temperature": LLM_TEMPERATURE, "num_predict": LLM_MAX_TOKENS},
        )

        try:
            reply = response.message.content.strip()
        except (AttributeError, KeyError, TypeError):
            logger.error("Unexpected Ollama response format: %r", response)
            return OUTPUT_BLOCKED_RESPONSE
        if not reply:
            return OUTPUT_BLOCKED_RESPONSE

        safe, reason = is_output_safe(reply)
        if not safe:
            logger.warning("Output blocked — %s", reason)
            return OUTPUT_BLOCKED_RESPONSE

        logger.info("LLM reply: %r", reply)
        return reply

    def respond_stream(self, user_text: str, history: list | None = None) -> Iterator[str]:
        """Yield sentence-sized chunks from the LLM with per-sentence safety checks."""
        if not is_input_safe(user_text):
            logger.warning("Blocked input (stream): %r", user_text)
            yield REDIRECT_RESPONSE
            return

        stream = ollama.chat(
            model=OLLAMA_MODEL,
            messages=self._build_messages(user_text, history),
            options={"temperature": LLM_TEMPERATURE, "num_predict": LLM_MAX_TOKENS},
            stream=True,
        )

        buffer = ""
        for chunk in stream:
            try:
                buffer += chunk.message.content
            except (AttributeError, TypeError):
                continue

            while True:
                m = _SENT_BOUNDARY.search(buffer)
                if not m:
                    break
                sentence = buffer[:m.start() + 1].strip()
                buffer = buffer[m.end():]
                if len(sentence) < _MIN_SENTENCE_LEN:
                    buffer = sentence + " " + buffer
                    break
                safe, reason = is_output_safe(sentence)
                if not safe:
                    logger.warning("Stream output blocked — %s", reason)
                    yield OUTPUT_BLOCKED_RESPONSE
                    return
                logger.info("LLM stream sentence: %r", sentence)
                yield sentence

        if buffer.strip():
            remaining = buffer.strip()
            safe, reason = is_output_safe(remaining)
            if safe:
                logger.info("LLM stream final: %r", remaining)
                yield remaining
            else:
                logger.warning("Stream final output blocked — %s", reason)
                yield OUTPUT_BLOCKED_RESPONSE
