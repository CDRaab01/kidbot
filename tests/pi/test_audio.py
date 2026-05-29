"""Tests for pi_client/audio.py — AudioManager playback, blip, and mic discovery.

The mic stream is never opened in these tests: the PyAudio stub reports zero
input devices, so _find_mic() returns None and __init__ skips stream setup.
"""
import importlib
from unittest.mock import MagicMock, patch

import pytest


def _import_audio():
    import pi_client.audio as audio
    importlib.reload(audio)
    # No input devices → _find_mic returns None → no stream/thread on init.
    audio.pyaudio.PyAudio.return_value.get_device_count.return_value = 0
    return audio


def _make_manager(audio):
    return audio.AudioManager()


class TestFindMic:
    def test_matches_device_by_hint(self):
        audio = _import_audio()
        pa = audio.pyaudio.PyAudio.return_value
        pa.get_device_count.return_value = 2
        pa.get_device_info_by_index.side_effect = [
            {"name": "bcm2835 Headphones", "maxInputChannels": 0},
            {"name": "seeed aic3104 capture", "maxInputChannels": 2},
        ]
        am = audio.AudioManager()
        assert am._device_index == 1

    def test_returns_none_when_no_match(self):
        audio = _import_audio()
        pa = audio.pyaudio.PyAudio.return_value
        pa.get_device_count.return_value = 1
        pa.get_device_info_by_index.side_effect = [
            {"name": "some other mic", "maxInputChannels": 2},
        ]
        am = audio.AudioManager()
        assert am._device_index is None


class TestStopPlayback:
    def test_kills_running_process(self):
        audio = _import_audio()
        am = _make_manager(audio)
        proc = MagicMock()
        proc.poll.return_value = None  # still running
        am._playback_proc = proc
        am.stop_playback()
        proc.kill.assert_called_once()
        assert am._playback_proc is None

    def test_no_error_when_nothing_playing(self):
        audio = _import_audio()
        am = _make_manager(audio)
        am._playback_proc = None
        am.stop_playback()  # must not raise


class TestPlayMp3Stream:
    def test_feeds_chunks_to_mpg123(self):
        audio = _import_audio()
        am = _make_manager(audio)
        proc = MagicMock()
        proc.poll.return_value = None
        with patch("subprocess.Popen", return_value=proc):
            am.play_mp3_stream(iter([b"a", b"b"]))
        assert proc.stdin.write.call_count == 2
        proc.stdin.close.assert_called_once()
        proc.wait.assert_called_once()

    def test_broken_pipe_kills_process(self):
        audio = _import_audio()
        am = _make_manager(audio)
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdin.write.side_effect = BrokenPipeError
        with patch("subprocess.Popen", return_value=proc):
            am.play_mp3_stream(iter([b"a"]))
        proc.kill.assert_called_once()


class TestVolumeBlip:
    def test_plays_via_paplay_and_cleans_up(self):
        audio = _import_audio()
        am = _make_manager(audio)
        proc = MagicMock()
        proc.communicate.return_value = (b"", b"")
        proc.returncode = 0
        with patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen", return_value=proc) as mock_popen, \
             patch("os.unlink") as mock_unlink:
            am.play_volume_blip(50)
        # paplay was invoked
        assert mock_popen.call_args[0][0][0] == "paplay"
        # temp wav cleaned up
        mock_unlink.assert_called_once()
        # PCM level re-asserted via amixer
        assert any(c[0][0][0] == "amixer" for c in mock_run.call_args_list)
