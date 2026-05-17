import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Stub out the ollama module before any server code imports it
_ollama_stub = ModuleType("ollama")
_ollama_stub.list = MagicMock()
_ollama_stub.chat = MagicMock()
sys.modules.setdefault("ollama", _ollama_stub)

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
