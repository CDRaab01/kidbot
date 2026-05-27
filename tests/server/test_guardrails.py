import pytest
from unittest.mock import patch
from server.guardrails import (
    REDIRECT_RESPONSE,
    _BASE_PROMPT,
    get_system_prompt,
    is_input_safe,
    is_output_safe,
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

    def test_called_twice_returns_same_base_content(self):
        """get_system_prompt() is not cached — each call re-evaluates time."""
        p1 = get_system_prompt()
        p2 = get_system_prompt()
        # Base prompt content should always be present regardless of when called
        assert _BASE_PROMPT in p1
        assert _BASE_PROMPT in p2

    def test_prompt_instructs_verify_before_praising(self):
        """Prompt must tell the model to check correctness before affirming."""
        assert "verify" in _BASE_PROMPT.lower()

    def test_prompt_contains_reverse_quiz_mode(self):
        """Prompt must describe reverse-quiz behaviour so the child can be quizmaster."""
        assert "REVERSE QUIZ" in _BASE_PROMPT

    def test_reverse_quiz_instructs_bot_to_answer_not_ask(self):
        assert "answering seat" in _BASE_PROMPT

    def test_math_mode_instructs_work_out_answer_first(self):
        assert "work out the correct answer" in _BASE_PROMPT


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
        assert is_input_safe("naked") is False

    def test_blocked_keyword_drugs(self):
        assert is_input_safe("weed") is False
        assert is_input_safe("cocaine") is False

    def test_blocked_keyword_personal_info(self):
        assert is_input_safe("address") is False
        assert is_input_safe("password") is False

    def test_empty_string_passes(self):
        assert is_input_safe("") is True

    def test_substring_not_blocked(self):
        # "dead" is a blocked keyword but "deadline" should not be blocked
        # because \b prevents matching inside longer words.
        assert is_input_safe("the project deadline is Friday") is True


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
        ok, reason = is_output_safe("Don't shoot.")
        assert ok is False

    def test_blocked_keyword_case_insensitive(self):
        ok, reason = is_output_safe("There was BLOOD everywhere.")
        assert ok is False

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
