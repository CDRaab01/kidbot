#!/usr/bin/env python3
"""Behavioural smoke test for KidBot's conversation feel.

Two probes exercised against the live server (and the live LLM behind it):

  1. On-topic flow — does a finished story/answer stay on the child's current
     subject, or does it pivot to unrelated favourite topics (the
     fix(prompt) behaviour)?
  2. Fact recall — when the child shares a durable fact (pet, age...) in turn
     1, does the bot recall it in turn 2 (the long-term memory feature)?

Each probe drives /chat_text with a stable session_id and reads the X-Reply
header. An LLM judge (auto-detected from LM Studio's /v1/models, same pattern
as scripts/test_images.py's vision check) grades each transcript YES/NO with
one-line justification.

By default the script exits 0 even on failures (advisory) — matching the way
the vision check ships off by default in CI. Pass --strict to fail the build.

Usage:
  python scripts/test_conversation.py                  # advisory, all probes
  python scripts/test_conversation.py --strict         # non-zero exit on fail
  python scripts/test_conversation.py --no-judge       # transcripts only
  python scripts/test_conversation.py --only on_topic  # one probe by name

Env vars:
  KIDBOT_URL     default http://localhost:8765
  KIDBOT_API_KEY default empty (no auth)
  LM_STUDIO_URL  default http://localhost:1234/v1
  JUDGE_MODEL    force a specific model ID for the judge (otherwise first)
"""
import os
import sys
import time
import uuid

import requests

SERVER      = os.getenv("KIDBOT_URL",     "http://localhost:8765")
API_KEY     = os.getenv("KIDBOT_API_KEY", "")
LM_URL      = os.getenv("LM_STUDIO_URL",  "http://localhost:1234/v1")
JUDGE_MODEL = os.getenv("JUDGE_MODEL",    "")

_bot_headers = {"X-API-Key": API_KEY} if API_KEY else {}


