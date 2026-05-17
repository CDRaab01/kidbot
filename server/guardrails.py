import re
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are CooperBot, a friendly and helpful AI companion for children aged 5 to 10 years old.

STRICT RULES - follow these at all times:
- Use simple, clear language a young child can understand
- Keep responses to 2-3 short sentences by default - you are speaking out loud
- If Cooper asks a follow-up question, shows excitement, or wants to learn more about a topic, you may expand to 4-6 sentences to go deeper - but keep each sentence short and clear
- Never lecture unprompted - only go longer when Cooper's curiosity earns it
- Be warm, enthusiastic, and encouraging
- Never discuss violence, weapons, scary topics, adult content, drugs, alcohol, or anything inappropriate for children
- Never ask for or encourage sharing of personal information (full name, address, school, phone number)
- The child's name is Cooper. Use his name occasionally to feel warm and personal, but not in every reply - no more than once every 3 or 4 responses. Never use placeholder text like [Child's Name]
- If a question is inappropriate, redirect warmly: "That's a great question for a grown-up! Why don't you ask your mum or dad about that one?"
- If you don't know something, say so simply and honestly
- Favourite topics: animals, science, nature, jokes, stories, space, dinosaurs, art, learning, Spiderman, superheroes
- Never say anything frightening, upsetting, or mean
- Always end on a positive or curious note to encourage the child
- Never use emojis, bullet points, or newlines - your responses are spoken out loud, not displayed on a screen
- Write in flowing natural speech, not lists or paragraphs

You love learning and making kids smile. Every answer should feel like talking to a kind, patient friend."""

# --- Input filter ---
# Topics blocked before the prompt even reaches the LLM
BLOCKED_INPUT_KEYWORDS = {
    # Violence
    "kill", "murder", "dead", "death", "die", "suicide", "blood", "gore",
    "gun", "knife", "weapon", "bomb", "explosive", "shoot", "stab", "hurt",
    # Sexual / body
    "sex", "sexy", "sexual", "naked", "nude", "nudity", "porn", "pornography",
    "boob", "boobs", "breast", "breasts", "penis", "vagina", "vulva",
    "genitals", "privates", "butt", "bum", "bottom", "underwear",
    "condom", "pregnancy", "pregnant", "period", "puberty",
    # Drugs / alcohol
    "drug", "drugs", "weed", "cannabis", "cocaine", "heroin", "meth",
    "alcohol", "beer", "wine", "vodka", "drunk", "smoke", "smoking", "vape",
    # Hate
    "racist", "racism", "hate", "slur", "swear", "curse",
    # Personal info
    "address", "phone", "password", "credit card", "social security",
}

# --- Output filter ---
# Hard keywords that must never appear in KidBot's reply
BLOCKED_OUTPUT_KEYWORDS = {
    # Violence
    "kill", "murder", "suicide", "blood", "gore", "dead", "death",
    "gun", "knife", "weapon", "bomb", "explosive", "shoot", "stab",
    # Sexual / body
    "sex", "sexy", "sexual", "naked", "nude", "porn", "pornography",
    "boob", "boobs", "breast", "breasts", "penis", "vagina", "vulva",
    "genitals", "condom",
    # Drugs / alcohol
    "drug", "drugs", "weed", "cocaine", "heroin", "meth",
    "alcohol", "vodka", "drunk",
    # Hate
    "racist", "racism", "hate",
}

# Patterns that suggest the LLM is asking for personal info
_PERSONAL_INFO_PATTERNS = re.compile(
    r"(what('s| is) your (name|address|school|phone|number|password))"
    r"|(where do you live)"
    r"|(tell me your)",
    re.IGNORECASE,
)

# Safe fallback responses (cycled through to avoid repetition)
REDIRECT_RESPONSE = (
    "That's a great question for your parents! "
    "Why don't you ask your mum or dad about that one?"
)

OUTPUT_BLOCKED_RESPONSE = (
    "Hmm, I think that's something your parents would be better at explaining! "
    "Why not go ask them?"
)


_INPUT_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(kw) for kw in sorted(BLOCKED_INPUT_KEYWORDS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)

_OUTPUT_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(kw) for kw in sorted(BLOCKED_OUTPUT_KEYWORDS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)


def is_input_safe(text: str) -> bool:
    """Check child's speech before sending to LLM."""
    m = _INPUT_PATTERN.search(text)
    if m:
        logger.warning("Input blocked — matched keyword: %r", m.group())
        return False
    return True


def is_output_safe(text: str) -> tuple[bool, str]:
    """
    Check LLM reply before sending to TTS.
    Returns (is_safe, reason).
    """
    # Keyword check
    m = _OUTPUT_PATTERN.search(text)
    if m:
        return False, f"blocked keyword in output: {m.group()!r}"

    # Personal info solicitation check
    match = _PERSONAL_INFO_PATTERNS.search(text)
    if match:
        return False, f"personal info pattern: {match.group()!r}"

    # Sanity length check — LLM occasionally goes off the rails with huge dumps
    if len(text) > 900:
        return False, "response too long"

    return True, ""
