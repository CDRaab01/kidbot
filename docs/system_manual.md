# KidBot — System Manual

**Version:** 0.5  
**Audience:** System operators, parents, and anyone setting up or maintaining a KidBot deployment

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Hardware Components](#2-hardware-components)
3. [System Architecture](#3-system-architecture)
4. [Network Architecture](#4-network-architecture)
5. [Server Setup](#5-server-setup)
6. [Raspberry Pi Setup](#6-raspberry-pi-setup)
7. [Model Setup](#7-model-setup)
8. [Wiring & GPIO](#8-wiring--gpio)
9. [Security](#9-security)
10. [Starting & Stopping](#10-starting--stopping)
11. [Monitoring & Logs](#11-monitoring--logs)
12. [Troubleshooting](#12-troubleshooting)
13. [Updating](#13-updating)

---

## 1. System Overview

KidBot is a voice-activated AI companion for children, designed to be friendly, safe, and engaging. A child holds a button to speak, releases it to send, and hears a response through a speaker — with an animated robot face reacting on a small LCD screen.

```
┌────────────────────────────────────────────────────────────────┐
│                         KidBot System                          │
│                                                                │
│    ┌──────────────────────────┐       ┌────────────────────┐   │
│    │   Raspberry Pi Zero WH   │       │    Server (PC)     │   │
│    │                          │  WiFi │                    │   │
│    │  [Button] ─► [LED]       │◄─────►│  Whisper STT       │   │
│    │  [ReSpeaker Mic HAT]     │  LAN  │  Gemma 3 4B LLM   │   │
│    │  [Waveshare 2.4" LCD]    │       │  Kokoro TTS        │   │
│    │  [Speaker / 3.5mm]       │       │  FastAPI :8765     │   │
│    └──────────────────────────┘       └────────────────────┘   │
│                                                                │
│    The Pi handles all hardware.                                │
│    The server handles all AI inference.                        │
└────────────────────────────────────────────────────────────────┘
```

**Design principles:**
- **Child-safe:** All AI output passes through a two-stage content filter.
- **Low-latency:** Sentence-level streaming means audio starts playing ~1–3 s after the child finishes speaking.
- **Offline-resilient:** Cached error/offline audio clips play if the server is unreachable.
- **Private:** Everything runs on your own LAN — no cloud services, no data leaves the home.

---

## 2. Hardware Components

### Required

| Component | Model | Notes |
|---|---|---|
| Single-board computer | Raspberry Pi Zero WH | WiFi built-in |
| Microphone | ReSpeaker 2-Mic Pi HAT | Mounts on 40-pin header |
| Display | Waveshare 2.4" Touch LCD (B) | ILI9341, 320×240, SPI |
| Push button | Momentary tactile switch | Normally open, connects to GND |
| LED | 5 mm LED + 220 Ω resistor | Status indicator |
| Speaker | Any 3.5 mm passive speaker | Via ReSpeaker 3.5 mm jack |
| Power | 5 V / 2.5 A USB micro | Pi Zero WH micro-USB power port |
| Server PC | Any x86-64 machine | Ubuntu / Windows / macOS |

### Server Minimum Specs

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 4-core x86-64 | 6+ cores |
| RAM | 8 GB | 16 GB |
| Disk | 10 GB free | 20 GB free |
| OS | Ubuntu 22.04+ / Windows 10+ | Ubuntu 22.04 LTS |

> Ollama can use an NVIDIA GPU for faster inference. Set `WHISPER_DEVICE=cuda` and `WHISPER_COMPUTE_TYPE=float16` if a GPU is available.

---

## 3. System Architecture

### Component Interaction

```
╔══════════════════════════════════════════════════════════════════════╗
║                    KidBot — Full System View                         ║
╠══════════════════════╦═══════════════════════════════════════════════╣
║  RASPBERRY PI        ║  SERVER PC                                    ║
║                      ║                                               ║
║  Physical world:     ║  ┌──────────────────────────────────────────┐ ║
║                      ║  │            Ollama daemon                 │ ║
║  ┌──────────────┐    ║  │  gemma-3-4b-it-Q4_K_M.gguf             │ ║
║  │ Push button  │    ║  │  listening on localhost:11434            │ ║
║  │ (GPIO 17)    │    ║  └─────────────┬────────────────────────────┘ ║
║  └──────┬───────┘    ║                │                              ║
║         │ press      ║  ┌─────────────▼────────────────────────────┐ ║
║  ┌──────▼───────┐    ║  │       FastAPI server :8765               │ ║
║  │ AudioManager │    ║  │                                          │ ║
║  │ ReSpeaker HAT│    ║  │  ┌────────┐ ┌─────────┐ ┌────────────┐  │ ║
║  │ 16 kHz mono  │    ║  │  │Whisper │ │  Ollama │ │  Kokoro    │  │ ║
║  └──────┬───────┘    ║  │  │ small  │ │ (local) │ │  bm_lewis  │  │ ║
║         │ WAV        ║  │  └────────┘ └─────────┘ └────────────┘  │ ║
║  ┌──────▼───────┐    ║  │                                          │ ║
║  │ServerClient  │────╬──►  Guardrails ◄──────────────────────────  │ ║
║  │ HTTP/stream  │◄───╬──── input/output safety filter              │ ║
║  └──────┬───────┘    ║  │                                          │ ║
║         │ MP3 stream ║  │  SessionStore (in-memory + SQLite)       │ ║
║  ┌──────▼───────┐    ║  └──────────────────────────────────────────┘ ║
║  │ mpg123       │    ║                                               ║
║  │ audio output │    ║  Wikipedia API (image search)                 ║
║  └──────────────┘    ║  └── GET /w/api.php?pageimages=...           ║
║                      ║                                               ║
║  ┌──────────────┐    ║                                               ║
║  │DisplayManager│    ║                                               ║
║  │ ILI9341 LCD  │    ║                                               ║
║  │ PIL + luma   │    ║                                               ║
║  └──────────────┘    ║                                               ║
║                      ║                                               ║
║  ┌──────────────┐    ║                                               ║
║  │ LED (GPIO 27)│    ║                                               ║
║  └──────────────┘    ║                                               ║
╚══════════════════════╩═══════════════════════════════════════════════╝
```

### Conversation State Machine

```
                       ┌──────────────────────┐
                       │         IDLE         │
                       │  face: slow blink    │
                       │  LED: off            │
                       └──────────┬───────────┘
                                  │  button pressed
                                  ▼
                       ┌──────────────────────┐
                       │      LISTENING       │
                       │  face: wide eyes     │
                       │  LED: on             │
                       │  recording audio...  │
                       └──────────┬───────────┘
                                  │  button released
                                  ▼
                       ┌──────────────────────┐
                       │      THINKING        │
                       │  face: narrow eyes   │
                       │  LED: off            │
                       │  sending to server.. │
                       └──────────┬───────────┘
                                  │  stream begins
                                  ▼
                       ┌──────────────────────┐
                       │      SPEAKING        │
                       │  face: mouth moves   │
                       │  LED: on             │
                       │  playing audio...    │
                       └──────────┬───────────┘
                                  │  audio done
                         ┌────────┴────────┐
                         │                 │
                         ▼                 ▼
              ┌─────────────────┐  ┌──────────────────────┐
              │     HAPPY       │  │       IMAGE          │
              │ face: ^ ^ smile │  │ Wikipedia photo      │
              │ (1.5 s)         │  │ fills screen (8 s)   │
              └────────┬────────┘  └──────────┬───────────┘
                       │                      │  timeout
                       └──────────┬───────────┘
                                  ▼
                               IDLE

              [any network failure]
                       ▼
              ┌──────────────────┐
              │      ERROR       │
              │  face: × eyes    │
              │  plays fallback  │
              │  clip (2 s)      │
              └────────┬─────────┘
                       ▼
                     IDLE
```

---

## 4. Network Architecture

```
Home LAN  (192.168.1.0/24 example)
─────────────────────────────────────────────────────────────────
  ┌──────────────┐          ┌──────────────┐
  │  Server PC   │          │  WiFi Router │
  │ 192.168.1.100│◄─────────┤              │
  │  port 8765   │          └──────┬───────┘
  └──────────────┘                 │ 802.11n/ac
                                   │
                          ┌────────▼──────────┐
                          │  Pi Zero WH       │
                          │  192.168.1.xxx    │
                          └───────────────────┘

Internet (optional — Wikipedia image search only)
  Server PC ──► wikipedia.org/w/api.php  (HTTPS, no auth required)
─────────────────────────────────────────────────────────────────
```

**Ports used:**

| Port | Direction | Purpose |
|---|---|---|
| 8765/TCP | Pi → Server | KidBot API (HTTP) |
| 11434/TCP | localhost only | Ollama LLM daemon |
| 443/TCP | Server → Internet | Wikipedia image search |

**Firewall:** Open port `8765` on the server machine's firewall for LAN access. Do **not** expose this port to the internet.

---

## 5. Server Setup

### 5.1 Install System Dependencies

```bash
# Ubuntu / Debian
sudo apt update
sudo apt install -y python3.11 python3-pip ffmpeg git

# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh
```

### 5.2 Clone and Install

```bash
git clone https://github.com/CDRaab01/kidbot.git
cd kidbot
pip install -r requirements/server_requirements.txt
```

### 5.3 Set Up Models

```bash
# 1. Download the Gemma 3 4B quantized model
#    Place at: server/models/gemma-3-4b-it-Q4_K_M.gguf

# 2. Import into Ollama
ollama create kidbot -f Modelfile

# 3. Verify
ollama list        # should show "kidbot"
ollama run kidbot "Hello"   # quick sanity check

# 4. Download Kokoro ONNX model and voices
#    Place at: server/models/kokoro-v1.0.onnx
#              server/models/voices-v1.0.bin
```

### 5.4 Configure

```bash
# Optional — create a .env file or export in your shell:
export KIDBOT_API_KEY="your-secret-key"
export PERSIST_SESSIONS=1
export LOG_FILE=/var/log/kidbot/server.log
export CHILD_NAME=Cooper
```

### 5.5 Start the Server

```bash
# Development (auto-reload)
python -m uvicorn server.main:app --host 0.0.0.0 --port 8765 --reload

# Production
python -m server.main
# or:
uvicorn server.main:app --host 0.0.0.0 --port 8765 --workers 1
```

> Use `--workers 1` — the ML models are not fork-safe.

### 5.6 Run as a systemd Service (Linux)

```ini
# /etc/systemd/system/kidbot.service
[Unit]
Description=KidBot AI Server
After=network.target ollama.service

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/kidbot
Environment=KIDBOT_API_KEY=your-secret-key
Environment=PERSIST_SESSIONS=1
ExecStart=/usr/bin/python3 -m server.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kidbot
sudo systemctl status kidbot
```

---

## 6. Raspberry Pi Setup

### 6.1 Operating System

Flash **Raspberry Pi OS Lite (32-bit)** to a microSD card using Raspberry Pi Imager. The Pi Zero WH uses an ARMv6 (BCM2835) processor and cannot boot a 64-bit OS — you must select the 32-bit image. During imaging, configure:
- Hostname: `kidbot`
- SSH: enabled
- WiFi SSID and password
- Username / password

### 6.2 Enable SPI and I2S

```bash
sudo raspi-config
# → Interface Options → SPI → Enable
# → Interface Options → I2C → Enable (for ReSpeaker)
sudo reboot
```

### 6.3 Install ReSpeaker Drivers

```bash
sudo apt install -y git python3-pip portaudio19-dev
git clone https://github.com/HinTak/seeed-voicecard.git
cd seeed-voicecard
sudo ./install.sh
sudo reboot
```

### 6.4 Install System Dependencies

```bash
sudo apt install -y mpg123 python3-pip python3-venv
```

### 6.5 Install Pi Client

```bash
cd /home/pi
git clone https://github.com/CDRaab01/kidbot.git
cd kidbot
pip3 install -r requirements/pi_requirements.txt
```

### 6.6 Configure

```bash
export KIDBOT_SERVER="http://192.168.1.100:8765"
export KIDBOT_API_KEY="your-secret-key"    # match server
```

### 6.7 Run as a systemd Service

```ini
# /etc/systemd/system/kidbot-pi.service
[Unit]
Description=KidBot Pi Client
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/kidbot
Environment=KIDBOT_SERVER=http://192.168.1.100:8765
Environment=KIDBOT_API_KEY=your-secret-key
ExecStart=/usr/bin/python3 -m pi_client.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kidbot-pi
```

---

## 7. Model Setup

### 7.1 LLM — Gemma 3 4B (Ollama)

```
Modelfile:
  FROM ./server/models/gemma-3-4b-it-Q4_K_M.gguf

Create:
  ollama create kidbot -f Modelfile

Verify:
  ollama list
  ollama ps         ← shows loaded model when server is running
```

The model is addressed as `kidbot` throughout the codebase. To swap models, update `Modelfile` and re-run `ollama create`, or set `OLLAMA_MODEL=<new-name>`.

**VRAM / RAM requirements:**

| Quantisation | RAM needed | Speed (CPU) |
|---|---|---|
| Q4_K_M (default) | ~4 GB | ~1–3 tok/s on modern CPU |
| Q8_0 | ~8 GB | ~0.5–1 tok/s |
| fp16 (GPU) | ~8 GB VRAM | ~20+ tok/s |

### 7.2 STT — Faster-Whisper

Downloaded automatically by `faster-whisper` on first run. Model files are cached at `~/.cache/huggingface/`.

| Model | WER | Speed | RAM |
|---|---|---|---|
| `tiny` | High | Very fast | 200 MB |
| `base` | Medium | Fast | 300 MB |
| `small` (default) | Low | Moderate | 500 MB |
| `medium` | Very low | Slow | 1.5 GB |

### 7.3 TTS — Kokoro ONNX

Kokoro model files must be placed manually:

```
server/models/
├── kokoro-v1.0.onnx      (~300 MB)
└── voices-v1.0.bin       (~50 MB)
```

Available voices (sample): `af_bella`, `af_sarah`, `am_adam`, `am_michael`, `bm_lewis` (default), `bf_emma`, `bf_isabella`.

Change voice at runtime without restarting:
```bash
curl -X POST http://localhost:8765/settings \
     -H "X-API-Key: your-key" \
     -d "voice=bm_lewis&speed=1.2"
```

---

## 8. Wiring & GPIO

### GPIO Pin Map (BCM numbering)

```
Raspberry Pi Zero WH — 40-pin header
─────────────────────────────────────────────────────────────────

  3V3  [1]  [2]  5V
  SDA  [3]  [4]  5V
  SCL  [5]  [6]  GND
       [7]  [8]  TXD
  GND  [9]  [10] RXD
  GP17 [11] [12] GP18   ← BUTTON_PIN (11 = GPIO 17)
  GP27 [13] [14] GND    ← LED_PIN    (13 = GPIO 27)
  GP22 [15] [16] GP23
  3V3  [17] [18] GP24   ← DISPLAY_BL (18 = GPIO 24)  ★
  MOSI [19] [20] GND    ← SPI0 MOSI
  MISO [21] [22] GP25   ← DISPLAY_DC (22 = GPIO 25)  ★
  SCLK [23] [24] CE0    ← SPI0 SCLK / CE0
  GND  [25] [26] CE1
  ID_SD[27] [28] ID_SC
  GP5  [29] [30] GND    ← VOL_UP_PIN   (29 = GPIO 5)  ▲
  GP6  [31] [32] GP12   ← VOL_DOWN_PIN (31 = GPIO 6)  ▼
  GP13 [33] [34] GND
  GP19 [35] [36] GP16
  GP26 [37] [38] GP20
  GND  [39] [40] GP21

★ Display SPI (Waveshare ILI9341):
    CS  → CE0  (pin 24)
    DC  → GP25 (pin 22)
    RST → (unconnected by default — see note below)
    BL  → GP24 (pin 18)
    CLK → SCLK (pin 23)
    DIN → MOSI (pin 19)
    GND → GND  (pin 25)
    VCC → 3V3  (pin 17)

▲▼ Volume rocker — each side connects its GPIO pin to GND when pressed.
   Internal pull-ups are enabled in software (active-low).

⚠ RST conflict: DISPLAY_RST defaults to None (no hardware reset).
  If you want hardware reset, use a GPIO pin that is NOT 27 (LED).
  Set DISPLAY_RST=<pin> in environment.
─────────────────────────────────────────────────────────────────
```

### Button Wiring

```
GPIO 17 (pin 11) ──┤ Button ├── GND (pin 9 or 25)
```

Internal pull-up is enabled in software. Button is active-low (pressing connects GPIO 17 to GND).

### LED Wiring

```
GPIO 27 (pin 13) ──[ 220 Ω ]──┤ LED anode ├──┤ LED cathode ├── GND
```

### Volume Rocker Wiring

```
GPIO 5 (pin 29) ──┤ Vol ▲ side ├── GND (pin 30)
GPIO 6 (pin 31) ──┤ Vol ▼ side ├── GND (pin 30 or 34)
```

A standard 3-pin rocker / SPST momentary switch works. Internal pull-ups are enabled; each side fires a FALLING-edge interrupt when pressed. Bouncetime is 150 ms.

When volume changes, a cyan progress bar overlays the bottom of the LCD for 2 seconds, showing the new level.

Override the default GPIO pins or ALSA control via environment variables:
```bash
export VOL_UP_PIN=5        # BCM number of "volume up" button
export VOL_DOWN_PIN=6      # BCM number of "volume down" button
export VOL_STEP=5          # percent change per press (default 5)
export ALSA_CONTROL=Master # amixer control name (default "Master")
```

### ReSpeaker 2-Mic HAT

The HAT plugs directly into the 40-pin header. It provides:
- 2× MEMS microphones (I2S interface)
- 3.5 mm stereo output jack
- RGB LED ring (not used by KidBot)

The HAT occupies most of the header. The push button and LED connect to the remaining exposed pins (17, 27, GND). The volume rocker connects to the free pins 29/31 (GPIO 5/6).

> **The Waveshare display also connects via SPI on the same header.** This is compatible because the HAT routes through the SPI pins and exposes them.

---

## 9. Security

### API Key Authentication

When `KIDBOT_API_KEY` is set, all API endpoints (except `/health`) require the header:

```
X-API-Key: your-secret-key
```

Both the server and Pi client must use the same key. The key is never logged.

To generate a strong key:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Network Exposure

- Run the server **only on your LAN**. Do not port-forward 8765 to the internet.
- The Pi connects only to the server IP — no direct internet access required.
- Wikipedia image search runs from the server, not the Pi.

### Content Safety

All child interactions pass through a two-stage filter:

1. **Input filter** — child's words are checked before reaching the LLM. Blocked terms trigger a gentle redirect ("That's a great question for a grown-up!").
2. **Output filter** — every sentence of the LLM's reply is checked before being spoken. Blocked output is replaced with a neutral fallback.

The system prompt strictly instructs the model to avoid violence, adult content, personal information requests, and age-inappropriate topics.

### Data & Privacy

- No audio is stored permanently. WAV files are written to `server/temp/` and deleted immediately after transcription.
- Conversation history is held in memory (or optionally in a local SQLite file). Nothing is sent to any external service except Wikipedia image thumbnails.
- The Whisper STT, Gemma LLM, and Kokoro TTS all run locally — no API keys, no cloud.

---

## 10. Starting & Stopping

### Start Everything

```bash
# 1. Start Ollama (if not already running as a service)
ollama serve &

# 2. Start the KidBot server
cd /path/to/kidbot
python -m server.main

# 3. The Pi client starts automatically on boot via systemd
#    Or manually:
python -m pi_client.main
```

### Stop

```bash
# Server: Ctrl+C or
sudo systemctl stop kidbot

# Pi client:
sudo systemctl stop kidbot-pi
# or: the Pi handles SIGINT / SIGTERM cleanly (GPIO cleanup, display off)
```

### Test Without Pi Hardware (Desktop)

```bash
# Start the server first, then:
cd /path/to/kidbot
python test_gui.py
```

The test GUI connects to `http://localhost:8765` by default. Change `SERVER_URL` at the top of `test_gui.py` to target a remote server.

---

## 11. Monitoring & Logs

### Server Logs

```bash
# Stdout (default)
python -m server.main

# File-based (rotating, 10 MB × 5 files)
export LOG_FILE=/var/log/kidbot/server.log
python -m server.main

# systemd journal
journalctl -u kidbot -f
```

**Log format:**
```
2026-05-18 10:23:14,522 INFO     server.main: [abc-123] STT: 1.24s  heard: 'tell me about dinosaurs'
2026-05-18 10:23:17,891 INFO     server.main: [abc-123] LLM: 3.42s  TTS: 0.81s  total: 5.47s
```

### Health Check

```bash
curl http://localhost:8765/health
# {"status": "ok"}

# With API key:
curl -H "X-API-Key: your-key" http://localhost:8765/health
```

### Pi Client Logs

```bash
# Stdout
python -m pi_client.main

# File
export KIDBOT_LOG_FILE=/home/pi/kidbot.log
python -m pi_client.main

# systemd
journalctl -u kidbot-pi -f
```

### Session Inspection

```bash
# See how many sessions are active (no direct endpoint — check logs)
grep "New session" /var/log/kidbot/server.log | wc -l

# SQLite inspection (if PERSIST_SESSIONS=1)
sqlite3 server/sessions.db "SELECT session_id, last_active FROM sessions;"
```

---

## 12. Troubleshooting

### Server won't start

| Symptom | Likely Cause | Fix |
|---|---|---|
| `RuntimeError: kidbot not found in Ollama` | Model not imported | `ollama create kidbot -f Modelfile` |
| `FileNotFoundError: kokoro-v1.0.onnx` | Missing model file | Download Kokoro and place in `server/models/` |
| `ImportError: faster_whisper` | Package not installed | `pip install faster-whisper` |
| Port 8765 already in use | Previous instance running | `pkill -f server.main` |

### Pi won't connect to server

```bash
# On Pi — check network connectivity
ping 192.168.1.100

# Check if server is listening
curl http://192.168.1.100:8765/health

# Check firewall on server
sudo ufw allow 8765/tcp
sudo iptables -I INPUT -p tcp --dport 8765 -j ACCEPT
```

### No audio recorded

```bash
# On Pi — list audio devices
arecord -l

# Check ReSpeaker is detected
arecord -D plughw:seeed -c 2 -r 16000 -f S16_LE test.wav
aplay test.wav

# Check seeed-voicecard is installed
lsmod | grep snd_soc_seeed_voicecard
```

### Display not initialising

```bash
# Check SPI is enabled
ls /dev/spidev*
# Should show: /dev/spidev0.0

# Enable if missing
sudo raspi-config → Interface Options → SPI → Enable

# Check luma is installed
python3 -c "from luma.lcd.device import ili9341; print('OK')"

# Run display test
python3 -c "
from pi_client.display import DisplayManager
import time
d = DisplayManager()
for s in ['IDLE','LISTENING','THINKING','SPEAKING','HAPPY','ERROR']:
    d.set_state(s)
    time.sleep(1.5)
d.cleanup()
"
```

### Volume rocker not changing volume

| Symptom | Likely Cause | Fix |
|---|---|---|
| Pressing rocker has no effect | `ALSA_CONTROL` name wrong | Run `amixer scontrols` to list names; set `ALSA_CONTROL=<name>` |
| Logs show "Could not read ALSA volume" | amixer not installed | `sudo apt install alsa-utils` |
| Volume changes but bar not visible | Display not initialised | Check SPI is enabled and luma.lcd installed |
| Button fires multiple times per press | Mechanical bounce | Increase `VOL_STEP` env var or add hardware debounce cap |

```bash
# List available ALSA controls
amixer scontrols

# Test volume read manually
amixer sget Master

# Test volume set manually
amixer sset Master 60%
```

### Audio playback silence / distortion

```bash
# Check mpg123 is installed
which mpg123

# Test directly
echo "test" | espeak  # basic audio test
mpg123 /path/to/test.mp3

# Adjust volume
alsamixer
```

### LLM responses are slow

| Symptom | Fix |
|---|---|
| >10 s per response | Upgrade to larger CPU, or enable GPU with `WHISPER_DEVICE=cuda` |
| Ollama timing out | Check `ollama ps` — model may have been unloaded |
| Choppy streaming | Check WiFi signal strength on Pi |

### Pi Zero WH performance notes

The Pi Zero WH is a single-core ARMv6 @ 700 MHz. It handles audio recording, streaming playback, HTTP, and display rendering concurrently on one core. If the system feels sluggish:

```bash
# Reduce display frame rate (default 8 fps — try 5)
export DISPLAY_FPS=5

# Confirm the Pi is not throttling due to heat or low voltage
vcgencmd get_throttled    # 0x0 = healthy; anything else = throttling
vcgencmd measure_temp     # should be < 70 °C

# Ensure a good power supply (5 V / 2.5 A minimum for stable operation)
```

> The Pi Zero WH cannot run a 64-bit OS. If you see `Kernel panic` or a blank screen on boot, verify you flashed the **32-bit** Raspberry Pi OS image.

### Rate limit errors (HTTP 429)

The API enforces 5 requests/minute on chat endpoints. If testing rapidly, add delays between requests or use the test GUI which handles this gracefully.

### Session history lost after restart

Set `PERSIST_SESSIONS=1` to enable SQLite persistence. Sessions survive restarts and are cleaned up after 30 minutes of inactivity.

---

## 13. Updating

### Pull Latest Code

```bash
# On server machine
cd /path/to/kidbot
git pull origin main
pip install -r requirements/server_requirements.txt
sudo systemctl restart kidbot

# On Pi
cd /home/pi/kidbot
git pull origin main
pip3 install -r requirements/pi_requirements.txt
sudo systemctl restart kidbot-pi
```

### Changing the LLM Model

```bash
# 1. Download new GGUF to server/models/
# 2. Edit Modelfile:   FROM ./server/models/new-model.gguf
# 3. Re-create in Ollama:
ollama create kidbot -f Modelfile
# 4. Restart server
sudo systemctl restart kidbot
```

Or, to use a different model name without editing `Modelfile`:
```bash
export OLLAMA_MODEL=llama3.2
```

### Changing the TTS Voice

```bash
# At runtime (no restart):
curl -X POST http://localhost:8765/settings \
     -H "X-API-Key: your-key" \
     -d "voice=af_bella&speed=1.1"

# Permanently:
export KOKORO_VOICE=af_bella
sudo systemctl restart kidbot
```

### Running Tests After Update

```bash
cd /path/to/kidbot
python -m pytest tests/ -v --tb=short
```

All 207 tests should pass before deploying.
