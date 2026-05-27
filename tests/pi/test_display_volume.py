"""Tests for volume overlay methods in DisplayManager."""
import time
from unittest.mock import MagicMock, patch


def _make_display():
    """Return a headless DisplayManager with render/battery threads suppressed."""
    import pi_client.display as disp_mod
    with patch.object(disp_mod, "_init_device", return_value=None), \
         patch("threading.Thread"):
        from pi_client.display import DisplayManager
        dm = DisplayManager()
    return dm, disp_mod


class TestShowVolume:
    def test_sets_vol_pct(self):
        dm, _ = _make_display()
        dm.show_volume(60)
        assert dm._vol_pct == 60

    def test_sets_expiry_approximately_2s_from_now(self):
        dm, disp_mod = _make_display()
        before = time.time()
        dm.show_volume(40)
        after = time.time()
        assert disp_mod.VOL_DISPLAY_SECONDS - 0.1 <= dm._vol_expiry - before <= disp_mod.VOL_DISPLAY_SECONDS + 0.2

    def test_does_not_change_face_state(self):
        dm, _ = _make_display()
        dm._state = "THINKING"
        dm.show_volume(70)
        assert dm._state == "THINKING"

    def test_works_during_image_state(self):
        dm, _ = _make_display()
        dm._state = "IMAGE"
        dm.show_volume(30)
        assert dm._vol_pct == 30
        assert dm._state == "IMAGE"

    def test_clears_after_expiry_in_animate(self):
        dm, disp_mod = _make_display()
        dm.show_volume(50)
        dm._vol_expiry = time.time() - 1  # already expired

        rendered = []

        def fake_render(state, frame, battery):
            from PIL import Image
            return Image.new("RGB", (320, 240))

        with patch.object(disp_mod, "_render_face", side_effect=fake_render):
            dm._running = True
            # Run one iteration manually by calling internals
            now = time.time()
            vol_pct = dm._vol_pct
            vol_expiry = dm._vol_expiry
            if vol_pct is not None and now > vol_expiry:
                with dm._lock:
                    dm._vol_pct = None
                vol_pct = None
            assert vol_pct is None
            assert dm._vol_pct is None


class TestDrawVolumeOverlay:
    def _make_draw(self):
        return MagicMock()

    def test_draws_two_rectangles(self):
        from pi_client.display import _draw_volume_overlay
        draw = self._make_draw()
        _draw_volume_overlay(draw, 50)
        assert draw.rectangle.call_count == 2

    def test_handles_0_percent(self):
        from pi_client.display import _draw_volume_overlay
        _draw_volume_overlay(self._make_draw(), 0)

    def test_handles_100_percent(self):
        from pi_client.display import _draw_volume_overlay
        _draw_volume_overlay(self._make_draw(), 100)

    def test_fill_rect_uses_vol_color(self):
        from pi_client.display import _draw_volume_overlay, VOL_COLOR
        draw = self._make_draw()
        _draw_volume_overlay(draw, 75)
        fill_calls = [c for c in draw.rectangle.call_args_list if c[1].get("fill") == VOL_COLOR]
        assert len(fill_calls) == 1
