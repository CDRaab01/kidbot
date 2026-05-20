import logging
import re
from typing import Iterator

from openai import OpenAI
from .config import LM_STUDIO_BASE_URL, LM_STUDIO_MODEL, LLM_MAX_TOKENS, LLM_MAX_HISTORY_EXCHANGES, LLM_TEMPERATURE
from .guardrails import (OUTPUT_BLOCKED_RESPONSE, REDIRECT_RESPONSE, get_system_prompt,
                         is_input_safe, is_output_safe)

logger = logging.getLogger(__name__)

# Split on whitespace after sentence-end OR a capital letter directly after
# sentence-end punctuation (Gemma 4 sometimes omits the space at the
# reasoning→response boundary, e.g. "…repetition.Good morning!").
_SENT_BOUNDARY = re.compile(r'(?<=[.!?])(?:\s+|(?=[A-Z]))')

# Strip <think>...</think> blocks (some model configs emit tagged reasoning).
_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)

# Preserve [IMAGE: ...] tags embedded in reasoning sentences so the server
# can still trigger an image search even when the sentence is filtered.
_IMAGE_TAG_RE = re.compile(r'\[IMAGE:[^\]]+\]', re.IGNORECASE)

# Gemma 4 leaks its chain-of-thought as plain sentences before the real reply.
# These markers identify reasoning sentences that must be dropped.
_REASONING_RE = re.compile(
    r'^(the user\b|i need to\b|i must\b|i will\b|i should\b|since i\b|'
    r'i am going to\b|i have to\b|this means\b|as instructed\b|'
    r'my (response|persona|role|task|goal|directive|instruction|primary|job)\b|'
    r'the (system|instructions?|prompt|rules|guidelines)\b|'
    r'looking at (this|the)|given that\b|in this case\b|'
    # "This is another greeting / similar / a repeat..."
    r'this is (another|a (greeting|repeat|continuation|follow.?up)|similar|the same)\b|'
    # "Since they/the user/the child haven't..."
    r'since (they|the (user|child|kid)|he|she|we)\b|'
    # meta-observation about what the user said / is asking
    r'they (are|have|seem|want|need|said|asked|haven\'t|haven.t)\b|'
    # "Based on..." / situational reasoning openers
    r'based on\b|in this situation\b|'
    # structured thinking headers (e.g. "Thinking Process:", "Thinking:")
    r'thinking (process|steps?|mode|through)\b|thinking:\s|'
    # markdown formatting — never appears in spoken output (*, **, *_, ##, etc.)
    r'\*|#{1,6}\s|'
    # colon-labelled planning / meta markers  (e.g. "Plan:", "Step 1:", "Action:")
    r'plan[:\s]|step[\s:]\d|action:|thought:|response:|output:|'
    # transitional reasoning openers
    r'first,?\s+i\b|next,?\s+i\b|finally,?\s+i\b|'
    r'\d+\.\s)',  # numbered list items
    re.IGNORECASE,
)

# Patterns that are ALWAYS reasoning regardless of position — these reference the
# bot's own internal state and can never appear in a real child-directed response.
# Filtered even after past_reasoning is True to catch mid-stream leaks.
_META_REASONING_RE = re.compile(
    r'^(my (response|persona|role|task|goal|directive|instruction|primary|job)\b|'
    r'the (system|instructions?|prompt|rules|guidelines)\b|'
    r'as (instructed|per (the|my|our))\b|'
    r'i (need to|will|should|must) (keep|maintain|ensure|make sure|remember|note)\b|'
    # markdown formatting is never valid in spoken output — always filter it
    r'\*|#{1,6}\s)',
    re.IGNORECASE,
)

_MIN_SENTENCE_LEN = 8


def _strip_reasoning(text: str) -> str:
    """Remove chain-of-thought preamble that Gemma 4 prepends to responses."""
    sentences = [s.strip() for s in _SENT_BOUNDARY.split(text) if s.strip()]
    for i, sent in enumerate(sentences):
        if not _REASONING_RE.match(sent):
            result = " ".join(sentences[i:])
            if result != text:
                logger.debug("Stripped %d reasoning sentence(s) from reply.", i)
            return result
    return text  # fallback: nothing matched, return as-is


