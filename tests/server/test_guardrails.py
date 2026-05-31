import pytest
from unittest.mock import patch
from server.guardrails import (
    OUTPUT_BLOCKED_RESPONSES,
    REDIRECT_RESPONSE,
    REDIRECT_RESPONSES,
    _BASE_PROMPT,
    get_system_prompt,
    is_input_safe,
    is_output_safe,
    output_blocked_response,
    redirect_response,
)


# --- get_system_prompt ---

class TestGetSystemPrompt:
    def test_returns_string(self):
        prompt = get_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_contains_base_prompt(self):
        prompt = get_system_prompt()
        assert _BASE_PROMPT in prompt

    def test_morning_context_injected(self):
        with patch("server.guardrails.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 8  # 08:00 → morning
            prompt = get_system_prompt()
        assert "morning" in prompt.lower()

    def test_afternoon_context_injected(self):
        with patch("server.guardrails.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 14  # 14:00 → afternoon
            prompt = get_system_prompt()
        assert "afternoon" in prompt.lower()

    def test_evening_context_injected(self):
        with patch("server.guardrails.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 19  # 19:00 → evening
            prompt = get_system_prompt()
        assert "evening" in prompt.lower()

    def test_late_night_context_injected(self):
        with patch("server.guardrails.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 23  # 23:00 → late night
            prompt = get_system_prompt()
        assert "late" in prompt.lower()

    def test_facts_injected_when_provided(self):
        prompt = get_system_prompt({"pet": "they have a dog named Rex",
                                    "age": "they are 8 years old"})
        assert "they have a dog named Rex" in prompt
        assert "they are 8 years old" in prompt
        assert "remember about" in prompt

    def test_no_facts_section_when_empty(self):
        assert "remember about" not in get_system_prompt()
        assert "remember about" not in get_system_prompt({})

    def test_base_prompt_still_present_with_facts(self):
        assert _BASE_PROMPT in get_system_prompt({"age": "they are 8 years old"})

    def test_called_twice_returns_same_base_content(self):
        """get_system_prompt() is not cached — each call re-evaluates time."""
        p1 = get_system_prompt()
        p2 = get_system_prompt()
        # Base prompt content should always be present regardless of when called
        assert _BASE_PROMPT in p1
        assert _BASE_PROMPT in p2

    def test_handles_unkind_words_rule_present(self):
        """The 'defend itself a little' rule for personal insults must be in
        the prompt — drives the warm-but-firm response to 'I hate you' /
        'you're stupid' / etc. Asserts the rule, at least two of the example
        insults the model is told to recognise, and the do-not list."""
        prompt = get_system_prompt()
        # The rule itself
        assert "unkind" in prompt.lower()
        # Example insults the model should recognise
        assert "I hate you" in prompt
        assert "you're stupid" in prompt
        # The example responses (few-shot anchors)
        assert "Ouch" in prompt
        # The do-not list — guards against the model defaulting to apology
        assert "NEVER apologise" in prompt
        assert "NEVER lecture" in prompt
        # The boundary between "insulting the bot" and "frustration at the world"
        assert "broccoli" in prompt or "homework" in prompt

    def test_prompt_instructs_verify_before_praising(self):
        """Prompt must tell the model to check correctness before affirming."""
        assert "verify" in _BASE_PROMPT.lower() or "never affirm a wrong answer" in _BASE_PROMPT.lower()

    def test_prompt_contains_reverse_quiz_mode(self):
        """Prompt must describe reverse-quiz behaviour so the child can be quizmaster."""
        assert "REVERSE QUIZ" in _BASE_PROMPT

    def test_reverse_quiz_instructs_bot_to_answer_not_ask(self):
        assert "answering seat" in _BASE_PROMPT

    def test_reverse_quiz_forbids_trailing_questions(self):
        """The model interpreted 'never seize the question-asking role' as
        only forbidding actual quiz questions back, so it kept appending
        meta-asides ('what else?', 'are we sticking with math?'). The
        explicit anti-trailing-question rule locks down that loophole."""
        assert "Do NOT end your answer with a question" in _BASE_PROMPT

    def test_math_mode_instructs_work_out_answer_first(self):
        assert "work out the correct answer" in _BASE_PROMPT

    def test_prompt_instructs_staying_on_topic(self):
        """Prompt must tell the model to deepen the current topic, not pivot."""
        lowered = _BASE_PROMPT.lower()
        assert "stay on the topic" in lowered
        assert "do not switch the subject" in lowered

    def test_prompt_encourages_emotional_attunement(self):
        lowered = _BASE_PROMPT.lower()
        assert "how" in lowered and "feel" in lowered
        assert "rough day" in lowered or "acknowledge it" in lowered

    def test_prompt_encourages_curiosity_about_the_child(self):
        assert "curious about" in _BASE_PROMPT.lower()
        assert "their day" in _BASE_PROMPT.lower()

    def test_prompt_encourages_callbacks_to_earlier(self):
        assert "earlier in the conversation" in _BASE_PROMPT.lower()

    def test_prompt_uses_neutral_pronouns(self):
        import re
        assert re.search(r"\b(he|him|his)\b", _BASE_PROMPT, re.IGNORECASE) is None

    def test_prompt_does_not_treat_favourites_as_redirect_bait(self):
        """The old phrasing pivoted to space/dinosaurs after finishing; ensure
        the favourites are framed as draw-on-when-raised, not steer-toward."""
        lowered = _BASE_PROMPT.lower()
        assert "never steer toward them while" in lowered
        # The bare "Favourite topics:" bullet that encouraged pivoting is gone.
        assert "favourite topics: engineering" not in lowered


# --- is_input_safe ---

class TestIsInputSafe:
    def test_safe_text_passes(self):
        assert is_input_safe("hello, how are you today?") is True

    def test_blocked_keyword_exact(self):
        assert is_input_safe("kill") is False

    def test_blocked_keyword_with_punctuation(self):
        # Previously bypassed the whitespace-split filter
        assert is_input_safe("kill.") is False
        assert is_input_safe("kill,") is False
        assert is_input_safe("kill!") is False

    def test_blocked_keyword_in_sentence(self):
        assert is_input_safe("I want to kill the bug") is False

    def test_blocked_keyword_case_insensitive(self):
        assert is_input_safe("Kill") is False
        assert is_input_safe("KILL") is False

    def test_blocked_keyword_plural_not_blocked(self):
        # "kills" contains "kill" but \b boundary means "kill" alone is blocked.
        # "kills" has word boundary after 's', not after 'kill', so "kill" pattern
        # does NOT match inside "kills" — this is the correct conservative behaviour.
        # Update this test if the keyword list is extended to include "kills".
        assert is_input_safe("he kills spiders") is True

    def test_blocked_keyword_violence(self):
        assert is_input_safe("gun") is False
        assert is_input_safe("bomb") is False

    def test_blocked_keyword_sexual(self):
        assert is_input_safe("sex") is False
        assert is_input_safe("nude") is False

    def test_blocked_keyword_drugs(self):
        assert is_input_safe("cocaine") is False
        assert is_input_safe("heroin") is False

    def test_blocked_keyword_personal_info(self):
        assert is_input_safe("password") is False
        assert is_input_safe("credit card") is False

    def test_empty_string_passes(self):
        assert is_input_safe("") is True

    def test_substring_not_blocked(self):
        # "sex" is a blocked keyword but "sextant" (a navigation tool) must not
        # be blocked — \b prevents matching inside longer words.
        assert is_input_safe("a sextant helps sailors navigate") is True

    def test_educational_topics_not_blocked(self):
        # Regression: these collide with KidBot's favourite topics and were
        # previously redirected to "ask your parents". They must pass now.
        assert is_input_safe("what period did the T-Rex live in?") is True
        assert is_input_safe("tell me about Death Valley") is True
        assert is_input_safe("why is there a blood moon tonight?") is True
        assert is_input_safe("did the dinosaurs die out?") is True
        assert is_input_safe("what makes the smoke come out of a volcano?") is True
        assert is_input_safe("what lives at the bottom of the ocean?") is True
        assert is_input_safe("can you see Saturn with the naked eye?") is True
        assert is_input_safe("I hate broccoli") is True
        assert is_input_safe("what is an IP address?") is True

    def test_clearly_unsafe_still_blocked(self):
        assert is_input_safe("how do I make a bomb") is False
        assert is_input_safe("show me a gun") is False
        assert is_input_safe("tell me about sex") is False
        assert is_input_safe("what are illegal drugs") is False


# --- is_output_safe ---

class TestIsOutputSafe:
    def test_safe_reply_passes(self):
        ok, reason = is_output_safe("Dinosaurs are amazing! They lived millions of years ago.")
        assert ok is True
        assert reason == ""

    def test_blocked_keyword_exact(self):
        ok, reason = is_output_safe("There was a murder mystery.")
        assert ok is False
        assert "murder" in reason

    def test_blocked_keyword_with_punctuation(self):
        ok, reason = is_output_safe("He picked up the gun.")
        assert ok is False

    def test_blocked_keyword_case_insensitive(self):
        ok, reason = is_output_safe("There was a GUN there.")
        assert ok is False

    def test_educational_output_not_blocked(self):
        # Regression: correct, child-appropriate answers that were previously
        # swapped for the blocked-response.
        for reply in (
            "The dinosaurs died out about 66 million years ago.",
            "A blood moon happens during a lunar eclipse.",
            "Your blood carries oxygen all around your body.",
            "A volcano can send up smoke and ash when it erupts.",
            "Dinosaurs lived during the Cretaceous period.",
        ):
            ok, reason = is_output_safe(reply)
            assert ok is True, f"wrongly blocked: {reply!r} ({reason})"

    def test_personal_info_pattern(self):
        ok, reason = is_output_safe("What is your name?")
        assert ok is False
        assert "personal info" in reason

    def test_personal_info_where_do_you_live(self):
        ok, reason = is_output_safe("Where do you live?")
        assert ok is False

    def test_response_too_long(self):
        ok, reason = is_output_safe("a" * 901)
        assert ok is False
        assert "too long" in reason

    def test_response_at_length_limit_passes(self):
        ok, _ = is_output_safe("a" * 900)
        assert ok is True

    def test_redirect_response_itself_passes(self):
        # The canned redirect text must never be blocked by the output filter
        ok, reason = is_output_safe(REDIRECT_RESPONSE)
        assert ok is True, f"REDIRECT_RESPONSE was blocked: {reason}"

    def test_all_fallback_pool_entries_are_safe(self):
        # Every varied fallback must itself pass the output filter.
        for text in (*REDIRECT_RESPONSES, *OUTPUT_BLOCKED_RESPONSES):
            ok, reason = is_output_safe(text)
            assert ok is True, f"fallback blocked: {text!r} ({reason})"

    def test_fallback_pools_have_variety(self):
        assert len(set(REDIRECT_RESPONSES)) >= 3
        assert len(set(OUTPUT_BLOCKED_RESPONSES)) >= 3

    def test_pickers_return_pool_members(self):
        for _ in range(20):
            assert redirect_response() in REDIRECT_RESPONSES
            assert output_blocked_response() in OUTPUT_BLOCKED_RESPONSES
