"""Tests for DisplayManager state machine + image lifecycle.

Locked-in invariants for current behaviour (the safety net before the
face/image work begins). Pillow is stubbed by the shared conftest, so we
mock PIL and _render_face anywhere a real Image would be needed.
"""
import sys
import time
from unittest.mock import MagicMock, patch


def _make_display():
    """Headless DisplayManager with render/battery threads suppressed."""
    import pi_client.display as disp_mod
    with patch.object(disp_mod, "_init_device", return_value=None), \
         patch("threading.Thread"):
        from pi_client.display import DisplayManager
        dm = DisplayManager()
    return dm, disp_mod


# ---------------------------------------------------------------------------
# set_state()
# ---------------------------------------------------------------------------

class TestSetState:
    def test_unknown_state_is_ignored(self):
        dm, _ = _make_display()
        dm._state = "IDLE"
        dm.set_state("BOGUS")
        assert dm._state == "IDLE"

    def test_transition_to_known_state(self):
        dm, _ = _make_display()
        for state in ("LISTENING", "THINKING", "SPEAKING", "HAPPY", "ERROR", "IDLE"):
            dm.set_state(state)
            assert dm._state == state

    def test_transitioning_off_image_clears_override(self):
        """A stale picture must never survive a state change away from IMAGE."""
        dm, _ = _make_display()
        dm._image_override = object()  # sentinel — real PIL image not needed
        dm._state = "IMAGE"
        dm.set_state("LISTENING")
        assert dm._image_override is None

    def test_transition_to_image_keeps_override(self):
        dm, _ = _make_display()
        canvas = object()  # sentinel
        dm._image_override = canvas
        dm.set_state("IMAGE")
        assert dm._image_override is canvas


# ---------------------------------------------------------------------------
# _animate() one-tick behaviour — drives a single iteration via patched sleep
# ---------------------------------------------------------------------------

def _run_one_tick(dm, disp_mod):
    """Drive _animate for exactly one iteration with a real device attached."""
    dm._device = MagicMock()

    def stop_after_first(_):
        dm._running = False

    # Patch _render_face so we never touch real PIL, and ImageDraw used by
    # the volume-overlay branch (only fires when _vol_pct is not None).
    with patch.object(disp_mod, "_render_face", return_value=MagicMock()) as r, \
         patch("time.sleep", side_effect=stop_after_first):
        dm._running = True
        dm._animate()
    return r


class TestAnimateImageAutoRevert:
    def test_expired_image_state_reverts_to_idle(self):
        dm, disp_mod = _make_display()
        dm._state = "IMAGE"
        dm._image_override = MagicMock()  # has .copy()
        dm._image_expiry = time.time() - 1  # already expired
        _run_one_tick(dm, disp_mod)
        assert dm._state == "IDLE"
        assert dm._image_override is None

    def test_unexpired_image_state_keeps_state(self):
        dm, disp_mod = _make_display()
        dm._state = "IMAGE"
        dm._image_override = MagicMock()  # supports .copy()
        dm._image_expiry = time.time() + 30
        with patch.object(disp_mod, "_render_face") as render:
            _run_one_tick(dm, disp_mod)
        # Override path: _render_face isn't called when override present.
        render.assert_not_called()
        assert dm._state == "IMAGE"

    def test_non_image_state_calls_render_face(self):
        dm, disp_mod = _make_display()
        dm._state = "SPEAKING"
        render = _run_one_tick(dm, disp_mod)
        render.assert_called_once()
        called_state = render.call_args[0][0]
        assert called_state == "SPEAKING"


# ---------------------------------------------------------------------------
# _animate() snapshot is taken under the lock
# ---------------------------------------------------------------------------

class TestRenderSnapshot:
    def test_state_battery_volume_read_atomically(self):
        """A single render tick must use one consistent snapshot, not a mix."""
        dm, disp_mod = _make_display()
        dm._state = "IDLE"
        dm._battery = 80
        dm._vol_pct = None
        dm._device = MagicMock()

        seen: list[tuple] = []

        def fake_render(state, frame, battery):
            seen.append((state, battery))
            return MagicMock()

        def stop_after_first(_):
            dm._running = False

        with patch.object(disp_mod, "_render_face", side_effect=fake_render), \
             patch("time.sleep", side_effect=stop_after_first):
            dm._running = True
            dm._animate()

        assert seen == [("IDLE", 80)]


# ---------------------------------------------------------------------------
# _load_image() current behaviour
# ---------------------------------------------------------------------------

