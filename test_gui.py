"""
CooperBot Test GUI
------------------
SPACE      : Start recording / stop recording and send / interrupt playback
ENTER      : Send typed text directly (bypasses speech recognition)
ESC        : Clear the text input
"""

import os
import queue
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk
import wave

import numpy as np
import requests
import sounddevice as sd

SERVER_URL   = "http://localhost:8765"
SESSION_ID   = "gui-session"
SAMPLE_RATE  = 16000

STATUS_COLORS = {
    "IDLE":       "#27ae60",
    "RECORDING":  "#e74c3c",
    "PROCESSING": "#f39c12",
    "PLAYING":    "#2980b9",
}


class CooperBotGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CooperBot - Test Console")
        self.root.geometry("680x540")
        self.root.configure(bg="#2c3e50")
        self.root.resizable(True, True)

        self.state = "IDLE"
        self._audio_frames: list[np.ndarray] = []
        self._ui_queue: queue.Queue = queue.Queue()
        self._input_devices: list[dict] = self._get_input_devices()
        self._selected_device_index: int | None = None
        self._stream: sd.InputStream | None = None

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

        # Microphone selector — top of window, always visible
        mic_row = tk.Frame(self.root, bg="#2c3e50", pady=5)
        mic_row.pack(fill=tk.X, padx=12)
        tk.Label(mic_row, text="Mic:", font=("Segoe UI", 10, "bold"),
                 fg="#bdc3c7", bg="#2c3e50").pack(side=tk.LEFT)
        self.mic_var = tk.StringVar()
        self.mic_combo = ttk.Combobox(
            mic_row, textvariable=self.mic_var,
            values=[d["name"] for d in self._input_devices],
            state="readonly", width=50, font=("Segoe UI", 10),
        )
        self.mic_combo.pack(side=tk.LEFT, padx=(6, 0))
        if self._input_devices:
            self.mic_combo.current(0)
            self._selected_device_index = self._input_devices[0]["index"]
        self.mic_combo.bind("<<ComboboxSelected>>", self._on_mic_selected)

        # Chat area
        self.chat = scrolledtext.ScrolledText(
            self.root, wrap=tk.WORD, state=tk.DISABLED,
            font=("Segoe UI", 11), bg="#f0f3f4", fg="#2c3e50",
            padx=12, pady=8, relief=tk.FLAT, borderwidth=0,
        )
        self.chat.pack(fill=tk.BOTH, expand=True, padx=12, pady=(10, 6))
        self.chat.tag_config("you",    foreground="#2471a3", font=("Segoe UI", 11, "bold"))
        self.chat.tag_config("bot",    foreground="#1e8449", font=("Segoe UI", 11, "bold"))
        self.chat.tag_config("system", foreground="#888",    font=("Segoe UI", 10, "italic"))
        self.chat.tag_config("text",   foreground="#6c3483", font=("Segoe UI", 11, "bold"))

        # Status bar + volume meter
        status_bar = tk.Frame(self.root, bg="#1a252f", pady=5)
        status_bar.pack(fill=tk.X, padx=12)
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

        # Text input row
        input_row = tk.Frame(self.root, bg="#2c3e50", pady=6)
        input_row.pack(fill=tk.X, padx=12, pady=(0, 4))

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

        # Hint bar
        hint = tk.Frame(self.root, bg="#17202a", pady=4)
        hint.pack(fill=tk.X)
        tk.Label(
            hint,
            text="SPACE: Start / Stop recording   |   SPACE during playback: Interrupt   |   ENTER: Send text",
            font=("Segoe UI", 9), fg="#626567", bg="#17202a"
        ).pack()

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
        self.root.bind("<space>",  self._on_space)
        self.root.bind("<Return>", self._on_enter)
        self.root.bind("<Escape>", lambda e: self.text_var.set(""))

    def _on_space(self, event):
        if self.root.focus_get() is self.text_entry:
            return  # let space work normally in the text box
        if self.state == "IDLE":
            self._start_recording()
        elif self.state == "RECORDING":
            self._stop_recording_and_send()
        elif self.state == "PLAYING":
            self._interrupt_playback()

    def _on_enter(self, event):
        self._send_text()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _set_state(self, state: str):
        self.state = state
        self.status_lbl.config(text=state, fg=STATUS_COLORS.get(state, "#ecf0f1"))

    def _log(self, speaker: str, text: str):
        self.chat.config(state=tk.NORMAL)
        if self.chat.index(tk.END).strip() != "1.0":
            self.chat.insert(tk.END, "\n")
        tag = {"You": "you", "CooperBot": "bot", "You (text)": "text"}.get(speaker, "system")
        self.chat.insert(tk.END, f"{speaker}: ", tag)
        self.chat.insert(tk.END, text)
        self.chat.config(state=tk.DISABLED)
        self.chat.see(tk.END)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _start_recording(self):
        self._set_state("RECORDING")
        self._audio_frames = []

        device_name = sd.query_devices(self._selected_device_index)["name"]
        self._log("System", f"Recording via: {device_name}")

        def _audio_callback(indata, frames, time_info, status):
            self._audio_frames.append(indata.copy())
            level = int(np.abs(indata).mean() / 32768 * 150)
            self._ui_queue.put(("level", level))

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=1024,
            device=self._selected_device_index,
            callback=_audio_callback,
        )
        self._stream.start()

    def _stop_recording_and_send(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._set_state("PROCESSING")

        def process():
            if not self._audio_frames:
                self._ui_queue.put(("error", "No audio captured."))
                return

            audio = np.concatenate(self._audio_frames, axis=0)
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
                        f"{SERVER_URL}/chat",
                        files={"audio": ("audio.wav", f, "audio/wav")},
                        data={"session_id": SESSION_ID},
                        timeout=60,
                    )

                if resp.status_code == 200:
                    transcription = resp.headers.get("X-Transcription", "")
                    reply         = resp.headers.get("X-Reply", "")
                    self._ui_queue.put(("chat", ("You", transcription or "(no transcription)")))
                    self._ui_queue.put(("chat", ("CooperBot", reply)))
                    self._ui_queue.put(("play", resp.content))
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
                    f"{SERVER_URL}/chat_text",
                    data={"text": text, "session_id": SESSION_ID},
                    timeout=60,
                )
                if resp.status_code == 200:
                    reply = resp.headers.get("X-Reply", "")
                    self._ui_queue.put(("chat", ("CooperBot", reply)))
                    self._ui_queue.put(("play", resp.content))
                else:
                    self._ui_queue.put(("error", f"Server error {resp.status_code}"))
            except requests.RequestException as e:
                self._ui_queue.put(("error", f"Connection error: {e}"))

        threading.Thread(target=process, daemon=True).start()

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _play_audio(self, mp3_data: bytes):
        self._set_state("PLAYING")
        proc = subprocess.run(
            ["ffmpeg", "-i", "pipe:0", "-f", "s16le",
             "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1", "pipe:1"],
            input=mp3_data,
            capture_output=True,
        )
        samples = np.frombuffer(proc.stdout, dtype=np.int16)
        sd.play(samples, samplerate=22050)
        sd.wait()
        self._ui_queue.put(("state", "IDLE"))

    def _interrupt_playback(self):
        sd.stop()
        self._set_state("IDLE")
        self._log("System", "Interrupted.")

    # ------------------------------------------------------------------
    # UI queue (thread -> main thread bridge)
    # ------------------------------------------------------------------

    def _poll_queue(self):
        try:
            while True:
                item = self._ui_queue.get_nowait()
                kind = item[0]
                if kind == "play":
                    threading.Thread(target=self._play_audio, args=(item[1],), daemon=True).start()
                elif kind == "chat":
                    _, (speaker, text) = item
                    self._log(speaker, text)
                elif kind == "state":
                    self._set_state(item[1])
                    if item[1] == "IDLE":
                        self.level_canvas.coords(self._level_bar, 0, 0, 0, 14)
                elif kind == "level":
                    self.level_canvas.coords(self._level_bar, 0, 0, item[1], 14)
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