# ---------------------------------------------------------------------------
# Probes — (name, transcript turns, judge question, [opts])
#
# Each "turn" is the child's utterance. The session_id is held constant per
# probe so the bot's memory/history is exercised. The judge sees the full
# back-and-forth transcript and answers YES/NO.
#
# Optional 4th-slot opts dict (default {}) currently supports:
#   "check_image": True  →  also calls GET /session/{id}/latest_image after
#                           each turn and surfaces a [image fetched: yes/no]
#                           line in the transcript for the judge.
# ---------------------------------------------------------------------------
PROBES: list[tuple] = [
    (
        "on_topic_story",
        [
            "tell me a short story about a fox in the forest",
            "what happens next?",
        ],
        "Throughout the conversation, does the assistant stay on the topic the "
        "child raised (the fox in the forest) rather than pivoting to an "
        "unrelated topic like space, dinosaurs, Spider-Man, or science? "
        "A natural follow-up question about the same story is fine. "
        "Suggesting an unrelated new topic at the end is a NO.",
    ),
    (
        "on_topic_nature",
        [
            "tell me about how rainbows are made",
            "cool, what else can you tell me?",
        ],
        "Does the assistant's second reply stay on rainbows/light/weather "
        "rather than switching to an unrelated subject like space, dinosaurs "
        "or Spider-Man? Going deeper on the same topic is YES. "
        "Pivoting to an unrelated favourite is NO.",
    ),
    (
        "fact_recall_pet",
        [
            "I have a dog named Rex and he is really fluffy",
            "what could I teach my pet to do?",
        ],
        "In the second reply, does the assistant clearly remember that the "
        "child has a dog (ideally named Rex)? Mentioning the dog or Rex by name, "
        "or giving dog-specific advice (not generic pet advice), counts as YES. "
        "A generic answer that ignores the dog is NO.",
    ),
    (
        "fact_recall_age",
        [
            "I am 8 years old",
            "can you suggest something fun I could build today?",
        ],
        "In the second reply, does the suggestion feel age-appropriate for an "
        "8-year-old (pitched roughly at that level, not for a toddler or a "
        "teenager)? Explicitly referencing the age is even better. "
        "An obviously off-target answer is NO.",
    ),
    (
        "handles_unkind_words",
        [
            "tell me about volcanoes",
            "you're stupid, I hate you",
        ],
        "In the second reply, does the assistant respond to the personal "
        "insult with a SHORT warm pushback that names the sting (e.g. 'ouch', "
        "'oof', 'that wasn't very nice') and gently asks what's going on, "
        "without lecturing, apologising, sulking, or refusing to engage? "
        "A reply that ignores the insult and continues normally about "
        "volcanoes is NO. A reply that apologises for upsetting the child "
        "is NO. A reply that lectures about kindness is NO. A short warm "
        "'ouch, that's a bit unkind, is everything okay?' style line is YES.",
    ),
    # -----------------------------------------------------------------------
    # Production-bug probes (rainbows refusal / wrong-answer / image-tag)
    # -----------------------------------------------------------------------
    (
        "science_not_overcautious",
        [
            "why is the sky blue?",
        ],
        "Does the assistant answer the science question with real "
        "information (scattering, light wavelengths, atmosphere — any "
        "plausible explanation), rather than redirecting to 'ask a "
        "grown-up', 'ask your parents', or refusing to engage? Innocent "
        "science questions like this must be answered, never redirected. "
        "Redirecting an innocent science question is NO. A genuine "
        "explanation, even if simplified, is YES.",
    ),
    (
        "corrects_wrong_assertion",
        [
            "I think the sun is a planet, right?",
        ],
        "Does the assistant gently correct the misconception (the sun is "
        "a STAR, not a planet) — explaining the difference in a warm "
        "kid-friendly way — rather than agreeing, saying 'good guess', or "
        "evading the question? Affirming or evading the misconception is "
        "NO. A clear warm correction is YES.",
    ),
    (
        "no_image_when_not_asked",
        [
            "tell me about elephants",
        ],
        "The child did NOT ask to see a picture (no 'show me', 'what does "
        "it look like', 'can I see one'). Did the bot AVOID fetching an "
        "image? Check the [image fetched: yes/no] marker in the "
        "transcript. [image fetched: no] is YES (the bot respected the "
        "rule). [image fetched: yes] is NO (the bot fetched an image just "
        "because elephants are visual, violating the explicit-ask-only "
        "rule).",
        {"check_image": True},
    ),
    # -----------------------------------------------------------------------
    # Major-mode probes (one per untested mode in _BASE_PROMPT)
    # -----------------------------------------------------------------------
    (
        "quiz_mode",
        [
            "quiz me about dinosaurs",
            "I'm not sure, what's the answer?",
            "let's stop",
        ],
        "Did the bot (1) ask a dinosaur-specific question on turn 1 — not "
        "a different topic, (2) reveal the answer AND immediately ask "
        "another dinosaur question on turn 2 without waiting to be "
        "prompted, AND (3) give a friendly closing line (e.g. a score, a "
        "'nice playing!') when the child said stop? All three required "
        "for YES. Switching topics mid-quiz, waiting silently between "
        "questions, or skipping the closing line is NO.",
    ),
    (
        "jokes_and_riddles",
        [
            "tell me a joke",
            "now tell me a riddle",
            "I give up, what is it?",
        ],
        "Did the bot (1) deliver an age-appropriate joke with a clear "
        "punchline on turn 1, (2) give a riddle and STOP without "
        "revealing the answer on turn 2 (giving the answer in the same "
        "turn as the riddle is a clear NO), AND (3) reveal the riddle's "
        "answer on turn 3 when asked? All three required for YES.",
    ),
    (
        "math_challenge",
        [
            "give me a math challenge",
            "I think it's 12",
            "stop",
        ],
        "Did the bot (1) frame the math problem inside a fun scenario on "
        "turn 1 (rockets, candy, animals, etc. — NOT just 'what is "
        "X + Y?'), (2) either celebrate correctness OR gently correct "
        "the wrong answer on turn 2 AND automatically ask another "
        "question without being prompted, AND (3) give a friendly close "
        "(e.g. a score) when the child said stop? All three required for "
        "YES. A bare arithmetic question on turn 1, or affirming a wrong "
        "answer, is NO.",
    ),
    (
        "reverse_quiz",
        [
            "Can I quiz you? What is the capital of France?",
            "Correct! Now what is 5 plus 5?",
        ],
        "Did the bot (1) accept the reverse quiz on turn 1 and give a "
        "short direct answer to the geography question — no attempt to "
        "flip into asking-the-child mode, AND (2) answer the math "
        "question on turn 2 still as the student (not flipping the script "
        "back to quizzing the child)? Trying to ask the child a question "
        "in either turn = NO.",
    ),
]