class LLMInterface:
    def __init__(self):
        self.client = OpenAI(base_url=LM_STUDIO_BASE_URL, api_key="lm-studio")
        try:
            models = [m.id for m in self.client.models.list().data]
            if not any(LM_STUDIO_MODEL in m for m in models):
                logger.warning(
                    "Model %r not found in LM Studio. Loaded: %s — "
                    "make sure the model is loaded in LM Studio.",
                    LM_STUDIO_MODEL, models,
                )
            else:
                logger.info("LLM ready. Model: %s  Base URL: %s", LM_STUDIO_MODEL, LM_STUDIO_BASE_URL)
        except Exception as exc:
            logger.warning("Could not reach LM Studio at %s: %s", LM_STUDIO_BASE_URL, exc)

    def _build_messages(self, user_text: str, history: list | None) -> list:
        messages = [{"role": "system", "content": get_system_prompt()}]
        if history:
            # Each exchange is 2 messages (user + assistant). Keep the most
            # recent N exchanges so the prompt never crowds out the response.
            max_msgs = LLM_MAX_HISTORY_EXCHANGES * 2
            trimmed = history[-max_msgs:] if len(history) > max_msgs else history
            if len(trimmed) < len(history):
                logger.debug(
                    "History trimmed from %d to %d messages (max %d exchanges).",
                    len(history), len(trimmed), LLM_MAX_HISTORY_EXCHANGES,
                )
            messages.extend(trimmed)
        messages.append({"role": "user", "content": user_text})
        return messages

    def respond(self, user_text: str, history: list | None = None) -> str:
        if not is_input_safe(user_text):
            logger.warning("Blocked input: %r", user_text)
            return REDIRECT_RESPONSE

        try:
            response = self.client.chat.completions.create(
                model=LM_STUDIO_MODEL,
                messages=self._build_messages(user_text, history),
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
            )
            raw = response.choices[0].message.content or ""
            reply = _strip_reasoning(_THINK_RE.sub("", raw).strip())
        except Exception as exc:
            logger.error("LM Studio request failed: %s", exc)
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

        try:
            stream = self.client.chat.completions.create(
                model=LM_STUDIO_MODEL,
                messages=self._build_messages(user_text, history),
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
                stream=True,
            )
        except Exception as exc:
            logger.error("LM Studio stream request failed: %s", exc)
            yield OUTPUT_BLOCKED_RESPONSE
            return

        buffer = ""
        in_think = False
        past_reasoning = False  # True once we've yielded the first real sentence

        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if not delta:
                continue
            buffer += delta

            # Suppress tagged <think> blocks across chunk boundaries.
            if '<think>' in buffer.lower():
                in_think = True
            if in_think:
                if '</think>' in buffer.lower():
                    buffer = _THINK_RE.sub("", buffer).strip()
                    in_think = False
                else:
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

                # Drop reasoning sentences.
                # _META_REASONING_RE is always checked (can never be real output).
                # _REASONING_RE is only checked before the first real sentence.
                is_reasoning = (
                    _META_REASONING_RE.match(sentence)
                    or (not past_reasoning and _REASONING_RE.match(sentence))
                )
                if is_reasoning:
                    logger.debug("Skipping reasoning sentence: %r", sentence)
                    # Still rescue any [IMAGE: ...] tag embedded in the
                    # reasoning so the server can trigger an image search.
                    img = _IMAGE_TAG_RE.search(sentence)
                    if img:
                        logger.info("Rescued image tag from reasoning: %r", img.group(0))
                        yield img.group(0)
                    continue

                past_reasoning = True
                safe, reason = is_output_safe(sentence)
                if not safe:
                    logger.warning("Stream output blocked — %s", reason)
                    yield OUTPUT_BLOCKED_RESPONSE
                    return
                logger.info("LLM stream sentence: %r", sentence)
                yield sentence

        if buffer.strip():
            remaining = buffer.strip()
            # Strip any trailing reasoning if we never found a real sentence
            if not past_reasoning:
                remaining = _strip_reasoning(remaining)
            safe, reason = is_output_safe(remaining)
            if safe:
                logger.info("LLM stream final: %r", remaining)
                yield remaining
            else:
                logger.warning("Stream final output blocked — %s", reason)
                yield OUTPUT_BLOCKED_RESPONSE
