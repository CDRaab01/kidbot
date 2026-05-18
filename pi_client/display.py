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

# Colours (RGB)
BG          = (20,  20,  40)   # dark navy
EYE_COLOR   = (0,  220, 220)   # cyan
PUPIL       = (10,  10,  30)
MOUTH_COLOR = (0,  220, 220)
BROW_COLOR  = (0,  220, 220)
CHEEK_COLOR = (255, 130, 130)
ERR_COLOR   = (220,  40,  40)
THINK_COLOR = (100, 200, 255)
BAT_GREEN   = (60,  200,  60)
BAT_YELLOW  = (220, 200,  40)
BAT_RED     = (220,  40,  40)
BAT_BG      = (60,   60,  80)

FACE_STATES = ("IDLE", "LISTENING", "THINKING", "SPEAKING", "HAPPY", "ERROR", "IMAGE")


# ---------------------------------------------------------------------------
# Hardware initialisation (guarded)
# ---------------------------------------------------------------------------

def _init_device():
    """Return an luma.lcd device or None if hardware unavailable."""
    try:
        from luma.lcd.device import ili9341
        from luma.core.interface.serial import spi
        from pi_client.config import DISPLAY_DC, DISPLAY_RST, DISPLAY_BL, DISPLAY_SPI_PORT
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
    """Download and resize a PIL image to fit within max_w x max_h."""
    try:
        import urllib.request
        from PIL import Image
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((max_w, max_h), Image.LANCZOS)
        return img
    except Exception as exc:
        logger.warning("Could not fetch display image: %s", exc)
        return None


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
        _draw_circle_eyes(draw, leye_x, reye_x, eye_y, r=22)
        _draw_mouth_speaking(draw, cx, cy + 30, frame)

    elif state == "HAPPY":
        _draw_happy_eyes(draw, leye_x, reye_x, eye_y)
        _draw_cheeks(draw, leye_x, reye_x, eye_y + 40)
        _draw_mouth_smile(draw, cx, cy + 30, small=False)

    elif state == "ERROR":
        _draw_x_eyes(draw, leye_x, reye_x, eye_y)
        _draw_mouth_frown(draw, cx, cy + 30)

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
    """Normal eyes with slow blink every ~4.5 s (at 10 fps = 45 frames)."""
    blinking = (frame % 45) in (42, 43, 44)
    r = 22
    for ex in (lx, rx):
        if blinking:
            draw.line([ex - r, y, ex + r, y], fill=EYE_COLOR, width=4)
        else:
            draw.ellipse([ex - r, y - r, ex + r, y + r], outline=EYE_COLOR, width=3)
            draw.ellipse([ex - 8, y - 8, ex + 8, y + 8], fill=PUPIL)


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


# ---------------------------------------------------------------------------
# Eyebrow helpers
# ---------------------------------------------------------------------------

def _draw_eyebrows_raised(draw, lx, rx, ey):
    for ex in (lx, rx):
        sign = -1 if ex < W // 2 else 1
        draw.line([ex - 18, ey - 38, ex + 18 * sign, ey - 48],
                  fill=BROW_COLOR, width=3)


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


_SPEAK_FRAMES = [
    ("flat",   0),
    ("arc_sm", 10),
    ("arc_md", 20),
    ("oval",   0),
    ("arc_md", 20),
    ("arc_sm", 10),
]


def _draw_mouth_speaking(draw, cx, y, frame):
    phase = _SPEAK_FRAMES[(frame // 2) % len(_SPEAK_FRAMES)]
    kind, h = phase
    if kind == "flat":
        draw.line([cx - 35, y, cx + 35, y], fill=MOUTH_COLOR, width=3)
    elif kind in ("arc_sm", "arc_md"):
        draw.arc([cx - 35, y - h, cx + 35, y + h], start=0, end=180, fill=MOUTH_COLOR, width=3)
    elif kind == "oval":
        draw.ellipse([cx - 22, y - 18, cx + 22, y + 18], outline=MOUTH_COLOR, width=3)


# ---------------------------------------------------------------------------
# Thinking dots
# ---------------------------------------------------------------------------

def _draw_thinking_dots(draw, cx, y, frame):
    for i in range(3):
        visible = ((frame // 5) % 3) >= i
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

        self._battery: Optional[int] = None
        self._lock = threading.Lock()
        self._running = True

        self._render_thread = threading.Thread(target=self._animate, daemon=True, name="display-render")
        self._battery_thread = threading.Thread(target=self._poll_battery, daemon=True, name="display-battery")
        self._render_thread.start()
        self._battery_thread.start()
        logger.info("DisplayManager started (device=%s)", "hardware" if self._device else "headless")

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

    def show_image_url(self, url: str) -> None:
        """Download url in a background thread and switch to IMAGE state."""
        threading.Thread(target=self._load_image, args=(url,), daemon=True).start()

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

    def _load_image(self, url: str) -> None:
        from pi_client.config import IMAGE_DISPLAY_SECONDS
        img = _fetch_pil_image(url, W, H - 24)
        if img is None:
            return
        # Centre on dark background
        canvas = __import__("PIL.Image", fromlist=["Image"]).new("RGB", (W, H), BG)
        x = (W - img.width) // 2
        y = 24 + (H - 24 - img.height) // 2
        canvas.paste(img, (x, y))
        # Draw battery on top
        from PIL import ImageDraw
        draw = ImageDraw.Draw(canvas)
        with self._lock:
            _draw_battery(draw, self._battery)
        with self._lock:
            self._image_override = canvas
            self._state = "IMAGE"
            self._image_expiry = time.time() + IMAGE_DISPLAY_SECONDS

    def _animate(self) -> None:
        frame = 0
        while self._running:
            with self._lock:
                state = self._state
                override = self._image_override
                battery = self._battery
                expiry = self._image_expiry

            # Auto-revert IMAGE state after timeout
            if state == "IMAGE" and time.time() > expiry:
                with self._lock:
                    self._state = "IDLE"
                    self._image_override = None
                state = "IDLE"
                override = None

            if state == "IMAGE" and override is not None:
                frame_img = override
            else:
                frame_img = _render_face(state, frame, battery)

            if self._device:
                try:
                    self._device.display(frame_img)
                except Exception as exc:
                    logger.debug("Display render error: %s", exc)

            frame = (frame + 1) % 1000
            time.sleep(0.1)

    def _poll_battery(self) -> None:
        while self._running:
            pct = _read_battery_percent()
            with self._lock:
                self._battery = pct
            time.sleep(30)
