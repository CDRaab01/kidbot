"""Tests for pi_client/main.py — push-to-talk busy-lock robustness.

Importing pi_client.main constructs the hardware objects (button, audio,
client, display, volume_rocker) against the stubbed platform modules, then we
swap them for MagicMocks to drive on_press / on_release in isolation.
"""
import importlib
import time
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

    def test_no_loading_state_on_immediate_no_image(self, main_mod):
        """Common no-image turn: don't flash LOADING for a single poll."""
        main_mod.client.get_latest_image.return_value = (None, False)
        with patch("time.sleep"):
            main_mod._poll_for_image()
        # set_state("LOADING") was NOT called — the only state change is
        # whatever main.py does AFTER _poll_for_image returns.
        states = [c[0][0] for c in main_mod.display.set_state.call_args_list]
        assert "LOADING" not in states

    def test_shows_loading_while_pending(self, main_mod):
        """Slow image fetch — LOADING appears so the child sees us trying."""
        main_mod.client.get_latest_image.side_effect = [
            (None, True), (None, True), ("http://img/late.jpg", False),
        ]
        with patch("time.sleep"):
            main_mod._poll_for_image()
        states = [c[0][0] for c in main_mod.display.set_state.call_args_list]
        # LOADING shown exactly once (not on every pending poll).
        assert states.count("LOADING") == 1


# ---------------------------------------------------------------------------
# CURIOUS dispatch when the bot ends a reply with a question
# ---------------------------------------------------------------------------

class TestEndsWithQuestion:
    def test_plain_question(self, main_mod):
        assert main_mod._ends_with_question("what do you think?") is True

    def test_trailing_whitespace_ok(self, main_mod):
        assert main_mod._ends_with_question("really?  \n") is True

    def test_trailing_quote_ok(self, main_mod):
        assert main_mod._ends_with_question('does it count?"') is True

    def test_no_question(self, main_mod):
        assert main_mod._ends_with_question("dinosaurs are huge.") is False

    def test_empty_returns_false(self, main_mod):
        assert main_mod._ends_with_question("") is False
        assert main_mod._ends_with_question(None) is False  # type: ignore


class TestCuriousDispatch:
    def test_question_reply_shows_curious_not_happy(self, main_mod):
        main_mod.audio.stop_recording.return_value = "/tmp/x.wav"
        main_mod.client.send_audio_stream.return_value = iter([b"mp3"])
        main_mod.client.get_latest_image.return_value = (None, False)
        main_mod.client.get_latest_reply.return_value = "want to hear more?"
        main_mod.on_press()
        with patch("os.unlink"), patch("time.sleep"):
            main_mod.on_release()
        states = [c[0][0] for c in main_mod.display.set_state.call_args_list]
        assert "CURIOUS" in states
        assert "HAPPY" not in states

    def test_non_question_reply_shows_happy(self, main_mod):
        main_mod.audio.stop_recording.return_value = "/tmp/x.wav"
        main_mod.client.send_audio_stream.return_value = iter([b"mp3"])
        main_mod.client.get_latest_image.return_value = (None, False)
        main_mod.client.get_latest_reply.return_value = "Dinosaurs are amazing!"
        main_mod.on_press()
        with patch("os.unlink"), patch("time.sleep"):
            main_mod.on_release()
        states = [c[0][0] for c in main_mod.display.set_state.call_args_list]
        assert "HAPPY" in states
        assert "CURIOUS" not in states


# ---------------------------------------------------------------------------
# BORED idle timer
# ---------------------------------------------------------------------------

class TestBoredWatcher:
    def test_mark_activity_resets_timer(self, main_mod):
        main_mod._last_activity = 0
        main_mod._bored = True
        main_mod._mark_activity()
        assert main_mod._last_activity > 0
        assert main_mod._bored is False

    def test_press_marks_activity(self, main_mod):
        main_mod._last_activity = 0
        main_mod.on_press()
        assert main_mod._last_activity > 0

    def test_watcher_skips_when_disabled(self, main_mod):
        # BORED_AFTER_SECONDS=0 → never set BORED.
        main_mod._last_activity = 0
        with patch.object(main_mod, "BORED_AFTER_SECONDS", 0), \
             patch("time.sleep") as sleep:
            def stop_after_first(_):
                raise StopIteration  # break out of while True
            sleep.side_effect = stop_after_first
            try:
                main_mod._bored_watcher()
            except StopIteration:
                pass
        # display.set_state never called with BORED
        states = [c[0][0] for c in main_mod.display.set_state.call_args_list]
        assert "BORED" not in states

    def test_watcher_sets_bored_after_threshold(self, main_mod):
        main_mod._bored = False
        main_mod._last_activity = time.time() - 200  # very stale
        with patch.object(main_mod, "BORED_AFTER_SECONDS", 120), \
             patch("time.sleep") as sleep:
            stop = [False]
            def one_tick(_):
                if stop[0]:
                    raise StopIteration
                stop[0] = True
            sleep.side_effect = one_tick
            try:
                main_mod._bored_watcher()
            except StopIteration:
                pass
        states = [c[0][0] for c in main_mod.display.set_state.call_args_list]
        assert "BORED" in states
        assert main_mod._bored is True

    def test_watcher_does_not_re_set_when_already_bored(self, main_mod):
        main_mod._bored = True  # already bored
        main_mod._last_activity = time.time() - 200
        with patch.object(main_mod, "BORED_AFTER_SECONDS", 120), \
             patch("time.sleep") as sleep:
            stop = [False]
            def one_tick(_):
                if stop[0]:
                    raise StopIteration
                stop[0] = True
            sleep.side_effect = one_tick
            try:
                main_mod._bored_watcher()
            except StopIteration:
                pass
        # No new set_state("BORED") — already there.
        states = [c[0][0] for c in main_mod.display.set_state.call_args_list]
        assert states.count("BORED") == 0

    def test_watcher_skips_during_conversation(self, main_mod):
        """Busy_lock held → mid-conversation → never bored."""
        main_mod._bored = False
        main_mod._last_activity = time.time() - 200
        main_mod._busy_lock.acquire()
        try:
            with patch.object(main_mod, "BORED_AFTER_SECONDS", 120), \
                 patch("time.sleep") as sleep:
                stop = [False]
                def one_tick(_):
                    if stop[0]:
                        raise StopIteration
                    stop[0] = True
                sleep.side_effect = one_tick
                try:
                    main_mod._bored_watcher()
                except StopIteration:
                    pass
        finally:
            main_mod._busy_lock.release()
        states = [c[0][0] for c in main_mod.display.set_state.call_args_list]
        assert "BORED" not in states
