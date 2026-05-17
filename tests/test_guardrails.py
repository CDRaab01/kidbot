import pytest
from server.guardrails import (
    REDIRECT_RESPONSE,
    is_input_safe,
    is_output_safe,
)


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