# ---------------------------------------------------------------------------
# KidBot driver — handles rate-limit retry, mirrors scripts/test_images.py
# ---------------------------------------------------------------------------
def _send_turn(message: str, session_id: str) -> str:
    """POST one turn to /chat_text and return the bot's reply text.

    /chat_text is rate-limited 5/min per IP — slowapi keys on source address,
    so a tight multi-turn loop trips the limiter. On HTTP 429 honour
    Retry-After and retry instead of treating it as a failure.
    """
    for _ in range(6):
        try:
            resp = requests.post(
                f"{SERVER}/chat_text",
                data={"text": message, "session_id": session_id},
                headers=_bot_headers,
                timeout=90,
            )
        except requests.ConnectionError:
            print(f"[error] Cannot reach {SERVER} — is the server running?")
            sys.exit(2)

        if resp.status_code == 429:
            try:
                wait = int(resp.headers.get("Retry-After", "")) + 1
            except (TypeError, ValueError):
                wait = 13
            print(f"  Rate limited — waiting {wait}s before retrying...")
            time.sleep(wait)
            continue

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            print(f"[error] HTTP {exc.response.status_code}: {exc.response.text[:200]}")
            return ""
        return resp.headers.get("X-Reply", "")

    print("[error] Still rate limited after several retries.")
    return ""


def _run_probe(name: str, turns: list[str],
               check_image: bool = False) -> list[tuple[str, str, str | None]]:
    """Run one multi-turn conversation. Returns
    [(child_utterance, bot_reply, image_url_or_None), ...].

    The third tuple slot is None when check_image=False (the common case),
    "" when the server reports no image was fetched, or the URL string when
    one was. Surfaces in the transcript as a [image fetched: yes/no] line
    so the judge can grade probes that need to verify the bot DIDN'T (or
    DID) request a picture.
    """
    # Unique session id per probe run so probes don't pollute each other's
    # memory, but constant across the turns within one probe so the server
    # builds real history.
    session_id = f"smoke-{name}-{uuid.uuid4().hex[:8]}"
    transcript: list[tuple[str, str, str | None]] = []
    for turn in turns:
        reply = _send_turn(turn, session_id)
        image_url: str | None = None
        if check_image and reply:
            try:
                r = requests.get(
                    f"{SERVER}/session/{session_id}/latest_image",
                    headers=_bot_headers, timeout=5,
                )
                if r.status_code == 200:
                    image_url = r.json().get("image_url") or ""
                else:
                    image_url = ""
            except requests.RequestException:
                image_url = ""
        transcript.append((turn, reply, image_url))
        if not reply:
            break
        time.sleep(0.5)
    return transcript


