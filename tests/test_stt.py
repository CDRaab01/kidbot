import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_segment(text: str):
    seg = SimpleNamespace()
    seg.text = text
    return seg


class TestSpeechToText:
    def _make_stt(self, segments=None):
        """Return an STT instance with WhisperModel returning the given segments."""
        from server.stt import SpeechToText

        mock_model = MagicMock()
        mock_model.transcribe.return_value = (segments or [], MagicMock())
        sys.modules["faster_whisper"].WhisperModel.return_value = mock_model
        stt = SpeechToText()
        return stt, mock_model

    def test_single_segment_returned_stripped(self):
        stt, _ = self._make_stt([_make_segment("  hello world  ")])
        assert stt.transcribe("audio.wav") == "hello world"

    def test_multiple_segments_joined_with_space(self):
        stt, _ = self._make_stt([
            _make_segment("hello"),
            _make_segment("world"),
        ])
        assert stt.transcribe("audio.wav") == "hello world"

    def test_segments_with_inner_whitespace_collapsed(self):
        stt, _ = self._make_stt([
            _make_segment("  one  "),
            _make_segment("  two  "),
        ])
        assert stt.transcribe("audio.wav") == "one two"

    def test_empty_segment_list_returns_empty_string(self):
        stt, _ = self._make_stt([])
        assert stt.transcribe("audio.wav") == ""

    def test_transcribe_passes_correct_kwargs(self):
        stt, mock_model = self._make_stt([_make_segment("hi")])
        stt.transcribe("/tmp/test.wav")
        call_kwargs = mock_model.transcribe.call_args[1]
        assert call_kwargs["beam_size"] == 1
        assert call_kwargs["language"] == "en"
        assert call_kwargs["vad_filter"] is False

    def test_transcribe_passes_audio_path(self):
        stt, mock_model = self._make_stt([_make_segment("hi")])
        stt.transcribe("/tmp/test.wav")
        called_path = mock_model.transcribe.call_args[0][0]
        assert called_path == "/tmp/test.wav"
