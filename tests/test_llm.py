import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# conftest.py registers the ollama stub before any test file is imported
_ollama_stub = sys.modules["ollama"]

from server.guardrails import OUTPUT_BLOCKED_RESPONSE, REDIRECT_RESPONSE  # noqa: E402
from server.llm import LLMInterface  # noqa: E402


def _make_ollama_response(content: str):
    """Build a minimal mock matching ollama's ChatResponse structure."""
    return SimpleNamespace(message=SimpleNamespace(content=content))


def _mock_list(model_name="kidbot"):
    mock_model = MagicMock()
    mock_model.model = model_name
    _ollama_stub.list.return_value = MagicMock(models=[mock_model])


class TestLLMInterface:
    def setup_method(self):
        _mock_list()

    def test_unsafe_input_returns_redirect_without_calling_ollama(self):
        llm = LLMInterface()
        _ollama_stub.chat.reset_mock()

        result = llm.respond("kill everyone")

        _ollama_stub.chat.assert_not_called()
        assert result == REDIRECT_RESPONSE

    def test_safe_input_returns_llm_reply(self):
        _ollama_stub.chat.return_value = _make_ollama_response("Dinosaurs are awesome!")
        llm = LLMInterface()

        result = llm.respond("Tell me about dinosaurs")

        assert result == "Dinosaurs are awesome!"

    def test_safe_input_blocked_output_returns_blocked_response(self):
        _ollama_stub.chat.return_value = _make_ollama_response("I will kill you.")
        llm = LLMInterface()

        result = llm.respond("Tell me something")

        assert result == OUTPUT_BLOCKED_RESPONSE

    def test_malformed_response_returns_blocked_response_not_crash(self):
        _ollama_stub.chat.return_value = SimpleNamespace()  # no .message attribute
        llm = LLMInterface()

        result = llm.respond("Hello")

        assert result == OUTPUT_BLOCKED_RESPONSE

    def test_empty_reply_returns_blocked_response(self):
        _ollama_stub.chat.return_value = _make_ollama_response("   ")
        llm = LLMInterface()

        result = llm.respond("Hello")

        assert result == OUTPUT_BLOCKED_RESPONSE

    def test_history_is_passed_to_ollama(self):
        _ollama_stub.chat.return_value = _make_ollama_response("Sure thing!")
        llm = LLMInterface()

        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        llm.respond("How are you?", history=history)

        call_messages = _ollama_stub.chat.call_args[1]["messages"]
        # system prompt + 2 history messages + new user message = 4
        assert len(call_messages) == 4
        assert call_messages[1] == {"role": "user", "content": "Hi"}
        assert call_messages[3] == {"role": "user", "content": "How are you?"}

    def test_model_not_found_raises_runtime_error(self):
        mock_model = MagicMock()
        mock_model.model = "some-other-model"
        _ollama_stub.list.return_value = MagicMock(models=[mock_model])

        with pytest.raises(RuntimeError, match="not found"):
            LLMInterface()


def _make_stream_chunks(texts: list[str]):
    """Build mock streaming chunks matching ollama's stream format."""
    return [SimpleNamespace(message=SimpleNamespace(content=t)) for t in texts]


class TestLLMInterfaceStream:
    def setup_method(self):
        _mock_list()

    def test_stream_yields_complete_sentences(self):
        _ollama_stub.chat.return_value = _make_stream_chunks(
            ["Dino", "saurs are cool. ", "They were big!"]
        )
        llm = LLMInterface()
        sentences = list(llm.respond_stream("Tell me about dinosaurs"))
        assert "Dinosaurs are cool." in sentences
        assert "They were big!" in sentences

    def test_stream_unsafe_input_yields_redirect(self):
        llm = LLMInterface()
        _ollama_stub.chat.reset_mock()
        sentences = list(llm.respond_stream("kill everyone"))
        assert sentences == [REDIRECT_RESPONSE]
        _ollama_stub.chat.assert_not_called()

    def test_stream_unsafe_output_stops_and_yields_blocked(self):
        # "I will kill you." should fail the output safety check
        _ollama_stub.chat.return_value = _make_stream_chunks(
            ["Hello there. ", "I will kill you. ", "More text."]
        )
        llm = LLMInterface()
        sentences = list(llm.respond_stream("hi"))
        assert OUTPUT_BLOCKED_RESPONSE in sentences
        # Should stop after the blocked sentence
        assert sentences[-1] == OUTPUT_BLOCKED_RESPONSE

    def test_stream_short_fragment_merged_with_next(self):
        # "Hi." is only 3 chars — should be merged, not yielded alone
        _ollama_stub.chat.return_value = _make_stream_chunks(
            ["Hi. ", "How are you doing today?"]
        )
        llm = LLMInterface()
        sentences = list(llm.respond_stream("hello"))
        # "Hi." alone should not appear as a separate chunk
        assert "Hi." not in sentences

    def test_stream_flushes_remainder_without_trailing_punctuation(self):
        _ollama_stub.chat.return_value = _make_stream_chunks(
            ["Cats are great pets"]  # no trailing punctuation
        )
        llm = LLMInterface()
        sentences = list(llm.respond_stream("tell me about cats"))
        assert any("Cats are great" in s for s in sentences)

    def test_stream_passes_history_to_ollama(self):
        _ollama_stub.chat.return_value = _make_stream_chunks(["Sure thing!"])
        llm = LLMInterface()
        history = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}]
        list(llm.respond_stream("How are you?", history=history))
        call_messages = _ollama_stub.chat.call_args[1]["messages"]
        assert len(call_messages) == 4  # system + 2 history + user