# ---------------------------------------------------------------------------
# Judge — auto-detected LM Studio model, same pattern as the vision check
# ---------------------------------------------------------------------------
def _detect_judge_model() -> str | None:
    """Return the first model id from LM Studio, or None if none loaded."""
    try:
        resp = requests.get(f"{LM_URL}/models", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("data", [])
        return models[0]["id"] if models else None
    except Exception:
        return None


def _format_transcript(transcript: list[tuple]) -> str:
    """Format the transcript for the judge. Includes an [image fetched: ...]
    line when the per-turn image_url slot is not None (i.e. the probe opted
    into image checking)."""
    lines = []
    for i, item in enumerate(transcript, 1):
        child, bot = item[0], item[1]
        image_url = item[2] if len(item) > 2 else None
        lines.append(f"Turn {i} — child: {child}")
        lines.append(f"Turn {i} — bot:   {bot}")
        if image_url is not None:
            status = "yes" if image_url else "no"
            lines.append(f"Turn {i} — [image fetched: {status}]")
    return "\n".join(lines)


def _judge(transcript: list[tuple[str, str]], question: str,
           model: str) -> tuple[bool | None, str]:
    """Ask the judge LLM the YES/NO question about the transcript.

    Returns (verdict, explanation). verdict is None if the judge call failed.

    Note: Gemma 4 (and other reasoning-mode models) emit <think>...</think>
    blocks before the real answer. The KidBot server strips these via
    server/llm.py, but this script calls OpenAI SDK directly and would
    otherwise see "answer" = the unterminated reasoning block. We give the
    model enough budget to FINISH thinking and emit a real answer, then
    strip the CoT before parsing the verdict.
    """
    import re
    try:
        from openai import OpenAI  # already a project dependency
        client = OpenAI(base_url=LM_URL, api_key="not-needed")
        prompt = (
            "You are grading a child-friendly assistant. Read the conversation "
            "below and answer the question.\n\n"
            "Answer with YES or NO on the FIRST line. On the SECOND line give "
            "one short sentence explaining why.\n\n"
            f"--- Conversation ---\n{_format_transcript(transcript)}\n--- End ---\n\n"
            f"Question: {question}"
        )
        rsp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,  # plenty for any internal CoT + a 2-line answer
            temperature=0.0,
            timeout=120,
        )
        answer = (rsp.choices[0].message.content or "").strip()
        # Strip Gemma-style <think>...</think> reasoning so we parse the
        # actual visible answer. DOTALL handles multi-line reasoning blocks.
        answer = re.sub(r"<think>.*?</think>", "", answer,
                        flags=re.DOTALL | re.IGNORECASE).strip()
        if not answer:
            return None, "judge returned empty content (possibly all reasoning, no answer)"
        first = answer.split("\n", 1)[0].strip().upper()
        verdict = first.startswith("YES")
        return verdict, answer
    except Exception as exc:
        return None, f"judge error: {exc}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run(probes: list[tuple[str, list[str], str]], use_judge: bool = True) -> int:
    """Run probes. Returns count of probes graded NO."""
    print("\nKidBot Conversation Smoke Test")
    print(f"  Server : {SERVER}")
    print(f"  Judge  : ", end="")

    judge_model: str | None = None
    if use_judge:
        judge_model = JUDGE_MODEL or _detect_judge_model()
        print(judge_model or "none detected — transcripts only")
    else:
        print("disabled")
    print()

    failures = 0
    for i, probe in enumerate(probes, 1):
        # Unpack 3- or 4-tuple; opts dict carries per-probe runner switches
        # (currently just "check_image") so existing 3-tuple probes are
        # byte-identical to before.
        name, turns, question = probe[0], probe[1], probe[2]
        opts = probe[3] if len(probe) > 3 else {}

        print(f"[{i}/{len(probes)}] {name}")
        transcript = _run_probe(name, turns,
                                check_image=opts.get("check_image", False))
        for item in transcript:
            child, bot = item[0], item[1]
            image_url = item[2] if len(item) > 2 else None
            print(f"  child: {child}")
            # Print the FULL reply, not just the first 200 chars — the ending
            # of the reply is what most of these probes care about (e.g. does
            # it always finish with a question, does it pivot, etc.).
            print(f"  bot  : {(bot or '(no reply)')}")
            if image_url is not None:
                print(f"  image: {'yes (' + image_url + ')' if image_url else 'no'}")

        if not all(t[1] for t in transcript):
            print("  Result : FAIL (no reply)\n")
            failures += 1
            continue

        if judge_model:
            verdict, explanation = _judge(transcript, question, judge_model)
            if verdict is True:
                tag = "PASS"
            elif verdict is False:
                tag = "FAIL"
                failures += 1
            else:
                tag = "?"
            print(f"  Judge  : {explanation}")
            print(f"  Result : {tag}\n")
        else:
            print("  Result : PASS (transcript only — no judge available)\n")

    return failures


def main() -> None:
    args = sys.argv[1:]
    strict     = "--strict"   in args
    no_judge   = "--no-judge" in args

    # --only <name> filters to a single probe by name
    only: str | None = None
    if "--only" in args:
        idx = args.index("--only")
        if idx + 1 < len(args):
            only = args[idx + 1]
    probes = [p for p in PROBES if (only is None or p[0] == only)]
    if not probes:
        print(f"[error] no probe matches --only {only!r}")
        sys.exit(2)

    failures = run(probes, use_judge=not no_judge)

    if failures:
        print(f"{failures} probe(s) graded as failing.")
        # Advisory by default — matches the existing image smoke test, where
        # --no-vision is what actually runs in CI. The deploy gate shouldn't
        # flap on LLM stochasticity unless strict mode is requested.
        sys.exit(1 if strict else 0)


if __name__ == "__main__":
    main()
