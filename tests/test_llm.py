from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from server.guardrails import OUTPUT_BLOCKED_RESPONSE, REDIRECT_RESPONSE
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
        assert result == REDIRECT_RESPONSE

    def test_blocked_output_returns_blocked_response(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_response("I will kill you.")
        llm = LLMInterface()
        result = llm.respond("Tell me something")
        assert result == OUTPUT_BLOCKED_RESPONSE

    def test_empty_reply_returns_blocked_response(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_response("   ")
        llm = LLMInterface()
        result = llm.respond("Hello")
        assert result == OUTPUT_BLOCKED_RESPONSE

    def test_api_exception_returns_blocked_response(self, mock_openai):
        mock_openai.chat.completions.create.side_effect = Exception("connection refused")
        llm = LLMInterface()
        result = llm.respond("Hello")
        assert result == OUTPUT_BLOCKED_RESPONSE

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
        assert sentences == [REDIRECT_RESPONSE]
        mock_openai.chat.completions.create.assert_not_called()

    def test_stream_unsafe_output_stops_and_yields_blocked(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_stream_chunks(
            ["Hello there. ", "I will kill you. ", "More text."]
        )
        llm = LLMInterface()
        sentences = list(llm.respond_stream("hi"))
        assert OUTPUT_BLOCKED_RESPONSE in sentences
        assert sentences[-1] == OUTPUT_BLOCKED_RESPONSE

    def test_stream_short_fragment_merged_with_next(self, mock_openai):
        mock_openai.chat.completions.create.return_value = _make_stream_chunks(
            ["Hi. ", "How are you doing today?"]
        )
        llm = LLMInterface()
        sentences = list(llm.respond_stream("hello"))
        assert "Hi." not in sentences

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
        assert sentences == [OUTPUT_BLOCKED_RESPONSE]

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