class TestLoadImage:
    def test_successful_fetch_sets_image_state_and_expiry(self):
        dm, disp_mod = _make_display()
        before = time.time()
        # Fake PIL image with the attrs _load_image reads.
        fake_img = MagicMock(width=200, height=100)
        # Replace PIL.Image used via __import__("PIL.Image", fromlist=["Image"]).new
        with patch.object(disp_mod, "_fetch_pil_image", return_value=fake_img), \
             patch.object(disp_mod, "_draw_battery"), \
             patch.dict(sys.modules,
                        {"PIL.Image": MagicMock(new=MagicMock(return_value=MagicMock(paste=MagicMock()))),
                         "PIL": MagicMock(ImageDraw=MagicMock(Draw=MagicMock(return_value=MagicMock())))}):
            dm._load_image("http://x/y.jpg", token=dm._image_token)
        assert dm._state == "IMAGE"
        assert dm._image_override is not None
        assert dm._image_expiry > before

    def test_canvas_includes_battery_indicator(self):
        dm, disp_mod = _make_display()
        dm._battery = 75
        fake_img = MagicMock(width=100, height=100)
        with patch.object(disp_mod, "_fetch_pil_image", return_value=fake_img), \
             patch.object(disp_mod, "_draw_battery") as draw_bat, \
             patch.dict(sys.modules,
                        {"PIL.Image": MagicMock(new=MagicMock(return_value=MagicMock(paste=MagicMock()))),
                         "PIL": MagicMock(ImageDraw=MagicMock(Draw=MagicMock(return_value=MagicMock())))}):
            dm._load_image("http://x/y.jpg", token=dm._image_token)
        # _draw_battery is called once with the battery snapshot.
        assert draw_bat.called
        last_call_battery = draw_bat.call_args[0][1]
        assert last_call_battery == 75


# ---------------------------------------------------------------------------
# show_image_url() dispatches a background thread (doesn't block)
# ---------------------------------------------------------------------------

class TestShowImageUrl:
    def test_spawns_daemon_thread(self):
        dm, _ = _make_display()
        with patch("threading.Thread") as Thread:
            dm.show_image_url("http://x/y.jpg")
        Thread.assert_called_once()
        kwargs = Thread.call_args.kwargs
        assert kwargs.get("daemon") is True
        # Target should be _load_image with the URL and current token.
        assert kwargs["target"] == dm._load_image
        url, token = kwargs["args"]
        assert url == "http://x/y.jpg"
        assert token == dm._image_token

    def test_each_call_bumps_token(self):
        dm, _ = _make_display()
        with patch("threading.Thread"):
            dm.show_image_url("a")
            t1 = dm._image_token
            dm.show_image_url("b")
            t2 = dm._image_token
        assert t2 == t1 + 1


# ---------------------------------------------------------------------------
# Token-based cancellation — the core fix from C2
# ---------------------------------------------------------------------------

class TestLoadingAndMissing:
    def test_show_image_url_enters_loading_immediately(self):
        dm, _ = _make_display()
        dm._state = "SPEAKING"
        with patch("threading.Thread"):
            dm.show_image_url("http://x/y.jpg")
        assert dm._state == "LOADING"

    def test_show_image_url_during_image_does_not_drop_into_loading(self):
        """If we're already showing an image and a new one arrives, keep the
        old image on screen rather than flashing to LOADING in between."""
        dm, _ = _make_display()
        dm._state = "IMAGE"
        dm._image_override = MagicMock()
        with patch("threading.Thread"):
            dm.show_image_url("http://x/y.jpg")
        # State stays IMAGE; the token still bumps so the in-flight stale
        # fetch (if any) drops, and _load_image will swap in the new canvas
        # when it completes.
        assert dm._state == "IMAGE"

    def test_failed_fetch_enters_image_missing(self):
        dm, disp_mod = _make_display()
        dm._state = "SPEAKING"
        before = time.time()
        with patch.object(disp_mod, "_fetch_pil_image", return_value=None):
            dm._load_image("http://x/y.jpg", token=dm._image_token)
        assert dm._state == "IMAGE_MISSING"
        assert dm._image_override is None
        assert dm._image_expiry > before

    def test_failed_fetch_with_stale_token_does_not_change_state(self):
        dm, disp_mod = _make_display()
        dm._state = "LISTENING"
        stale = dm._image_token
        dm._image_token += 1  # someone moved on
        with patch.object(disp_mod, "_fetch_pil_image", return_value=None):
            dm._load_image("http://x/y.jpg", token=stale)
        assert dm._state == "LISTENING"  # unchanged

    def test_image_display_timer_starts_when_image_renders(self):
        """Pre-C3 the timer started at show_image_url(); now at first paint.
        We can't easily measure show_image_url latency in a unit test, so we
        assert the timer is set inside _load_image, not in show_image_url."""
        dm, disp_mod = _make_display()
        fake_img = MagicMock(width=100, height=100)
        with patch("threading.Thread"):
            dm.show_image_url("http://x/y.jpg")
        # After show_image_url alone, the timer is not yet set.
        assert dm._image_expiry == 0.0
        before = time.time()
        with patch.object(disp_mod, "_fetch_pil_image", return_value=fake_img), \
             patch.object(disp_mod, "_draw_battery"), \
             patch.dict(sys.modules,
                        {"PIL.Image": MagicMock(new=MagicMock(return_value=MagicMock(paste=MagicMock()))),
                         "PIL": MagicMock(ImageDraw=MagicMock(Draw=MagicMock(return_value=MagicMock())))}):
            dm._load_image("http://x/y.jpg", token=dm._image_token)
        assert dm._image_expiry > before


