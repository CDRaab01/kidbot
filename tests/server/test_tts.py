import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from server.tts import clean_for_speech


# ---------------------------------------------------------------------------
# clean_for_speech — pure function, no mocks needed
# ---------------------------------------------------------------------------

class TestCleanForSpeech:
    def test_emoji_stripped(self):
        assert clean_for_speech("Hello 😀") == "Hello"

    def test_curly_apostrophes_normalised(self):
        assert "'" in clean_for_speech("it’s fine")
        assert "’" not in clean_for_speech("it’s fine")

    def test_curly_double_quotes_removed(self):
        result = clean_for_speech("“Hello”")
        assert "“" not in result
        assert "”" not in result

    def test_em_dash_becomes_comma_space(self):
        assert clean_for_speech("wait—stop") == "wait, stop"

    def test_en_dash_becomes_comma_space(self):
        assert clean_for_speech("1–5") == "1, 5"

    def test_markdown_symbols_removed(self):
        assert "*" not in clean_for_speech("**bold**")
        assert "#" not in clean_for_speech("# heading")
        assert "`" not in clean_for_speech("`code`")

    def test_parenthetical_asides_removed(self):
        result = clean_for_speech("Hello (this is aside) world")
        assert "this is aside" not in result
        assert "Hello" in result
        assert "world" in result

    def test_excess_whitespace_collapsed(self):
        assert clean_for_speech("too   many   spaces") == "too many spaces"

    def test_empty_string_stays_empty(self):
        assert clean_for_speech("") == ""

    def test_plain_text_unchanged(self):
        assert clean_for_speech("Hello, how are you?") == "Hello, how are you?"


# ---------------------------------------------------------------------------
# TextToSpeech.synthesize — mocked Kokoro + soundfile + subprocess
# ---------------------------------------------------------------------------

class TestTextToSpeech:
    def _make_tts(self, audio=None, sample_rate=24000):
        """Return a TTS instance with Kokoro mocked to return fake audio."""
        from server.tts import TextToSpeech

        if audio is None:
            audio = np.zeros(100, dtype=np.float32)

        kokoro_instance = MagicMock()
        kokoro_instance.create.return_value = (audio, sample_rate)
        sys.modules["kokoro_onnx"].Kokoro.return_value = kokoro_instance
        return TextToSpeech(), kokoro_instance

    def _fake_ffmpeg(self, mp3_bytes=b"FAKEMP3"):
        """Return a subprocess.run side_effect that writes mp3_bytes to mp3_path."""
        def _run(cmd, **kwargs):
            mp3_path = cmd[-1]
            with open(mp3_path, "wb") as f:
                f.write(mp3_bytes)
            return MagicMock(returncode=0)
        return _run

    @patch("server.tts.subprocess.run")
    def test_synthesize_returns_mp3_bytes(self, mock_run):
        mock_run.side_effect = self._fake_ffmpeg(b"REALMP3DATA")
        tts, kokoro_instance = self._make_tts()

        result = tts.synthesize("Hello!")

        assert result == b"REALMP3DATA"
        kokoro_instance.create.assert_called_once()

    @patch("server.tts.subprocess.run")
    def test_empty_text_falls_back_to_hmm(self, mock_run):
        mock_run.side_effect = self._fake_ffmpeg()
        tts, kokoro_instance = self._make_tts()

        tts.synthesize("(only an aside)")  # clean_for_speech removes all content

        text_arg = kokoro_instance.create.call_args[0][0]
        assert text_arg == "Hmm."

    @patch("server.tts.subprocess.run")
    def test_ffmpeg_called_with_wav_and_mp3_paths(self, mock_run):
        mock_run.side_effect = self._fake_ffmpeg()
        tts, _ = self._make_tts()

        tts.synthesize("test")

        cmd = mock_run.call_args[0][0]
        assert "ffmpeg" in cmd[0]
        assert any(a.endswith(".wav") for a in cmd)
        assert any(a.endswith(".mp3") for a in cmd)

    @patch("server.tts.subprocess.run")
    def test_temp_files_cleaned_up_on_success(self, mock_run):
        import os
        created_paths = []

        def tracking_ffmpeg(cmd, **kwargs):
            mp3_path = cmd[-1]
            with open(mp3_path, "wb") as f:
                f.write(b"MP3")
            created_paths.append(cmd[-2])  # wav_path
            created_paths.append(mp3_path)
            return MagicMock(returncode=0)

        mock_run.side_effect = tracking_ffmpeg
        tts, _ = self._make_tts()
        tts.synthesize("hello")

        for path in created_paths:
            assert not os.path.exists(path), f"Temp file not cleaned up: {path}"
