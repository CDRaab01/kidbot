"""Tests for pi_client/volume.py — VolumeRocker and amixer helpers."""
import subprocess
import sys
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers for importing the module under test
# ---------------------------------------------------------------------------

def _import_volume():
    # Force re-import so GPIO mock is freshly applied each time
    import importlib
    import pi_client.volume as vol
    importlib.reload(vol)
    return vol


# ---------------------------------------------------------------------------
# _get_volume
# ---------------------------------------------------------------------------

class TestGetVolume:
    def test_parses_50_percent(self):
        vol = _import_volume()
        with patch("subprocess.check_output", return_value="  [50%] [on]\n"):
            assert vol._get_volume("Master") == 50

    def test_parses_0_percent(self):
        vol = _import_volume()
        with patch("subprocess.check_output", return_value="[0%]"):
            assert vol._get_volume("Master") == 0

    def test_parses_100_percent(self):
        vol = _import_volume()
        with patch("subprocess.check_output", return_value="[100%]"):
            assert vol._get_volume("Master") == 100

    def test_returns_none_on_garbage_output(self):
        vol = _import_volume()
        with patch("subprocess.check_output", return_value="no match here"):
            assert vol._get_volume("Master") is None

    def test_returns_none_on_subprocess_error(self):
        vol = _import_volume()
        with patch("subprocess.check_output", side_effect=subprocess.SubprocessError):
            assert vol._get_volume("Master") is None

    def test_returns_none_on_file_not_found(self):
        vol = _import_volume()
        with patch("subprocess.check_output", side_effect=FileNotFoundError):
            assert vol._get_volume("Master") is None


# ---------------------------------------------------------------------------
# _set_volume
# ---------------------------------------------------------------------------

