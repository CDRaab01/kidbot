"""Tests for pi_client/main.py — push-to-talk busy-lock robustness.

Importing pi_client.main constructs the hardware objects (button, audio,
client, display, volume_rocker) against the stubbed platform modules, then we
swap them for MagicMocks to drive on_press / on_release in isolation.
"""
import importlib
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def main_mod():
    import pi_client.display as disp_mod
    # Suppress the display render/battery threads while constructing the module's
    # hardware objects (they would otherwise spin against the stubbed PIL).
    with patch.object(disp_mod, "_init_device", return_value=None), \
         patch("threading.Thread"):
        import pi_client.main as main
        importlib.reload(main)
    # Replace hardware/network with mocks; keep the real _busy_lock.
    main.button = MagicMock()
    main.audio = MagicMock()
    main.client = MagicMock()
    main.display = MagicMock()
    # Make sure we start from a clean (unlocked) lock.
    if main._busy_lock.locked():
        main._busy_lock.release()
    return main


class TestBusyLock:
    def test_press_acquires_lock(self, main_mod):
        main_mod.on_press()
        assert main_mod._busy_lock.locked()
        main_mod.audio.start_recording.assert_called_once()

    def test_second_press_while_busy_is_ignored(self, main_mod):
        main_mod.on_press()
        main_mod.audio.start_recording.reset_mock()
        main_mod.on_press()  # already busy
        main_mod.audio.start_recording.assert_not_called()

    def test_unpaired_release_does_not_raise_or_process(self, main_mod):
        # Lock not held — release must be a no-op, not RuntimeError.
        assert not main_mod._busy_lock.locked()
        main_mod.on_release()  # must not raise
        main_mod.client.send_audio_stream.assert_not_called()
        assert not main_mod._busy_lock.locked()

    def test_normal_press_release_cycle_releases_lock(self, main_mod):
        main_mod.audio.stop_recording.return_value = "/tmp/x.wav"
        main_mod.client.send_audio_stream.return_value = iter([b"mp3"])
        main_mod.client.get_latest_image.return_value = (None, False)
        main_mod.on_press()
        with patch("os.unlink"), patch("time.sleep"):
            main_mod.on_release()
        assert not main_mod._busy_lock.locked()

    def test_unlink_failure_still_releases_lock(self, main_mod):
        """A missing temp file must not wedge the button (the original bug)."""
        main_mod.audio.stop_recording.return_value = "/tmp/gone.wav"
        main_mod.client.send_audio_stream.return_value = iter([b"mp3"])
        main_mod.client.get_latest_image.return_value = (None, False)
        main_mod.on_press()
        with patch("os.unlink", side_effect=FileNotFoundError), patch("time.sleep"):
            main_mod.on_release()  # must not raise
        assert not main_mod._busy_lock.locked()

    def test_stop_recording_failure_releases_lock(self, main_mod):
        main_mod.audio.stop_recording.side_effect = RuntimeError("device gone")
        main_mod.on_press()
        main_mod.on_release()
        assert not main_mod._busy_lock.locked()
        main_mod.client.send_audio_stream.assert_not_called()


class TestPollForImage:
    def test_returns_url_on_first_poll(self, main_mod):
        main_mod.client.get_latest_image.return_value = ("http://img/x.jpg", False)
        assert main_mod._poll_for_image() == "http://img/x.jpg"
        assert main_mod.client.get_latest_image.call_count == 1

    def test_no_image_no_pending_returns_immediately(self, main_mod):
        # Normal (no-image) turn: first poll says not pending → no delay.
        main_mod.client.get_latest_image.return_value = (None, False)
        with patch("time.sleep") as sleep:
            assert main_mod._poll_for_image() is None
        sleep.assert_not_called()
        assert main_mod.client.get_latest_image.call_count == 1

    def test_waits_while_pending_then_returns_url(self, main_mod):
        # Pending for two polls, then the URL arrives.
        main_mod.client.get_latest_image.side_effect = [
            (None, True), (None, True), ("http://img/late.jpg", False),
        ]
        with patch("time.sleep"):
            assert main_mod._poll_for_image() == "http://img/late.jpg"
        assert main_mod.client.get_latest_image.call_count == 3

    def test_gives_up_after_budget(self, main_mod):
        main_mod.client.get_latest_image.return_value = (None, True)  # never resolves
        with patch("time.sleep"):
            assert main_mod._poll_for_image() is None
        assert main_mod.client.get_latest_image.call_count == main_mod._IMAGE_POLL_ATTEMPTS
