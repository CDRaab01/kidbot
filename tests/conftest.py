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


# ollama — used by server/llm.py
_stub("ollama", list=MagicMock(), chat=MagicMock())

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

# tkinter — not available for Python 3.11 in this environment; stub for import only
_TK_CONSTANTS = dict(
    X="x", Y="y", BOTH="both", LEFT="left", RIGHT="right",
    TOP="top", BOTTOM="bottom", N="n", S="s", E="e", W="w",
    NORMAL="normal", DISABLED="disabled", END="end",
    FLAT="flat", WORD="word", RAISED="raised",
)
_tk = _stub("tkinter", **_TK_CONSTANTS,
            Tk=MagicMock, Frame=MagicMock, Label=MagicMock, Button=MagicMock,
            Entry=MagicMock, Canvas=MagicMock, StringVar=MagicMock)
_stub("tkinter.ttk", Combobox=MagicMock)
_stub("tkinter.scrolledtext", ScrolledText=MagicMock)
# Make sub-modules accessible as attributes (from tkinter import scrolledtext, ttk)
import types as _types
_tk.ttk = sys.modules["tkinter.ttk"]
_tk.scrolledtext = sys.modules["tkinter.scrolledtext"]