class TestSetVolume:
    def test_calls_amixer_with_correct_args(self):
        vol = _import_volume()
        with patch("subprocess.run") as mock_run:
            vol._set_volume(70, "Master")
            mock_run.assert_called_once_with(
                ["amixer", "sset", "Master", "70%"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def test_uses_alsa_control_name(self):
        vol = _import_volume()
        with patch("subprocess.run") as mock_run:
            vol._set_volume(40, "PCM")
            args = mock_run.call_args[0][0]
            assert args[2] == "PCM"


# ---------------------------------------------------------------------------
# VolumeRocker._adjust
# ---------------------------------------------------------------------------

class TestVolumeRockerAdjust:
    def _make_rocker(self, current_vol=50):
        vol = _import_volume()
        gpio = sys.modules["RPi.GPIO"]
        on_change = MagicMock()
        with patch.object(vol, "_get_volume", return_value=current_vol), \
             patch.object(vol, "_set_volume") as mock_set:
            rocker = vol.VolumeRocker(on_change=on_change)
        rocker._get_volume_mock = lambda v: current_vol
        return rocker, on_change, vol

    def test_increase_volume(self):
        vol = _import_volume()
        on_change = MagicMock()
        with patch.object(vol, "_get_volume", return_value=50), \
             patch.object(vol, "_set_volume") as mock_set:
            rocker = vol.VolumeRocker(on_change=on_change)
            with patch.object(vol, "_get_volume", return_value=50), \
                 patch.object(vol, "_set_volume") as mock_set2:
                rocker._adjust(+5)
                mock_set2.assert_called_once_with(55, vol.ALSA_CONTROL)

    def test_decrease_volume(self):
        vol = _import_volume()
        with patch.object(vol, "_get_volume", return_value=50), \
             patch.object(vol, "_set_volume"):
            rocker = vol.VolumeRocker()
            with patch.object(vol, "_get_volume", return_value=50), \
                 patch.object(vol, "_set_volume") as mock_set:
                rocker._adjust(-5)
                mock_set.assert_called_once_with(45, vol.ALSA_CONTROL)

    def test_clamps_at_max(self):
        vol = _import_volume()
        with patch.object(vol, "_get_volume", return_value=98), \
             patch.object(vol, "_set_volume"):
            rocker = vol.VolumeRocker()
            with patch.object(vol, "_get_volume", return_value=98), \
                 patch.object(vol, "_set_volume") as mock_set:
                rocker._adjust(+5)
                mock_set.assert_called_once_with(100, vol.ALSA_CONTROL)

    def test_clamps_at_min(self):
        vol = _import_volume()
        with patch.object(vol, "_get_volume", return_value=2), \
             patch.object(vol, "_set_volume"):
            rocker = vol.VolumeRocker()
            with patch.object(vol, "_get_volume", return_value=2), \
                 patch.object(vol, "_set_volume") as mock_set:
                rocker._adjust(-5)
                mock_set.assert_called_once_with(0, vol.ALSA_CONTROL)

    def test_no_op_at_max_limit(self):
        """When already at VOL_MAX, _set_volume should not be called."""
        vol = _import_volume()
        on_change = MagicMock()
        with patch.object(vol, "_get_volume", return_value=100), \
             patch.object(vol, "_set_volume"):
            rocker = vol.VolumeRocker(on_change=on_change)
            with patch.object(vol, "_get_volume", return_value=100), \
                 patch.object(vol, "_set_volume") as mock_set:
                rocker._adjust(+5)
                mock_set.assert_not_called()

    def test_no_op_skips_on_change(self):
        """on_change must NOT be called when volume is unchanged."""
        vol = _import_volume()
        on_change = MagicMock()
        with patch.object(vol, "_get_volume", return_value=0), \
             patch.object(vol, "_set_volume"):
            rocker = vol.VolumeRocker(on_change=on_change)
            with patch.object(vol, "_get_volume", return_value=0), \
                 patch.object(vol, "_set_volume"):
                rocker._adjust(-5)
        on_change.assert_not_called()

    def test_on_change_called_with_new_pct(self):
        vol = _import_volume()
        on_change = MagicMock()
        with patch.object(vol, "_get_volume", return_value=50), \
             patch.object(vol, "_set_volume"):
            rocker = vol.VolumeRocker(on_change=on_change)
        with patch.object(vol, "_get_volume", return_value=50), \
             patch.object(vol, "_set_volume"), \
             patch("threading.Thread") as mock_thread:
            rocker._adjust(+5)
            # Thread should be started with on_change and new_pct=55
            mock_thread.assert_called_once()
            _, kwargs = mock_thread.call_args
            assert kwargs["args"] == (55,)

    def test_on_change_not_set_no_error(self):
        vol = _import_volume()
        with patch.object(vol, "_get_volume", return_value=50), \
             patch.object(vol, "_set_volume"):
            rocker = vol.VolumeRocker(on_change=None)
        with patch.object(vol, "_get_volume", return_value=50), \
             patch.object(vol, "_set_volume"):
            rocker._adjust(+5)  # should not raise

    def test_no_op_when_volume_unreadable(self):
        vol = _import_volume()
        on_change = MagicMock()
        with patch.object(vol, "_get_volume", return_value=50), \
             patch.object(vol, "_set_volume"):
            rocker = vol.VolumeRocker(on_change=on_change)
        with patch.object(vol, "_get_volume", return_value=None), \
             patch.object(vol, "_set_volume") as mock_set:
            rocker._adjust(+5)
            mock_set.assert_not_called()
        on_change.assert_not_called()


# ---------------------------------------------------------------------------
# VolumeRocker GPIO setup
# ---------------------------------------------------------------------------

class TestVolumeRockerGPIO:
    def test_gpio_pins_set_up(self):
        vol = _import_volume()
        gpio = sys.modules["RPi.GPIO"]
        gpio.setup.reset_mock()
        with patch.object(vol, "_get_volume", return_value=50), \
             patch.object(vol, "_set_volume"):
            vol.VolumeRocker()
        setup_pins = {c[0][0] for c in gpio.setup.call_args_list}
        assert vol.VOL_UP_PIN in setup_pins
        assert vol.VOL_DOWN_PIN in setup_pins

    def test_event_detect_registered_for_both_pins(self):
        vol = _import_volume()
        gpio = sys.modules["RPi.GPIO"]
        gpio.add_event_detect.reset_mock()
        with patch.object(vol, "_get_volume", return_value=50), \
             patch.object(vol, "_set_volume"):
            vol.VolumeRocker()
        detected_pins = {c[0][0] for c in gpio.add_event_detect.call_args_list}
        assert vol.VOL_UP_PIN in detected_pins
        assert vol.VOL_DOWN_PIN in detected_pins

    def test_cleanup_removes_event_detect(self):
        vol = _import_volume()
        gpio = sys.modules["RPi.GPIO"]
        gpio.remove_event_detect.reset_mock()
        with patch.object(vol, "_get_volume", return_value=50), \
             patch.object(vol, "_set_volume"):
            rocker = vol.VolumeRocker()
        rocker.cleanup()
        removed_pins = {c[0][0] for c in gpio.remove_event_detect.call_args_list}
        assert vol.VOL_UP_PIN in removed_pins
        assert vol.VOL_DOWN_PIN in removed_pins
