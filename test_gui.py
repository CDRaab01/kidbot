"""
CooperBot Test GUI
------------------
SPACE      : Hold to record, release to send / interrupt playback
ENTER      : Send typed text directly (bypasses speech recognition)
ESC        : Clear the text input
"""

import io
import os
import queue
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import scrolledtext, ttk
import wave

import numpy as np
from PIL import Image, ImageTk
import requests
import sounddevice as sd

_RESAMPLE = getattr(Image, "LANCZOS", getattr(Image, "ANTIALIAS", 1))

SERVER_URL   = "http://localhost:8765"
SESSION_ID   = "gui-session"
SAMPLE_RATE  = 16000


def _beep(freq: int, duration: float = 0.08, volume: float = 0.22) -> None:
    """Play a short sine-wave beep (non-blocking after wait)."""
    try:
        sr = 22050
        n  = int(sr * duration)
        t  = np.linspace(0, duration, n, False)
        tone = np.sin(2 * np.pi * freq * t).astype(np.float32)
        fade = min(int(sr * 0.012), n // 4)
        tone[:fade]  *= np.linspace(0, 1, fade)
        tone[-fade:] *= np.linspace(1, 0, fade)
        sd.play((tone * volume * 32767).astype(np.int16), samplerate=sr)
        sd.wait()
    except Exception:
        pass

STATUS_COLORS = {
    "IDLE":       "#27ae60",
    "RECORDING":  "#e74c3c",
    "PROCESSING": "#f39c12",
    "PLAYING":    "#2980b9",
}


class FacePanel:
    """
    Emulated 320×240 KidBot LCD display rendered inside a tkinter Canvas.

    Renders the same robot face as pi_client/display.py, scaled to fit
    the panel.  Call set_state() with GUI states; call show_image_url()
    to download and display a photo.
    """

    CANVAS_W = 240
    CANVAS_H = 180
    SRC_W    = 320
    SRC_H    = 240

    _STATE_MAP = {
        "IDLE":       "IDLE",
        "RECORDING":  "LISTENING",
        "PROCESSING": "THINKING",
        "PLAYING":    "SPEAKING",
    }

    def __init__(self, parent: tk.Widget, root: tk.Tk):
        self._root         = root
        self._face_state   = "IDLE"
        self._frame_idx    = 0
        self._photo        = None        # PhotoImage reference — prevents GC
        self._image_over   = None        # PIL Image while in IMAGE state
        self._image_expiry = 0.0
        self._lock         = threading.Lock()

        outer = tk.Frame(parent, bg="#1a252f")
        outer.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            outer, text="KidBot Screen",
            font=("Segoe UI", 10, "bold"), fg="#bdc3c7", bg="#1a252f",
        ).pack(pady=(8, 2))

        bezel = tk.Frame(outer, bg="#0a0a18", bd=3, relief=tk.SUNKEN)
        bezel.pack(padx=10, pady=4)

        self._canvas = tk.Canvas(
            bezel, width=self.CANVAS_W, height=self.CANVAS_H,
            bg="#141428", highlightthickness=0,
        )
        self._canvas.pack()

        tk.Label(
            outer, text="320×240 (scaled)",
            font=("Segoe UI", 8), fg="#4a5568", bg="#1a252f",
        ).pack(pady=(2, 0))

        self._tick()

    def set_state(self, gui_state: str) -> None:
        """Map a GUI state to a face state, respecting active IMAGE display."""
        face = self._STATE_MAP.get(gui_state, "IDLE")
        with self._lock:
            if self._face_state == "IMAGE" and face == "IDLE":
                return  # let image expire naturally
            self._face_state = face
            if face != "IMAGE":
                self._image_over = None

    def set_face_state(self, face_state: str) -> None:
        """Set face state directly (bypasses GUI-state mapping). Respects active IMAGE."""
        with self._lock:
            if self._face_state == "IMAGE":
                return  # image is showing — don't override it
            self._face_state = face_state
            if face_state != "IMAGE":
                self._image_over = None

    def show_image_url(self, url: str) -> None:
        """Download and display an image, auto-reverting to IDLE after 8 s."""
        threading.Thread(target=self._load_image, args=(url,), daemon=True).start()

    def _load_image(self, url: str) -> None:
        try:
            resp = requests.get(
                url, timeout=8,
                headers={"User-Agent": "CooperBot/1.0 (educational child chatbot)"},
            )
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            max_w, max_h = self.SRC_W, self.SRC_H - 24
            img.thumbnail((max_w, max_h), _RESAMPLE)
            canvas_img = Image.new("RGB", (self.SRC_W, self.SRC_H), (20, 20, 40))
            x = (self.SRC_W - img.width) // 2
            y = 24 + (max_h - img.height) // 2
            canvas_img.paste(img, (x, y))
            with self._lock:
                self._image_over   = canvas_img
                self._face_state   = "IMAGE"
                self._image_expiry = time.time() + 8
        except Exception as exc:
            pass  # silently ignore broken images

    def _tick(self) -> None:
        with self._lock:
            state  = self._face_state
            over   = self._image_over
            expiry = self._image_expiry
            fidx   = self._frame_idx

        if state == "IMAGE" and time.time() > expiry:
            with self._lock:
                self._face_state = "IDLE"
                self._image_over = None
            state = "IDLE"
            over  = None

        if state == "IMAGE" and over is not None:
            src_img = over.copy()
        else:
            from pi_client.display import _render_face
            src_img = _render_face(state, fidx, None)

        display_img = src_img.resize((self.CANVAS_W, self.CANVAS_H), _RESAMPLE)
        photo = ImageTk.PhotoImage(display_img)
        self._photo = photo  # hold reference
        self._canvas.create_image(0, 0, anchor=tk.NW, image=photo)

        with self._lock:
            self._frame_idx = (fidx + 1) % 1000

        self._root.after(100, self._tick)


class CooperBotGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CooperBot - Test Console")
        self.root.geometry("960x560")
        self.root.minsize(700, 420)
        self.root.configure(bg="#2c3e50")
        self.root.resizable(True, True)

        self.state = "IDLE"
        self._recording = False
        self._rec_buffer = None
        self._rec_start = 0.0
        self._ui_queue: queue.Queue = queue.Queue()
        self._input_devices: list[dict] = self._get_input_devices()
        self._selected_device_index: int | None = None
        self._images: list = []  # keep PhotoImage refs alive (prevents GC)
        self._current_proc = None  # active ffmpeg process for interrupt
        self.face: FacePanel | None = None

        self._build_ui()
        self._bind_keys()
        self._poll_queue()
        self._log("System", "Connected to CooperBot. Press SPACE to talk.")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#1a252f", pady=8)
        header.pack(fill=tk.X)
        tk.Label(
            header, text="CooperBot  Test Console",
            font=("Segoe UI", 15, "bold"), fg="#ecf0f1", bg="#1a252f"
        ).pack()
        tk.Button(
            header, text="⚙ Settings", command=self._open_settings,
            font=("Segoe UI", 9), bg="#2c3e50", fg="#bdc3c7",
            relief=tk.FLAT, padx=8, pady=2, activebackground="#3d5166",
        ).place(relx=1.0, rely=0.5, anchor=tk.E, x=-10)

        # Microphone selector — top of window, always visible
        mic_row = tk.Frame(self.root, bg="#2c3e50", pady=5)
        mic_row.pack(fill=tk.X, padx=12)
        tk.Label(mic_row, text="Mic:", font=("Segoe UI", 10, "bold"),
                 fg="#bdc3c7", bg="#2c3e50").pack(side=tk.LEFT)
        self.mic_var = tk.StringVar()
        self.mic_combo = ttk.Combobox(
            mic_row, textvariable=self.mic_var,
            values=[d["name"] for d in self._input_devices],
            state="readonly", font=("Segoe UI", 10),
        )
        self.mic_combo.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)
        if self._input_devices:
            self.mic_combo.current(0)
            self._selected_device_index = self._input_devices[0]["index"]
        self.mic_combo.bind("<<ComboboxSelected>>", self._on_mic_selected)

        # ── Bottom widgets packed first so chat can fill remaining space ──

        # Hint bar
        hint = tk.Frame(self.root, bg="#17202a", pady=4)
        hint.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Label(
            hint,
            text="SPACE (hold): Record, release to send   |   SPACE during playback: Interrupt   |   ENTER: Send text",
            font=("Segoe UI", 9), fg="#626567", bg="#17202a"
        ).pack()

        # Text input row
        input_row = tk.Frame(self.root, bg="#2c3e50", pady=6)
        input_row.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=(0, 4))

        self.text_var = tk.StringVar()
        self.text_entry = tk.Entry(
            input_row, textvariable=self.text_var,
            font=("Segoe UI", 11), relief=tk.FLAT, bg="#ecf0f1", fg="#2c3e50",
            insertbackground="#2c3e50",
        )
        self.text_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 6))

        send_btn = tk.Button(
            input_row, text="Send", command=self._send_text,
            font=("Segoe UI", 10, "bold"), bg="#2980b9", fg="white",
            relief=tk.FLAT, padx=14, pady=4, activebackground="#1a6fa3",
        )
        send_btn.pack(side=tk.RIGHT)

        # Status bar + volume meter
        status_bar = tk.Frame(self.root, bg="#1a252f", pady=5)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=12)
        tk.Label(status_bar, text="Status:", font=("Segoe UI", 10),
                 fg="#bdc3c7", bg="#1a252f").pack(side=tk.LEFT)
        self.status_lbl = tk.Label(
            status_bar, text="IDLE",
            font=("Segoe UI", 10, "bold"), fg=STATUS_COLORS["IDLE"], bg="#1a252f"
        )
        self.status_lbl.pack(side=tk.LEFT, padx=(4, 20))
        tk.Label(status_bar, text="Mic level:", font=("Segoe UI", 10),
                 fg="#bdc3c7", bg="#1a252f").pack(side=tk.LEFT)
        self.level_canvas = tk.Canvas(status_bar, width=150, height=14,
                                       bg="#0d1117", highlightthickness=0)
        self.level_canvas.pack(side=tk.LEFT, padx=(4, 0))
        self._level_bar = self.level_canvas.create_rectangle(
            0, 0, 0, 14, fill="#27ae60", outline=""
        )

        # Main content area — chat left, face panel right
        main_area = tk.Frame(self.root, bg="#2c3e50")
        main_area.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 6))

        # Right: face panel (fixed width, does not expand horizontally)
        right_panel = tk.Frame(main_area, bg="#1a252f", width=272)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        right_panel.pack_propagate(False)
        self.face = FacePanel(right_panel, self.root)

        # Left: tabbed notebook — Chat | Console
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook",        background="#2c3e50", borderwidth=0)
        style.configure("TNotebook.Tab",    background="#1a252f", foreground="#bdc3c7",
                        font=("Segoe UI", 10), padding=(12, 4))
        style.map("TNotebook.Tab",
                  background=[("selected", "#2c3e50")],
                  foreground=[("selected", "#ecf0f1")])

        self._notebook = ttk.Notebook(main_area)
        self._notebook.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tab 1 — Conversation
        chat_frame = tk.Frame(self._notebook, bg="#f0f3f4")
        self._notebook.add(chat_frame, text="  Chat  ")
        self.chat = scrolledtext.ScrolledText(
            chat_frame, wrap=tk.WORD, state=tk.DISABLED,
            font=("Segoe UI", 11), bg="#f0f3f4", fg="#2c3e50",
            padx=12, pady=8, relief=tk.FLAT, borderwidth=0,
        )
        self.chat.pack(fill=tk.BOTH, expand=True)
        self.chat.tag_config("you",  foreground="#2471a3", font=("Segoe UI", 11, "bold"))
        self.chat.tag_config("bot",  foreground="#1e8449", font=("Segoe UI", 11, "bold"))
        self.chat.tag_config("text", foreground="#6c3483", font=("Segoe UI", 11, "bold"))

        # Tab 2 — Console log
        console_frame = tk.Frame(self._notebook, bg="#0d1117")
        self._notebook.add(console_frame, text="  Console  ")
        self.console = scrolledtext.ScrolledText(
            console_frame, wrap=tk.WORD, state=tk.DISABLED,
            font=("Consolas", 9), bg="#0d1117", fg="#7ec8e3",
            padx=10, pady=6, relief=tk.FLAT, borderwidth=0,
        )
        self.console.pack(fill=tk.BOTH, expand=True)
        self.console.tag_config("warn", foreground="#f39c12")
        self.console.tag_config("err",  foreground="#e74c3c")

        # Clear the notification dot when user switches to console tab
        def _on_tab_change(event):
            if self._notebook.index("current") == 1:
                self._notebook.tab(1, text="  Console  ")
        self._notebook.bind("<<NotebookTabChanged>>", _on_tab_change)

    # ------------------------------------------------------------------
    # Settings panel
    # ------------------------------------------------------------------

    def _open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.configure(bg="#1a252f")
        win.resizable(False, False)
        win.grab_set()  # modal

        pad = {"padx": 16, "pady": 6}

        tk.Label(win, text="CooperBot Settings", font=("Segoe UI", 13, "bold"),
                 fg="#ecf0f1", bg="#1a252f").grid(row=0, column=0, columnspan=2, pady=(14, 8))

        # --- Voice ---
        tk.Label(win, text="Voice:", font=("Segoe UI", 10, "bold"),
                 fg="#bdc3c7", bg="#1a252f").grid(row=1, column=0, sticky=tk.E, **pad)
        voice_var = tk.StringVar()
        voice_combo = ttk.Combobox(win, textvariable=voice_var, state="readonly",
                                   font=("Segoe UI", 10), width=22)
        voice_combo.grid(row=1, column=1, sticky=tk.W, **pad)

        # --- Speed ---
        tk.Label(win, text="Speed:", font=("Segoe UI", 10, "bold"),
                 fg="#bdc3c7", bg="#1a252f").grid(row=2, column=0, sticky=tk.E, **pad)
        speed_frame = tk.Frame(win, bg="#1a252f")
        speed_frame.grid(row=2, column=1, sticky=tk.W, **pad)
        speed_var = tk.DoubleVar(value=1.2)
        speed_lbl = tk.Label(speed_frame, text="1.20×", font=("Segoe UI", 10),
                              fg="#ecf0f1", bg="#1a252f", width=5)
        speed_lbl.pack(side=tk.RIGHT, padx=(6, 0))

        def _on_speed(val):
            speed_lbl.config(text=f"{float(val):.2f}×")

        tk.Scale(
            speed_frame, variable=speed_var, from_=0.5, to=2.0, resolution=0.05,
            orient=tk.HORIZONTAL, length=180, command=_on_speed,
            bg="#1a252f", fg="#ecf0f1", troughcolor="#2c3e50",
            highlightthickness=0, showvalue=False,
        ).pack(side=tk.LEFT)

        # --- Status label ---
        status_var = tk.StringVar(value="Fetching current settings…")
        tk.Label(win, textvariable=status_var, font=("Segoe UI", 9, "italic"),
                 fg="#7f8c8d", bg="#1a252f").grid(row=3, column=0, columnspan=2, pady=(0, 4))

        # --- Buttons ---
        btn_frame = tk.Frame(win, bg="#1a252f")
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(4, 14))

        def _apply():
            def _do():
                try:
                    requests.post(
                        f"{SERVER_URL}/settings",
                        data={"voice": voice_var.get(), "speed": str(round(speed_var.get(), 2))},
                        timeout=5,
                    )
                    status_var.set(f"Applied: {voice_var.get()} @ {speed_var.get():.2f}×")
                    self._log("System", f"Settings updated — voice: {voice_var.get()}, speed: {speed_var.get():.2f}×")
                except Exception as e:
                    status_var.set(f"Error: {e}")
            threading.Thread(target=_do, daemon=True).start()

        tk.Button(btn_frame, text="Apply", command=_apply,
                  font=("Segoe UI", 10, "bold"), bg="#2980b9", fg="white",
                  relief=tk.FLAT, padx=18, pady=4, activebackground="#1a6fa3"
                  ).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_frame, text="Close", command=win.destroy,
                  font=("Segoe UI", 10), bg="#2c3e50", fg="#bdc3c7",
                  relief=tk.FLAT, padx=18, pady=4, activebackground="#3d5166"
                  ).pack(side=tk.LEFT, padx=6)

        # Fetch current settings from server
        def _fetch():
            try:
                r = requests.get(f"{SERVER_URL}/settings/voices", timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    voices = data.get("voices", [])
                    cur_voice = data.get("current_voice", "")
                    cur_speed = data.get("current_speed", 1.2)
                    voice_combo["values"] = voices
                    voice_var.set(cur_voice if cur_voice in voices else (voices[0] if voices else ""))
                    speed_var.set(cur_speed)
                    speed_lbl.config(text=f"{cur_speed:.2f}×")
                    status_var.set("Ready.")
                else:
                    status_var.set("Could not load settings from server.")
            except Exception as e:
                status_var.set(f"Server unreachable: {e}")
        threading.Thread(target=_fetch, daemon=True).start()

    # ------------------------------------------------------------------
    # Microphone helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_input_devices() -> list[dict]:
        # Words that identify virtual/loopback devices to skip
        skip = {"mapper", "primary", "stereo mix", "wave mapper", "mixagem"}
        seen_names: set[str] = set()
        devices = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] <= 0:
                continue
            name: str = d["name"]
            name_lower = name.lower()
            if any(s in name_lower for s in skip):
                continue
            # Deduplicate — Windows lists the same mic once per audio API
            base = name_lower.split("(")[0].strip()
            if base in seen_names:
                continue
            seen_names.add(base)
            devices.append({"index": i, "name": name})
        return devices

    def _on_mic_selected(self, event=None):
        name = self.mic_var.get()
        for d in self._input_devices:
            if d["name"] == name:
                self._selected_device_index = d["index"]
                self._log("System", f"Microphone set to: {name}")
                break

    # ------------------------------------------------------------------
    # Key bindings
    # ------------------------------------------------------------------

    def _bind_keys(self):
        self.root.bind("<KeyPress-space>",   self._on_space_press)
        self.root.bind("<KeyRelease-space>", self._on_space_release)
        self.root.bind("<Return>", self._on_enter)
        self.root.bind("<Escape>", lambda e: self.text_var.set(""))

    def _on_space_press(self, event):
        if self.root.focus_get() is self.text_entry:
            return  # let space work normally in the text box
        if self.state == "IDLE":
            self._start_recording()
        elif self.state == "PLAYING":
            self._interrupt_playback()

    def _on_space_release(self, event):
        if self.root.focus_get() is self.text_entry:
            return
        if self.state == "RECORDING":
            self._stop_recording_and_send()

    def _on_enter(self, event):
        self._send_text()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _set_state(self, state: str):
        self.state = state
        self.status_lbl.config(text=state, fg=STATUS_COLORS.get(state, "#ecf0f1"))
        if self.face:
            self.face.set_state(state)

    def _log(self, speaker: str, text: str):
        if speaker == "System":
            # Route to console tab
            self.console.config(state=tk.NORMAL)
            tag = "err" if text.lower().startswith("error") else ""
            self.console.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {text}\n", tag)
            self.console.config(state=tk.DISABLED)
            self.console.see(tk.END)
            # Flash tab label if console isn't currently visible
            if self._notebook.index("current") != 1:
                self._notebook.tab(1, text="  Console ●  ")
        else:
            # Route to chat tab — clear the dot if we write chat
            self.chat.config(state=tk.NORMAL)
            if self.chat.index(tk.END).strip() != "1.0":
                self.chat.insert(tk.END, "\n")
            tag = {"You": "you", "CooperBot": "bot", "You (text)": "text"}.get(speaker, "")
            self.chat.insert(tk.END, f"{speaker}: ", tag)
            self.chat.insert(tk.END, text)
            self.chat.config(state=tk.DISABLED)
            self.chat.see(tk.END)


    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _start_recording(self):
        self._set_state("RECORDING")
        _beep(880, 0.07)   # high pip — recording starting
        self._rec_start = time.time()
        self._rec_buffer = sd.rec(
            int(SAMPLE_RATE * 30),   # max 30 seconds
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            device=self._selected_device_index,
        )
        self._recording = True
        device_name = sd.query_devices(self._selected_device_index)["name"]
        self._log("System", f"Recording via: {device_name}")

        # Level meter — samples the live buffer periodically
        def meter():
            import time
            while self._recording:
                pos = int((time.time() - self._rec_start) * SAMPLE_RATE)
                if pos > 512:
                    chunk = self._rec_buffer[max(0, pos - 512):pos]
                    level = int(np.abs(chunk).mean() / 32768 * 150)
                    self._ui_queue.put(("level", level))
                time.sleep(0.05)

        threading.Thread(target=meter, daemon=True).start()

    def _stop_recording_and_send(self):
        elapsed = time.time() - self._rec_start
        self._recording = False
        sd.stop()
        _beep(520, 0.07)   # lower pip — recording sent

        frames = min(int(elapsed * SAMPLE_RATE), int(SAMPLE_RATE * 30))
        audio = self._rec_buffer[:frames].copy()
        self._set_state("PROCESSING")

        def process():
            if frames < 100:
                self._ui_queue.put(("error", "No audio captured."))
                return

            max_amp = int(np.abs(audio).max())
            self._ui_queue.put(("chat", ("System", f"Captured {frames} frames, peak amplitude: {max_amp}")))

            if max_amp < 50:
                self._ui_queue.put(("error", "Audio is silent — mic may not be capturing. Try a different device."))
                self._ui_queue.put(("state", "IDLE"))
                return

            fd, wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                with wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(SAMPLE_RATE)
                    wf.writeframes(audio.tobytes())

                with open(wav_path, "rb") as f:
                    resp = requests.post(
                        f"{SERVER_URL}/chat_stream",
                        files={"audio": ("audio.wav", f, "audio/wav")},
                        data={"session_id": SESSION_ID},
                        timeout=60,
                        stream=True,
                    )

                if resp.status_code == 200:
                    transcription = resp.headers.get("X-Transcription", "")
                    self._ui_queue.put(("chat", ("You", transcription or "(no transcription)")))
                    self._start_image_poller()
                    self._play_stream(resp.iter_content(chunk_size=4096))
                    self._after_play()
                else:
                    self._ui_queue.put(("error", f"Server error {resp.status_code}"))

            except requests.RequestException as e:
                self._ui_queue.put(("error", f"Connection error: {e}"))
            finally:
                os.unlink(wav_path)

        threading.Thread(target=process, daemon=True).start()

    # ------------------------------------------------------------------
    # Text injection
    # ------------------------------------------------------------------

    def _send_text(self):
        text = self.text_var.get().strip()
        if not text or self.state not in ("IDLE", "PLAYING"):
            return
        if self.state == "PLAYING":
            self._interrupt_playback()
        self.text_var.set("")
        self._set_state("PROCESSING")
        self._log("You (text)", text)

        def process():
            try:
                resp = requests.post(
                    f"{SERVER_URL}/chat_text_stream",
                    data={"text": text, "session_id": SESSION_ID},
                    timeout=60,
                    stream=True,
                )
                if resp.status_code == 200:
                    self._start_image_poller()
                    self._play_stream(resp.iter_content(chunk_size=4096))
                    self._after_play()
                else:
                    self._ui_queue.put(("error", f"Server error {resp.status_code}"))
            except requests.RequestException as e:
                self._ui_queue.put(("error", f"Connection error: {e}"))

        threading.Thread(target=process, daemon=True).start()

    # ------------------------------------------------------------------
    # Image display
    # ------------------------------------------------------------------

    def _show_image(self, url: str):
        """Download image from URL and insert it into the chat area."""
        try:
            resp = requests.get(
                url, timeout=8,
                headers={"User-Agent": "CooperBot/1.0 (educational child chatbot)"},
            )
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content))
            # Fit within chat width
            max_w = 420
            if img.width > max_w:
                ratio = max_w / img.width
                img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._images.append(photo)  # prevent garbage collection
            self.chat.config(state=tk.NORMAL)
            self.chat.insert(tk.END, "\n")
            self.chat.image_create(tk.END, image=photo)
            self.chat.insert(tk.END, "\n")
            self.chat.config(state=tk.DISABLED)
            self.chat.see(tk.END)
        except Exception as exc:
            self._log("System", f"Image unavailable: {exc}")

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _play_stream(self, chunk_iter):
        """Pipe streaming MP3 chunks through ffmpeg and play via sounddevice OutputStream."""
        self._ui_queue.put(("state", "PLAYING"))
        proc = subprocess.Popen(
            ["ffmpeg", "-i", "pipe:0", "-f", "s16le",
             "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._current_proc = proc

        def _feed():
            try:
                for chunk in chunk_iter:
                    if proc.stdin.closed:
                        break
                    proc.stdin.write(chunk)
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        threading.Thread(target=_feed, daemon=True).start()

        with sd.OutputStream(samplerate=22050, channels=1, dtype="int16") as out_stream:
            while True:
                pcm = proc.stdout.read(4096)
                if not pcm:
                    break
                out_stream.write(np.frombuffer(pcm, dtype=np.int16))

        proc.wait()
        self._current_proc = None
        self._ui_queue.put(("state", "IDLE"))

    def _interrupt_playback(self):
        if self._current_proc:
            self._current_proc.kill()
            self._current_proc = None
        sd.stop()
        self._set_state("IDLE")
        self._log("System", "Interrupted.")

    # ------------------------------------------------------------------
    # Post-play face + image update
    # ------------------------------------------------------------------

    def _start_image_poller(self):
        """
        Start a background thread that polls for an image URL immediately
        after the server response arrives — shows it as soon as it's ready,
        even while audio is still playing.
        """
        def _poll():
            for _ in range(40):  # poll every 500 ms for up to 20 s
                time.sleep(0.5)
                try:
                    r = requests.get(
                        f"{SERVER_URL}/session/{SESSION_ID}/latest_image",
                        timeout=3,
                    )
                    if r.status_code == 200:
                        url = r.json().get("image_url", "")
                        if url:
                            self._ui_queue.put(("face_image_url", url))
                            self._ui_queue.put(("image", url))
                            return
                except Exception:
                    pass
        threading.Thread(target=_poll, daemon=True).start()

    def _after_play(self):
        """After playback: fetch and display bot reply text, show HAPPY then IDLE."""
        try:
            r = requests.get(
                f"{SERVER_URL}/session/{SESSION_ID}/latest_reply", timeout=5,
            )
            if r.status_code == 200:
                reply = r.json().get("reply", "")
                if reply:
                    self._ui_queue.put(("chat", ("CooperBot", reply)))
        except Exception:
            pass
        self._ui_queue.put(("face_state", "HAPPY"))
        time.sleep(1.2)
        self._ui_queue.put(("face_state", "IDLE"))

    # ------------------------------------------------------------------
    # UI queue (thread -> main thread bridge)
    # ------------------------------------------------------------------

    def _poll_queue(self):
        try:
            while True:
                item = self._ui_queue.get_nowait()
                kind = item[0]
                if kind == "image":
                    threading.Thread(target=self._show_image, args=(item[1],), daemon=True).start()
                elif kind == "chat":
                    _, (speaker, text) = item
                    self._log(speaker, text)
                elif kind == "state":
                    self._set_state(item[1])
                    if item[1] == "IDLE":
                        self.level_canvas.coords(self._level_bar, 0, 0, 0, 14)
                elif kind == "level":
                    self.level_canvas.coords(self._level_bar, 0, 0, item[1], 14)
                elif kind == "face_state":
                    if self.face:
                        self.face.set_face_state(item[1])
                elif kind == "face_image_url":
                    if self.face:
                        self.face.show_image_url(item[1])
                elif kind == "error":
                    self._log("System", f"Error: {item[1]}")
                    self._set_state("IDLE")
                    self.level_canvas.coords(self._level_bar, 0, 0, 0, 14)
        except queue.Empty:
            pass
        self.root.after(40, self._poll_queue)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    root = tk.Tk()
    CooperBotGUI(root)
    root.mainloop()
