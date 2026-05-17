import logging
import ollama
from .config import OLLAMA_MODEL, LLM_MAX_TOKENS, LLM_TEMPERATURE
from .guardrails import SYSTEM_PROMPT, REDIRECT_RESPONSE, OUTPUT_BLOCKED_RESPONSE, is_input_safe, is_output_safe

logger = logging.getLogger(__name__)


class LLMInterface:
    def __init__(self):
        models = [m.model for m in ollama.list().models]
        if not any(OLLAMA_MODEL in m for m in models):
            raise RuntimeError(
                f"Ollama model '{OLLAMA_MODEL}' not found. "
                f"Run: ollama list   to see available models."
            )
        logger.info("LLM ready. Model: %s", OLLAMA_MODEL)

    def respond(self, user_text: str, history: list | None = None) -> str:
        if not is_input_safe(user_text):
            logger.warning("Blocked input: %r", user_text)
            return REDIRECT_RESPONSE

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        if history:
            messages.extend(history)

        messages.append({"role": "user", "content": user_text})

        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=messages,
            options={
                "temperature": LLM_TEMPERATURE,
                "num_predict": LLM_MAX_TOKENS,
            },
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
