"""
Waveshare 2.4" ILI9341 (320×240) animated robot face display.

States: IDLE, LISTENING, THINKING, SPEAKING, HAPPY, ERROR, IMAGE
Battery indicator top-right. All hardware calls guarded by ImportError
so the module loads and renders to a dummy canvas on non-Pi machines.
"""
import io
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Screen dimensions
W, H = 320, 240

# Colours (RGB) — tuned for cheap 320×240 LCDs where pure cyan washes out
# and soft pinks disappear against a dark navy background.
BG          = (20,  20,  40)   # dark navy
EYE_COLOR   = (80, 220, 230)   # warmer cyan — pops against BG on cheap panels
EYE_HILIGHT = (200, 255, 255)  # 1-px inner outline highlight for definition
PUPIL       = (10,  10,  30)
MOUTH_COLOR = (80, 220, 230)
BROW_COLOR  = (80, 220, 230)
CHEEK_COLOR = (255, 140, 150)  # slightly bolder pink so cheeks read at a glance
ERR_COLOR   = (220,  40,  40)
THINK_COLOR = (100, 200, 255)
BAT_GREEN   = (60,  200,  60)
BAT_YELLOW  = (220, 200,  40)
BAT_RED     = (220,  40,  40)
BAT_BG      = (60,   60,  80)
VOL_COLOR   = (0,  200, 200)   # cyan — same as eye colour
VOL_BG      = (40,  40,  60)
VOL_DISPLAY_SECONDS = 2

FACE_STATES = (
    "IDLE", "LISTENING", "THINKING", "SPEAKING", "HAPPY", "ERROR",
    "IMAGE",          # downloaded picture rendered via _image_override
    "LOADING",        # waiting for / fetching an image
    "IMAGE_MISSING",  # image fetch / decode failed — gentle "couldn't find it"
    "CURIOUS",        # bot just asked the child a question — pupils tilt, brow lifts
    "BORED",          # no input for a while — half-lid eyes, slow blink
)


# ---------------------------------------------------------------------------
# Hardware initialisation (guarded)
# ---------------------------------------------------------------------------

def _init_device():
    """Return an luma.lcd device or None if hardware unavailable."""
    try:
        from luma.lcd.device import ili9341
        from luma.core.interface.serial import spi
        from pi_client.config import DISPLAY_DC, DISPLAY_RST, DISPLAY_BL, DISPLAY_SPI_PORT, DISPLAY_FPS
        serial = spi(
            port=DISPLAY_SPI_PORT,
            device=0,
            gpio_DC=DISPLAY_DC,
            gpio_RST=DISPLAY_RST,
            gpio_BKLT=DISPLAY_BL,
            bus_speed_hz=32_000_000,
        )
        return ili9341(serial, width=W, height=H, rotate=0)
    except Exception as exc:
        logger.warning("Display hardware not available: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Battery reading
# ---------------------------------------------------------------------------

def _read_battery_percent() -> Optional[int]:
    import glob
    paths = glob.glob("/sys/class/power_supply/*/capacity")
    for path in paths:
        try:
            return int(open(path).read().strip())
        except (OSError, ValueError):
            pass
    return None


# ---------------------------------------------------------------------------
# Image fetching
# ---------------------------------------------------------------------------

def _fetch_pil_image(url: str, max_w: int, max_h: int):
    """Download a PIL image and fit it to max_w × max_h.

    Uses cover-crop (resize so the shorter side matches, centre-crop the long
    side) so the picture fills the screen for the common case. For images
    with an extreme aspect ratio mismatch (more than ~2.5×), falls back to
    letterbox so a banner photo doesn't get cropped to a nonsense slice.
    Returns None on download / decode / verify failure.
    """
    try:
        import urllib.request
        from PIL import Image
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read()
        # verify() walks the file looking for corruption without decoding
        # pixels — cheap defence against half-downloaded / broken images
        # that today decode "successfully" then render blank.
        Image.open(io.BytesIO(data)).verify()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        return _fit_image_to_canvas(img, max_w, max_h)
    except Exception as exc:
        logger.warning("Could not fetch display image: %s", exc)
        return None


_ASPECT_MISMATCH_LIMIT = 2.5  # falls back to letterbox above this


def _fit_image_to_canvas(img, max_w: int, max_h: int):
    """Cover-crop img to exactly (max_w, max_h), or letterbox if the aspect
    mismatch is extreme. Returns a new PIL Image; never mutates the input.
    """
    from PIL import Image
    src_w, src_h = img.width, img.height
    if src_w <= 0 or src_h <= 0:
        return None
    src_aspect = src_w / src_h
    dst_aspect = max_w / max_h
    # If the picture is wildly different shape from the screen, cover-crop
    # would throw away most of it. Letterbox instead so the child sees the
    # whole thing.
    if max(src_aspect / dst_aspect, dst_aspect / src_aspect) > _ASPECT_MISMATCH_LIMIT:
        scaled = img.copy()
        scaled.thumbnail((max_w, max_h), Image.LANCZOS)
        return scaled
    # Cover-crop: resize so the shorter side matches, centre-crop the long.
    if src_aspect > dst_aspect:
        # Source is wider — match height, crop horizontally.
        new_h = max_h
        new_w = int(round(src_w * (max_h / src_h)))
    else:
        # Source is taller — match width, crop vertically.
        new_w = max_w
        new_h = int(round(src_h * (max_w / src_w)))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - max_w) // 2
    top = (new_h - max_h) // 2
    return resized.crop((left, top, left + max_w, top + max_h))


