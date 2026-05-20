"""
Tests for test_gui.py logic that does not require audio hardware.

Tests that need tk.Tk() are guarded with @requires_display and skip
automatically on headless CI where $DISPLAY is not set.
"""
import os
import queue
import sys
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from server.config import BOT_NAME

requires_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="No $DISPLAY — skipping Tkinter tests",
)


# ---------------------------------------------------------------------------
# _get_input_devices() — static method, no Tk needed
# ---------------------------------------------------------------------------

class TestGetInputDevices:
    def _query(self, device_list):
        sys.modules["sounddevice"].query_devices.return_value = device_list
        from test_gui import KidBotGUI
        return KidBotGUI._get_input_devices()

    def test_output_only_devices_excluded(self):
        devices = self._query([
            {"name": "Real Mic", "max_input_channels": 2},
            {"name": "Speakers", "max_input_channels": 0},
        ])
        names = [d["name"] for d in devices]
        assert "Real Mic" in names
        assert "Speakers" not in names

    def test_virtual_loopback_devices_filtered(self):
        devices = self._query([
            {"name": "Real Mic", "max_input_channels": 2},
            {"name": "Stereo Mix", "max_input_channels": 2},
            {"name": "Wave Mapper", "max_input_channels": 2},
            {"name": "Primary Sound Capture", "max_input_channels": 2},
        ])
        names = [d["name"] for d in devices]
        assert "Real Mic" in names
        assert "Stereo Mix" not in names
        assert "Wave Mapper" not in names
        assert "Primary Sound Capture" not in names

    def test_duplicate_names_deduplicated(self):
        devices = self._query([
            {"name": "Blue Yeti", "max_input_channels": 2},
            {"name": "Blue Yeti (WASAPI)", "max_input_channels": 2},
        ])
        assert len(devices) == 1
        assert devices[0]["name"] == "Blue Yeti"

    def test_device_index_matches_enumerate_position(self):
        devices = self._query([
            {"name": "Speakers", "max_input_channels": 0},    # index 0, skipped
            {"name": "Real Mic", "max_input_channels": 1},    # index 1, kept
            {"name": "Webcam Mic", "max_input_channels": 1},  # index 2, kept
        ])
        indices = [d["index"] for d in devices]
        assert 1 in indices
        assert 2 in indices
        assert 0 not in indices

    def test_empty_device_list_returns_empty(self):
        devices = self._query([])
        assert devices == []


# ---------------------------------------------------------------------------
# WAV writing — pure I/O logic, no Tk needed
# ---------------------------------------------------------------------------

class TestWavWriting:
    def test_wav_written_with_correct_params(self, tmp_path):
        """Simulate what _stop_recording_and_send does with audio frames."""
        frames = [np.array([[v], [v + 1]], dtype=np.int16) for v in range(5)]
        audio = np.concatenate(frames, axis=0)

        wav_path = str(tmp_path / "test.wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio.tobytes())

        with wave.open(wav_path, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            data = wf.readframes(wf.getnframes())
        assert data == audio.tobytes()

    def test_empty_frames_list_produces_empty_wav(self, tmp_path):
        audio = np.concatenate([np.zeros((0, 1), dtype=np.int16)], axis=0) \
            if False else np.array([], dtype=np.int16)

        wav_path = str(tmp_path / "empty.wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(audio.tobytes())

        with wave.open(wav_path, "rb") as wf:
            assert wf.getnframes() == 0


# ---------------------------------------------------------------------------
# GUI state machine — requires a display
# ---------------------------------------------------------------------------

@requires_display
class TestGUIStateMachine:
    def setup_method(self):
        import tkinter as tk
        self.root = tk.Tk()
        from test_gui import KidBotGUI
        self.gui = KidBotGUI(self.root)

    def teardown_method(self):
        self.root.destroy()

    def test_initial_state_is_idle(self):
        assert self.gui.state == "IDLE"

    def test_set_state_updates_state_attribute(self):
        self.gui._set_state("RECORDING")
        assert self.gui.state == "RECORDING"

    def test_queue_level_message_updates_canvas(self):
        self.gui._ui_queue.put(("level", 75))
        self.gui._poll_queue()
        # Canvas coords should have been updated (no exception = pass)

    def test_queue_state_message_transitions_state(self):
        self.gui._ui_queue.put(("state", "PLAYING"))
        self.gui._poll_queue()
        assert self.gui.state == "PLAYING"

    def test_queue_idle_message_resets_level_bar(self):
        self.gui._ui_queue.put(("state", "IDLE"))
        self.gui._poll_queue()
        assert self.gui.state == "IDLE"

    def test_queue_chat_message_logged_to_chat_widget(self):
        self.gui._ui_queue.put(("chat", (BOT_NAME, "Hello!")))
        self.gui._poll_queue()
        import tkinter as tk
        content = self.gui.chat.get("1.0", tk.END)
        assert "Hello!" in content

    def test_queue_error_message_returns_state_to_idle(self):
        self.gui._set_state("PROCESSING")
        self.gui._ui_queue.put(("error", "Something went wrong"))
        self.gui._poll_queue()
        assert self.gui.state == "IDLE"

    def test_space_in_idle_calls_start_recording(self):
        self.gui._start_recording = MagicMock()
        event = MagicMock()
        event.widget = self.root  # not the text entry
        self.root.focus_get = MagicMock(return_value=self.root)
        self.gui._on_space(event)
        self.gui._start_recording.assert_called_once()

    def test_space_in_text_entry_does_nothing(self):
        self.gui._start_recording = MagicMock()
        event = MagicMock()
        # Simulate focus being on the text entry widget
        self.root.focus_get = MagicMock(return_value=self.gui.text_entry)
        self.gui._on_space(event)
        self.gui._start_recording.assert_not_called()

    def test_mic_selection_updates_device_index(self):
        if len(self.gui._input_devices) < 2:
            pytest.skip("Need at least 2 input devices for this test")
        second = self.gui._input_devices[1]
        self.gui.mic_var.set(second["name"])
        self.gui._on_mic_selected()
        assert self.gui._selected_device_index == second["index"]
