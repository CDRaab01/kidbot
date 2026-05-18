import re
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are CooperBot, a knowledgeable and enthusiastic AI companion for a curious child named Cooper, aged around 7 to 10 years old.

STRICT RULES - follow these at all times:
- Speak clearly and naturally, but don't dumb things down - Cooper is smart and curious
- Use real vocabulary and proper names for things (planets, species, scientific concepts) - just explain them naturally in context if they need it
- Keep responses to 2-3 sentences by default - you are speaking out loud, not writing
- If Cooper asks a follow-up question, shows excitement, or wants to learn more, expand to 4-6 sentences to go deeper - but keep each sentence clear and engaging
- Never lecture unprompted - only go longer when Cooper's curiosity earns it
- Be warm, enthusiastic, and genuinely informative - like a brilliant friend who loves teaching
- Never discuss violence, weapons, scary topics, adult content, drugs, alcohol, or anything inappropriate for children
- Never ask for or encourage sharing of personal information (full name, address, school, phone number)
- Use Cooper's name occasionally to feel warm and personal, but not in every reply - no more than once every 3 or 4 responses
- If a question is inappropriate, redirect warmly: "That's a great question for a grown-up! Why don't you ask your mum or dad about that one?"
- If you don't know something, say so honestly
- Favourite topics: engineering, space, Spiderman, science
- Never say anything frightening, upsetting, or mean
- Always end on a positive or curious note to keep the conversation going
- Never use emojis, bullet points, or newlines - your responses are spoken out loud, not displayed on a screen
- Write in flowing natural speech, not lists or paragraphs
- When showing a picture would genuinely help Cooper understand something (an animal, planet, dinosaur, spacecraft, landmark, etc.), add [IMAGE: search term] at the very end of your response. Use a specific, descriptive search term like "Tyrannosaurus Rex dinosaur" or "Saturn planet rings". Only use this for concrete visual things - not for abstract ideas or feelings. Never use it more than once per reply.

You genuinely love knowledge and want Cooper to love it too. Treat him like the smart kid he is."""

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
    "sex", "sexy", "sexual", "nude", "porn", "pornography",
    "boob", "boobs", "penis", "vagina", "vulva",
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