class TestImageMissingAutoRevert:
    def test_expired_image_missing_reverts_to_idle(self):
        dm, disp_mod = _make_display()
        dm._state = "IMAGE_MISSING"
        dm._image_expiry = time.time() - 1
        _run_one_tick(dm, disp_mod)
        assert dm._state == "IDLE"


class TestImageTokenCancellation:
    def test_set_state_off_image_bumps_token(self):
        dm, _ = _make_display()
        dm._state = "IMAGE"
        before = dm._image_token
        dm.set_state("LISTENING")
        assert dm._image_token == before + 1

    def test_stale_load_image_does_not_overwrite_fresh_state(self):
        """If a press lands during a download, the in-flight result must drop."""
        dm, disp_mod = _make_display()
        dm._state = "SPEAKING"
        # Simulate a press that lands while the download is in flight: the
        # press calls set_state("LISTENING") and bumps the token. The
        # _load_image that started before the press completes with the old
        # token and should silently discard.
        with patch("threading.Thread"):
            dm.show_image_url("http://x/y.jpg")
        stale_token = dm._image_token
        # The press happens — bump the token.
        dm.set_state("LISTENING")
        # Stale download now completes.
        fake_img = MagicMock(width=200, height=100)
        with patch.object(disp_mod, "_fetch_pil_image", return_value=fake_img), \
             patch.object(disp_mod, "_draw_battery"), \
             patch.dict(sys.modules,
                        {"PIL.Image": MagicMock(new=MagicMock(return_value=MagicMock(paste=MagicMock()))),
                         "PIL": MagicMock(ImageDraw=MagicMock(Draw=MagicMock(return_value=MagicMock())))}):
            dm._load_image("http://x/y.jpg", token=stale_token)
        # The fresh LISTENING state survives; no IMAGE override applied.
        assert dm._state == "LISTENING"
        assert dm._image_override is None

    def test_fresh_load_image_with_current_token_succeeds(self):
        dm, disp_mod = _make_display()
        fake_img = MagicMock(width=200, height=100)
        with patch("threading.Thread"):
            dm.show_image_url("http://x/y.jpg")
        token = dm._image_token
        with patch.object(disp_mod, "_fetch_pil_image", return_value=fake_img), \
             patch.object(disp_mod, "_draw_battery"), \
             patch.dict(sys.modules,
                        {"PIL.Image": MagicMock(new=MagicMock(return_value=MagicMock(paste=MagicMock()))),
                         "PIL": MagicMock(ImageDraw=MagicMock(Draw=MagicMock(return_value=MagicMock())))}):
            dm._load_image("http://x/y.jpg", token=token)
        assert dm._state == "IMAGE"
        assert dm._image_override is not None


# ---------------------------------------------------------------------------
# Every declared state has a dispatch arm in _render_face
# ---------------------------------------------------------------------------

class TestAllStatesDispatch:
    def test_every_face_state_has_a_dispatch_arm(self):
        """Static check on the source so the FACE_STATES tuple stays in sync
        with _render_face — would have caught any missing state when C3/C6
        add LOADING / IMAGE_MISSING / CURIOUS / BORED."""
        import inspect
        import pi_client.display as disp_mod
        source = inspect.getsource(disp_mod._render_face)
        for state in disp_mod.FACE_STATES:
            if state == "IMAGE":
                # IMAGE has no dispatch arm — the caller substitutes the
                # downloaded canvas and _render_face falls back to IDLE.
                continue
            assert f'"{state}"' in source, f"{state} missing from _render_face"