# ---------------------------------------------------------------------------
# Face renderer
# ---------------------------------------------------------------------------

def _render_face(state: str, frame: int, battery: Optional[int]) -> "PIL.Image.Image":
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    _draw_battery(draw, battery)

    if state == "IMAGE":
        # Caller replaces the image later; show blank face as fallback
        state = "IDLE"

    cx, cy = W // 2, H // 2

    # --- eye positions ---
    eye_y  = cy - 25
    leye_x = cx - 55
    reye_x = cx + 55

    if state == "IDLE":
        _draw_idle_eyes(draw, leye_x, reye_x, eye_y, frame)
        _draw_mouth_smile(draw, cx, cy + 30, small=True)

    elif state == "LISTENING":
        _draw_circle_eyes(draw, leye_x, reye_x, eye_y, r=26)
        _draw_eyebrows_raised(draw, leye_x, reye_x, eye_y)
        _draw_mouth_open_o(draw, cx, cy + 30)

    elif state == "THINKING":
        _draw_rect_eyes(draw, leye_x, reye_x, eye_y)
        _draw_mouth_flat(draw, cx, cy + 30)
        _draw_thinking_dots(draw, cx, cy + 55, frame)

    elif state == "SPEAKING":
        _draw_idle_eyes(draw, leye_x, reye_x, eye_y, frame)  # blink-capable eyes
        _draw_mouth_speaking(draw, cx, cy + 30, frame)

    elif state == "HAPPY":
        # Gentle vertical bob (±2 px on a 12-frame cycle) so the excitement
        # reads as alive rather than a static grin.
        bob = (frame % 12) - 6
        bob = bob // 3  # -2..1
        _draw_happy_eyes(draw, leye_x, reye_x, eye_y + bob)
        _draw_cheeks(draw, leye_x, reye_x, eye_y + 40 + bob)
        _draw_mouth_smile(draw, cx, cy + 30 + bob, small=False)

    elif state == "ERROR":
        _draw_x_eyes(draw, leye_x, reye_x, eye_y)
        # Tiny mouth tremble — reads as worried rather than crashed.
        tremble = ((frame // 2) % 2) * 2 - 1  # alternates -1, +1
        _draw_mouth_frown(draw, cx, cy + 30 + tremble)

    elif state == "LOADING":
        # Same wide-eyed look as LISTENING but with rotating dots underneath
        # so the child sees the bot is reaching for something, not frozen.
        _draw_circle_eyes(draw, leye_x, reye_x, eye_y, r=22)
        _draw_mouth_flat(draw, cx, cy + 30)
        _draw_thinking_dots(draw, cx, cy + 55, frame)

    elif state == "IMAGE_MISSING":
        # Distinct from IDLE: pupils look down (searching, didn't find it),
        # sad slanted eyebrows, soft frown. Reads as apologetic — "I tried
        # but couldn't find a picture."
        _draw_searching_eyes(draw, leye_x, reye_x, eye_y)
        _draw_eyebrows_sad(draw, leye_x, reye_x, eye_y)
        _draw_mouth_frown(draw, cx, cy + 30)

    elif state == "CURIOUS":
        # Pupils offset to one side + one eyebrow lifted — reads as "hm?".
        _draw_curious_eyes(draw, leye_x, reye_x, eye_y)
        _draw_eyebrow_lift_right(draw, reye_x, eye_y)
        _draw_mouth_smile(draw, cx, cy + 30, small=True)

    elif state == "BORED":
        # Half-lid eyes + flat mouth — gentle "still here, ready when you are".
        _draw_bored_eyes(draw, leye_x, reye_x, eye_y, frame)
        _draw_mouth_flat(draw, cx, cy + 30)

    return img


# ---------------------------------------------------------------------------
# Eye drawing helpers
# ---------------------------------------------------------------------------

def _draw_circle_eyes(draw, lx, rx, y, r=22, color=None):
    color = color or EYE_COLOR
    for ex in (lx, rx):
        draw.ellipse([ex - r, y - r, ex + r, y + r], outline=color, width=3)
        draw.ellipse([ex - 8, y - 8, ex + 8, y + 8], fill=PUPIL)


def _draw_idle_eyes(draw, lx, rx, y, frame):
    """Normal eyes with slow blink every ~45 frames (4.5s at 10fps, 5.6s at 8fps).

    Adds two small touches so the face looks alive between blinks:
    - Pupils drift on a slow horizontal sine (~1-2 px, ~6s cycle), so the
      eyes aren't dead-still.
    - Every 4th blink is a double-blink (a second close 3 frames after the
      first), which reads as a more natural human tic.
    """
    in_blink_window = (frame % 45) in (42, 43, 44)
    # Double-blink follow-up: 3 frames after the first blink, every 4th cycle.
    cycle = (frame // 45) % 4
    in_second_blink = (cycle == 0) and ((frame % 45) in (38, 39))
    blinking = in_blink_window or in_second_blink
    # Slow horizontal pupil drift: ±2 px on a ~60-frame (6-7.5 s) cycle.
    # Integer arithmetic — no math.sin to avoid float churn every frame.
    drift_phase = (frame % 60) - 30  # -30..29
    drift_x = drift_phase // 15      # -2..1, mostly 0
    r = 22
    for ex in (lx, rx):
        if blinking:
            draw.line([ex - r, y, ex + r, y], fill=EYE_COLOR, width=4)
        else:
            draw.ellipse([ex - r, y - r, ex + r, y + r], outline=EYE_COLOR, width=3)
            # Hairline white inner highlight for definition on cheap LCDs.
            draw.ellipse([ex - r + 2, y - r + 2, ex + r - 2, y + r - 2],
                         outline=EYE_HILIGHT, width=1)
            draw.ellipse([ex - 8 + drift_x, y - 8,
                          ex + 8 + drift_x, y + 8], fill=PUPIL)


def _draw_rect_eyes(draw, lx, rx, y):
    """Narrow rectangles for THINKING."""
    for ex in (lx, rx):
        draw.rectangle([ex - 20, y - 6, ex + 20, y + 6], outline=THINK_COLOR, width=3)


def _draw_happy_eyes(draw, lx, rx, y):
    """^ ^ crescent arcs."""
    r = 22
    for ex in (lx, rx):
        draw.arc([ex - r, y - r, ex + r, y + r], start=200, end=340, fill=EYE_COLOR, width=4)


def _draw_x_eyes(draw, lx, rx, y):
    """× for ERROR state."""
    s = 18
    for ex in (lx, rx):
        draw.line([ex - s, y - s, ex + s, y + s], fill=ERR_COLOR, width=4)
        draw.line([ex + s, y - s, ex - s, y + s], fill=ERR_COLOR, width=4)


def _draw_curious_eyes(draw, lx, rx, y):
    """Open eyes with pupils offset to the right — head-tilt 'hm?' look."""
    r = 22
    pupil_offset = 6  # px to the right
    for ex in (lx, rx):
        draw.ellipse([ex - r, y - r, ex + r, y + r], outline=EYE_COLOR, width=3)
        draw.ellipse([ex - r + 2, y - r + 2, ex + r - 2, y + r - 2],
                     outline=EYE_HILIGHT, width=1)
        draw.ellipse([ex - 8 + pupil_offset, y - 8,
                      ex + 8 + pupil_offset, y + 8], fill=PUPIL)


def _draw_searching_eyes(draw, lx, rx, y):
    """Open eyes with pupils dropped low — the universal 'looked around and
    didn't find it' look. Used for IMAGE_MISSING."""
    r = 22
    pupil_drop = 8  # px below centre
    for ex in (lx, rx):
        draw.ellipse([ex - r, y - r, ex + r, y + r], outline=EYE_COLOR, width=3)
        draw.ellipse([ex - r + 2, y - r + 2, ex + r - 2, y + r - 2],
                     outline=EYE_HILIGHT, width=1)
        draw.ellipse([ex - 8, y - 8 + pupil_drop,
                      ex + 8, y + 8 + pupil_drop], fill=PUPIL)


def _draw_eyebrows_sad(draw, lx, rx, ey):
    """Inner-corners raised, outer-corners drooping — sad/apologetic brows
    (the universal '/  \\' shape, mirrored)."""
    by = ey - 36
    # Left eyebrow: outer end low, inner end high
    draw.line([lx - 18, by + 4, lx + 14, by - 6], fill=BROW_COLOR, width=3)
    # Right eyebrow: inner end high, outer end low (mirror)
    draw.line([rx - 14, by - 6, rx + 18, by + 4], fill=BROW_COLOR, width=3)


def _draw_bored_eyes(draw, lx, rx, y, frame):
    """Half-lid eyes (upper half hidden by a lid line). Slow blink ~9s cycle."""
    in_blink = (frame % 90) in (87, 88, 89)
    r = 22
    for ex in (lx, rx):
        if in_blink:
            draw.line([ex - r, y, ex + r, y], fill=EYE_COLOR, width=4)
        else:
            # Lower half-circle only — the lid is a horizontal line across
            # the middle.
            draw.arc([ex - r, y - r, ex + r, y + r], start=0, end=180,
                     fill=EYE_COLOR, width=3)
            draw.line([ex - r, y, ex + r, y], fill=EYE_COLOR, width=3)
            draw.ellipse([ex - 6, y - 2, ex + 6, y + 6], fill=PUPIL)


# ---------------------------------------------------------------------------
# Eyebrow helpers
# ---------------------------------------------------------------------------

def _draw_eyebrows_raised(draw, lx, rx, ey):
    for ex in (lx, rx):
        sign = -1 if ex < W // 2 else 1
        draw.line([ex - 18, ey - 38, ex + 18 * sign, ey - 48],
                  fill=BROW_COLOR, width=3)


def _draw_eyebrow_lift_right(draw, rx, ey):
    """A single lifted eyebrow over the right eye — the universal 'hm?' look."""
    draw.line([rx - 16, ey - 36, rx + 16, ey - 46], fill=BROW_COLOR, width=3)


# ---------------------------------------------------------------------------
# Mouth helpers
# ---------------------------------------------------------------------------

def _draw_mouth_smile(draw, cx, y, small=True):
    w = 40 if small else 65
    draw.arc([cx - w, y - 20, cx + w, y + 20], start=0, end=180, fill=MOUTH_COLOR, width=3)


def _draw_mouth_frown(draw, cx, y):
    draw.arc([cx - 45, y, cx + 45, y + 30], start=180, end=0, fill=ERR_COLOR, width=3)


def _draw_mouth_flat(draw, cx, y):
    draw.line([cx - 35, y, cx + 35, y], fill=MOUTH_COLOR, width=3)


def _draw_mouth_open_o(draw, cx, y):
    draw.ellipse([cx - 22, y - 15, cx + 22, y + 15], outline=MOUTH_COLOR, width=3)


# Mouth heights — non-uniform pattern so the cadence doesn't read as a
# perfect 6-frame metronome. The trailing 0 holds the mouth briefly closed,
# the way a real speaker pauses between words.
_SPEAK_HEIGHTS = [0, 10, 18, 24, 20, 8, 0, 14, 22, 6]


def _draw_mouth_speaking(draw, cx, y, frame):
    h = _SPEAK_HEIGHTS[(frame // 3) % len(_SPEAK_HEIGHTS)]
    if h == 0:
        draw.line([cx - 35, y, cx + 35, y], fill=MOUTH_COLOR, width=3)
    else:
        draw.arc([cx - 35, y - h, cx + 35, y + h], start=0, end=180, fill=MOUTH_COLOR, width=3)


# ---------------------------------------------------------------------------
# Thinking dots
# ---------------------------------------------------------------------------

def _draw_thinking_dots(draw, cx, y, frame):
    # Speeded up from frame//5 → frame//3 (~1.1s cycle at 10fps instead of
    # ~1.9s) so the THINKING face doesn't read as frozen.
    for i in range(3):
        visible = ((frame // 3) % 4) > i
        color = THINK_COLOR if visible else BG
        dx = cx - 20 + i * 20
        draw.ellipse([dx - 5, y - 5, dx + 5, y + 5], fill=color)


# ---------------------------------------------------------------------------
# Cheeks (HAPPY)
# ---------------------------------------------------------------------------

def _draw_cheeks(draw, lx, rx, y):
    for ex, sign in ((lx, -1), (rx, 1)):
        cx_ = ex + sign * 5
        draw.ellipse([cx_ - 14, y - 7, cx_ + 14, y + 7], fill=CHEEK_COLOR)


# ---------------------------------------------------------------------------
# Volume overlay
# ---------------------------------------------------------------------------

def _draw_volume_overlay(draw, pct: int) -> None:
    """Draw a bottom-centre volume bar (180×18 px) on top of the current frame."""
    bar_w, bar_h = 180, 18
    bx = (W - bar_w) // 2
    by = H - bar_h - 6

    # Background pill
    draw.rectangle([bx, by, bx + bar_w, by + bar_h], fill=VOL_BG)
    # Fill
    fill_w = max(2, int((bar_w - 4) * max(0, min(100, pct)) / 100))
    draw.rectangle([bx + 2, by + 2, bx + 2 + fill_w, by + bar_h - 2], fill=VOL_COLOR)


# ---------------------------------------------------------------------------
# Battery indicator
# ---------------------------------------------------------------------------

def _draw_battery(draw, pct: Optional[int]):
    bx, by = W - 56, 6
    bw, bh = 44, 18
    nub_w, nub_h = 4, 8

    draw.rectangle([bx, by, bx + bw, by + bh], outline=BAT_BG, width=2)
    draw.rectangle([bx + bw, by + (bh - nub_h) // 2,
                    bx + bw + nub_w, by + (bh + nub_h) // 2], fill=BAT_BG)

    if pct is None:
        draw.line([bx + bw // 2, by + 2, bx + bw // 2, by + bh - 2], fill=BAT_BG, width=2)
        return

    fill_w = max(2, int((bw - 4) * pct / 100))
    color = BAT_GREEN if pct > 50 else (BAT_YELLOW if pct > 20 else BAT_RED)
    draw.rectangle([bx + 2, by + 2, bx + 2 + fill_w, by + bh - 2], fill=color)


# ---------------------------------------------------------------------------
# DisplayManager
# ---------------------------------------------------------------------------

class DisplayManager:
    """
    Drives the 320×240 ILI9341 LCD with an animated robot face.

    All state changes are thread-safe.  Call set_state() from any thread.
    Call show_image_url() to enter IMAGE state with a downloaded picture.
    Call cleanup() on shutdown.
    """

    def __init__(self):
        self._device = _init_device()
        self._state = "IDLE"
        self._image_override = None   # PIL Image while in IMAGE state
        self._image_expiry = 0.0
        # Token incremented on every state change and every show_image_url(),
        # so an in-flight _load_image can detect that the conversation has
        # moved on and quietly drop its result instead of overwriting a fresh
        # face with a stale picture. Avoids the one-frame flash where a press
        # mid-IMAGE would briefly show the old image under LISTENING.
        self._image_token = 0

        self._vol_pct: Optional[int] = None
        self._vol_expiry = 0.0

        self._battery: Optional[int] = None
        self._lock = threading.Lock()
        self._running = True

        from pi_client.config import DISPLAY_FPS
        self._frame_sleep = 1.0 / max(1, DISPLAY_FPS)

        self._render_thread = threading.Thread(target=self._animate, daemon=True, name="display-render")
        self._battery_thread = threading.Thread(target=self._poll_battery, daemon=True, name="display-battery")
        self._render_thread.start()
        self._battery_thread.start()
        logger.info("DisplayManager started (device=%s, fps=%d)", "hardware" if self._device else "headless", DISPLAY_FPS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_state(self, state: str) -> None:
        if state not in FACE_STATES:
            logger.warning("Unknown display state: %s", state)
            return
        with self._lock:
            self._state = state
            if state != "IMAGE":
                self._image_override = None
                # Any in-flight _load_image is now stale — invalidate its token.
                self._image_token += 1

    def show_image_url(self, url: str) -> None:
        """Download url in a background thread and switch to IMAGE state.

        Enters LOADING immediately so the child sees the bot reaching for
        something, captures a token before launching the fetch; if a later
        set_state / show_image_url bumps the token, the in-flight fetch
        silently discards its result on completion."""
        with self._lock:
            self._image_token += 1
            token = self._image_token
            # Don't go through set_state — set_state would bump the token
            # again and invalidate the token we just captured for the fetch.
            if self._state != "IMAGE":
                self._state = "LOADING"
                self._image_override = None
        threading.Thread(target=self._load_image, args=(url, token), daemon=True).start()

    def show_volume(self, pct: int) -> None:
        """Show a transient volume bar overlay for VOL_DISPLAY_SECONDS seconds."""
        with self._lock:
            self._vol_pct = int(pct)
            self._vol_expiry = time.time() + VOL_DISPLAY_SECONDS

    def cleanup(self) -> None:
        self._running = False
        if self._device:
            try:
                self._device.cleanup()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_image(self, url: str, token: int) -> None:
        from pi_client.config import IMAGE_DISPLAY_SECONDS, IMAGE_MISSING_SECONDS
        img = _fetch_pil_image(url, W, H - 24)
        if img is None:
            # Surface the failure: the child waited, give them feedback
            # instead of silently going back to IDLE.
            with self._lock:
                if token != self._image_token:
                    return  # user already moved on
                self._state = "IMAGE_MISSING"
                self._image_override = None
                self._image_expiry = time.time() + IMAGE_MISSING_SECONDS
            return
        # img is already sized to (W, H - 24) by _fit_image_to_canvas in the
        # cover-crop branch, or letterboxed inside that box in the fallback;
        # either way centre-pasting at (0, 24) is correct.
        canvas = __import__("PIL.Image", fromlist=["Image"]).new("RGB", (W, H), BG)
        x = (W - img.width) // 2
        y = 24 + ((H - 24) - img.height) // 2
        canvas.paste(img, (x, y))
        # Draw battery on top
        from PIL import ImageDraw
        draw = ImageDraw.Draw(canvas)
        with self._lock:
            _draw_battery(draw, self._battery)
        with self._lock:
            # If a later state change has bumped the token, the user has moved
            # on — drop the image instead of overwriting a fresh face state.
            if token != self._image_token:
                logger.debug("Discarding stale image (token %d != %d)",
                             token, self._image_token)
                return
            self._image_override = canvas
            self._state = "IMAGE"
            # The timer starts NOW, when the image actually appears, so a
            # slow fetch can't burn through the display budget before the
            # child sees anything.
            self._image_expiry = time.time() + IMAGE_DISPLAY_SECONDS

    def _animate(self) -> None:
        frame = 0
        while self._running:
            # Headless (display not wired): there is nothing to draw, so skip
            # the per-frame PIL render entirely instead of building and
            # discarding a 320x240 image every tick.
            if self._device is None:
                time.sleep(self._frame_sleep)
                continue

            now = time.time()
            with self._lock:
                state = self._state
                override = self._image_override
                battery = self._battery
                img_expiry = self._image_expiry
                vol_pct = self._vol_pct
                vol_expiry = self._vol_expiry

            # Auto-revert IMAGE / IMAGE_MISSING state after timeout. Both
            # are time-bound modes that should give way to IDLE; we don't
            # bump the token here because no in-flight fetch is being
            # cancelled — the timer simply expired.
            if state in ("IMAGE", "IMAGE_MISSING") and now > img_expiry:
                with self._lock:
                    self._state = "IDLE"
                    self._image_override = None
                state = "IDLE"
                override = None

            # Auto-clear volume overlay after timeout
            if vol_pct is not None and now > vol_expiry:
                with self._lock:
                    self._vol_pct = None
                vol_pct = None

            if state == "IMAGE" and override is not None:
                frame_img = override.copy()
            else:
                frame_img = _render_face(state, frame, battery)

            # Draw transient volume bar on top of whatever is on screen
            if vol_pct is not None:
                from PIL import ImageDraw
                _draw_volume_overlay(ImageDraw.Draw(frame_img), vol_pct)

            if self._device:
                try:
                    self._device.display(frame_img)
                except Exception as exc:
                    logger.debug("Display render error: %s", exc)

            frame = (frame + 1) % 1000
            time.sleep(self._frame_sleep)

    def _poll_battery(self) -> None:
        while self._running:
            pct = _read_battery_percent()
            with self._lock:
                self._battery = pct
            time.sleep(30)
