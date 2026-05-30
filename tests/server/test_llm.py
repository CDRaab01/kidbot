from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from server.guardrails import (OUTPUT_BLOCKED_RESPONSE, OUTPUT_BLOCKED_RESPONSES,
                               REDIRECT_RESPONSE, REDIRECT_RESPONSES)
from server.llm import LLMInterface


def _make_response(content: str):
    """Build a minimal mock matching openai ChatCompletion structure."""
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _make_stream_chunks(texts: list[str]):
    """Build mock streaming chunks matching openai stream format."""
    chunks = []
    for text in texts:
        delta = SimpleNamespace(content=text)
        choice = SimpleNamespace(delta=delta)
        chunks.append(SimpleNamespace(choices=[choice]))
    return iter(chunks)


@pytest.fixture
def mock_openai(monkeypatch):
    """Patch OpenAI client used by LLMInterface."""
    mock_client = MagicMock()
    # models.list() returns something with .data
    mock_client.models.list.return_value = MagicMock(
        data=[SimpleNamespace(id="google/gemma-4-e4b")]
    )
    with patch("server.llm.OpenAI", return_value=mock_client):
        yield mock_client


class TestLLMInterface:
    def test_safe_input_returns_llm_reply(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_response("Dinosaurs are awesome!")
        llm = LLMInterface()
        result = llm.respond("Tell me about dinosaurs")
        assert result == "Dinosaurs are awesome!"

    def test_unsafe_input_returns_redirect_without_calling_api(self, mock_openai):
        llm = LLMInterface()
        mock_openai.chat.completions.create.reset_mock()
        result = llm.respond("kill everyone")
        mock_openai.chat.completions.create.assert_not_called()
        assert result in REDIRECT_RESPONSES

    def test_blocked_output_returns_blocked_response(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_response("I will kill you.")
        llm = LLMInterface()
        result = llm.respond("Tell me something")
        assert result in OUTPUT_BLOCKED_RESPONSES

    def test_empty_reply_returns_blocked_response(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_response("   ")
        llm = LLMInterface()
        result = llm.respond("Hello")
        assert result in OUTPUT_BLOCKED_RESPONSES

    def test_api_exception_returns_blocked_response(self, mock_openai):
        mock_openai.chat.completions.create.side_effect = Exception("connection refused")
        llm = LLMInterface()
        result = llm.respond("Hello")
        assert result in OUTPUT_BLOCKED_RESPONSES

    def test_history_is_passed_to_api(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_response("Sure thing!")
        llm = LLMInterface()
        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        llm.respond("How are you?", history=history)
        call_messages = mock_openai.chat.completions.create.call_args[1]["messages"]
        # system prompt + 2 history + new user = 4
        assert len(call_messages) == 4
        assert call_messages[1] == {"role": "user", "content": "Hi"}
        assert call_messages[3] == {"role": "user", "content": "How are you?"}

    def test_system_prompt_evaluated_fresh_each_call(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_response("Great!")
        llm = LLMInterface()
        with patch("server.llm.get_system_prompt", return_value="PROMPT_A") as mock_gsp:
            llm.respond("Hello")
            llm.respond("Hello again")
        assert mock_gsp.call_count == 2

    def test_client_constructed_with_timeout(self):
        from server.config import LLM_TIMEOUT
        with patch("server.llm.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.models.list.return_value = MagicMock(
                data=[SimpleNamespace(id="google/gemma-4-e4b")]
            )
            LLMInterface()
        assert MockOpenAI.call_args.kwargs["timeout"] == LLM_TIMEOUT

    def test_model_not_found_logs_warning(self, mock_openai):
        mock_openai.models.list.return_value = MagicMock(
            data=[SimpleNamespace(id="some-other-model")]
        )
        import logging
        with patch.object(logging.getLogger("server.llm"), "warning") as mock_warn:
            LLMInterface()
        mock_warn.assert_called()


class TestLLMInterfaceStream:
    def test_stream_yields_complete_sentences(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_stream_chunks(
            ["Dino", "saurs are cool. ", "They were big!"]
        )
        llm = LLMInterface()
        sentences = list(llm.respond_stream("Tell me about dinosaurs"))
        assert "Dinosaurs are cool." in sentences
        assert "They were big!" in sentences

    def test_stream_unsafe_input_yields_redirect(self, mock_openai):
        llm = LLMInterface()
        mock_openai.chat.completions.create.reset_mock()
        sentences = list(llm.respond_stream("kill everyone"))
        assert len(sentences) == 1 and sentences[0] in REDIRECT_RESPONSES
        mock_openai.chat.completions.create.assert_not_called()

    def test_stream_unsafe_output_stops_and_yields_blocked(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_stream_chunks(
            ["Hello there. ", "I will kill you. ", "More text."]
        )
        llm = LLMInterface()
        sentences = list(llm.respond_stream("hi"))
        assert any(x in OUTPUT_BLOCKED_RESPONSES for x in sentences)
        assert sentences[-1] in OUTPUT_BLOCKED_RESPONSES

    def test_stream_short_fragment_merged_with_next(self, mock_openai):
        # "Hi." is only 3 chars — should be merged, not yielded alone
        mock_openai.chat.completions.create.return_value = _make_stream_chunks(
            ["Hi. ", "How are you doing today?"]
        )
        llm = LLMInterface()
        sentences = list(llm.respond_stream("hello"))
        # "Hi." alone should not appear as a separate yielded sentence
        assert all(s.strip() != "Hi." for s in sentences)

    def test_stream_flushes_remainder_without_trailing_punctuation(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_stream_chunks(
            ["Cats are great pets"]
        )
        llm = LLMInterface()
        sentences = list(llm.respond_stream("tell me about cats"))
        assert any("Cats are great" in s for s in sentences)

    def test_stream_passes_history_to_api(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_stream_chunks(["Sure thing!"])
        llm = LLMInterface()
        history = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}]
        list(llm.respond_stream("How are you?", history=history))
        call_messages = mock_openai.chat.completions.create.call_args[1]["messages"]
        assert len(call_messages) == 4

    def test_stream_api_exception_yields_blocked(self, mock_openai):
        mock_openai.chat.completions.create.side_effect = Exception("timeout")
        llm = LLMInterface()
        sentences = list(llm.respond_stream("hello"))
        assert len(sentences) == 1 and sentences[0] in OUTPUT_BLOCKED_RESPONSES

    def test_stream_skips_none_delta_content(self, mock_openai):
        # OpenAI streams sometimes emit chunks with None content (role-only chunks)
        chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello there!"))]),
        ]
        mock_openai.chat.completions.create.return_value = iter(chunks)
        llm = LLMInterface()
        sentences = list(llm.respond_stream("hi"))
        assert any("Hello" in s for s in sentences)


# ---------------------------------------------------------------------------
# _strip_reasoning() — chain-of-thought filter for Gemma 4
# ---------------------------------------------------------------------------

from server.llm import _strip_reasoning


class TestStripReasoning:
    def test_plain_text_unchanged(self):
        text = "The blue whale is the biggest animal in the world."
        assert _strip_reasoning(text) == text

    def test_the_user_preamble_stripped(self):
        # "The user is asking..." is a classic Gemma 4 chain-of-thought leak
        text = "The user is asking about whales. The blue whale is the biggest animal."
        result = _strip_reasoning(text)
        assert result.startswith("The blue whale")
        assert "The user" not in result

    def test_multiple_reasoning_sentences_stripped(self):
        text = "The user wants to know. I need to keep my response simple. Volcanoes are mountains."
        result = _strip_reasoning(text)
        assert "Volcanoes" in result
        assert "The user" not in result

    def test_all_reasoning_returns_original_as_fallback(self):
        # All sentences match _REASONING_RE — fallback returns original text
        text = "The user is asking this. I need to respond carefully."
        result = _strip_reasoning(text)
        assert result == text  # nothing non-reasoning found, return as-is

    def test_empty_string_returns_empty(self):
        assert _strip_reasoning("") == ""

    def test_first_sentence_not_reasoning_returns_all(self):
        text = "Dinosaurs lived millions of years ago. The user might ask more."
        result = _strip_reasoning(text)
        assert result == text  # first sentence is clean → whole text returned

    def test_markdown_asterisk_stripped(self):
        # Lines starting with * are markdown — always reasoning in this context
        text = "* thinking about this. Volcanoes are amazing."
        result = _strip_reasoning(text)
        assert "Volcanoes" in result

    def test_my_response_meta_stripped(self):
        text = "My response should be friendly. Elephants are the biggest land animals."
        result = _strip_reasoning(text)
        assert "Elephants" in result
        assert "My response" not in result

    def test_answer_opening_with_they_are_not_stripped(self):
        # Regression: "They are/have/seem" open real answers and must survive.
        for text in (
            "They are enormous reptiles that lived long ago.",
            "They have powerful legs for jumping.",
            "They seem scary but most were gentle plant-eaters.",
            "Since they live underwater, fish breathe through gills.",
        ):
            assert _strip_reasoning(text) == text

    def test_meta_they_forms_still_stripped(self):
        text = "They are asking about dinosaurs. Dinosaurs are amazing reptiles!"
        result = _strip_reasoning(text)
        assert result == "Dinosaurs are amazing reptiles!"
