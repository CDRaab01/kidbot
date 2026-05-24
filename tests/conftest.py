"""
Shared module stubs for packages not installed in the test environment.
These are registered in sys.modules before any server code is imported,
so all test files benefit automatically.
"""
import sys
from types import ModuleType
from unittest.mock import MagicMock


def _stub(name: str, **attrs) -> ModuleType:
    mod = ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# openai — used by server/llm.py (LM Studio client)
_stub("openai", OpenAI=MagicMock())

# faster_whisper — used by server/stt.py
# Use a MagicMock() instance so WhisperModel(path, ...) doesn't pick up spec=str
_stub("faster_whisper", WhisperModel=MagicMock())

# kokoro_onnx — used by server/tts.py
_stub("kokoro_onnx", Kokoro=MagicMock())

# soundfile — used by server/tts.py
_stub("soundfile", write=MagicMock())

# sounddevice — used by test_gui.py
_stub(
    "sounddevice",
    query_devices=MagicMock(return_value=[]),
    InputStream=MagicMock,
    play=MagicMock(),
    wait=MagicMock(),
    stop=MagicMock(),
    rec=MagicMock(),
)

# PIL (Pillow) — used by test_gui.py for image display
_pil = _stub("PIL", Image=MagicMock, ImageTk=MagicMock)
_stub("PIL.Image", Image=MagicMock)
_stub("PIL.ImageTk", ImageTk=MagicMock)
_pil.Image = sys.modules["PIL.Image"]
_pil.ImageTk = sys.modules["PIL.ImageTk"]

# RPi.GPIO — not available outside the Pi; stub for import only
_gpio_mod = _stub("RPi", GPIO=MagicMock())
_gpio_stub = MagicMock()
_gpio_stub.BCM    = 11
_gpio_stub.IN     = 1
_gpio_stub.OUT    = 0
_gpio_stub.PUD_UP = 22
_gpio_stub.FALLING = 31
_gpio_stub.RISING  = 32
_gpio_stub.BOTH    = 33
_gpio_stub.HIGH    = 1
_gpio_stub.LOW     = 0
_stub("RPi.GPIO")
sys.modules["RPi.GPIO"] = _gpio_stub
_gpio_mod.GPIO = _gpio_stub

# tkinter — not available for Python 3.11 in this environment; stub for import only
_TK_CONSTANTS = dict(
    X="x", Y="y", BOTH="both", LEFT="left", RIGHT="right",
    TOP="top", BOTTOM="bottom", N="n", S="s", E="e", W="w",
    NORMAL="normal", DISABLED="disabled", END="end",
    FLAT="flat", WORD="word", RAISED="raised",
)
_tk = _stub("tkinter", **_TK_CONSTANTS,
            Tk=MagicMock, Frame=MagicMock, Label=MagicMock, Button=MagicMock,
            Entry=MagicMock, Canvas=MagicMock, StringVar=MagicMock,
            Widget=MagicMock)
_stub("tkinter.ttk", Combobox=MagicMock)
_stub("tkinter.scrolledtext", ScrolledText=MagicMock)
# Make sub-modules accessible as attributes (from tkinter import scrolledtext, ttk)
import types as _types
_tk.ttk = sys.modules["tkinter.ttk"]
_tk.scrolledtext = sys.modules["tkinter.scrolledtext"]
