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
│    │  Raspberry Pi Zero WH/2W  │       │    Server (PC)     │   │
│    │                          │  WiFi │                    │   │
│    │  [Button] ─► [LED]       │◄─────►│  Whisper STT       │   │
│    │  [ReSpeaker Mic HAT]     │  LAN  │  Gemma 4 E4B LLM  │   │
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
| Single-board computer | Pi Zero WH *or* Pi Zero 2W | WiFi built-in; same 40-pin header |
| Microphone | ReSpeaker 2-Mic Pi HAT | Mounts on 40-pin header |
| Display | Waveshare 2.4" Touch LCD (B) | ILI9341, 320×240, SPI |
| Push button | Momentary tactile switch | Normally open, connects to GND |
| LED | 5 mm LED + 220 Ω resistor | Status indicator |
| Speaker | Any 3.5 mm passive speaker | Via ReSpeaker 3.5 mm jack |
| Power | 5 V / 2.5 A USB micro | micro-USB power port on both models |
| Server PC | Any x86-64 machine | Ubuntu / Windows / macOS |

### Server Minimum Specs

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 4-core x86-64 | 6+ cores |
| RAM | 8 GB | 16 GB |
| Disk | 10 GB free | 20 GB free |
| OS | Ubuntu 22.04+ / Windows 10+ | Ubuntu 22.04 LTS |

> LM Studio can use an NVIDIA GPU for faster inference via its built-in GPU settings. Set `WHISPER_DEVICE=cuda` and `WHISPER_COMPUTE_TYPE=float16` to also accelerate Whisper if a GPU is available.

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
║                      ║  │           LM Studio (desktop app)        │ ║
║  ┌──────────────┐    ║  │  google/gemma-4-e4b                      │ ║
║  │ Push button  │    ║  │  listening on localhost:1234             │ ║
║  │ (GPIO 17)    │    ║  └─────────────┬────────────────────────────┘ ║
║  └──────┬───────┘    ║                │                              ║
║         │ press      ║  ┌─────────────▼────────────────────────────┐ ║
║  ┌──────▼───────┐    ║  │       FastAPI server :8765               │ ║
║  │ AudioManager │    ║  │                                          │ ║
║  │ ReSpeaker HAT│    ║  │  ┌────────┐ ┌─────────┐ ┌────────────┐  │ ║
║  │ 16 kHz mono  │    ║  │  │Whisper │ │  LM     │ │  Kokoro    │  │ ║
║  └──────┬───────┘    ║  │  │ small  │ │ Studio  │ │  bm_lewis  │  │ ║
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
              │ face: ^ ^ smile │  │  image from web      │
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
                          │  Pi Zero WH/2W    │
                          │  192.168.1.xxx    │
                          └───────────────────┘

Internet (optional — image search: OpenVerse, Wikipedia, NASA, iNaturalist)
  Server PC ──► openverse.org, wikipedia.org, nasa.gov, inaturalist.org  (HTTPS, no auth)
─────────────────────────────────────────────────────────────────
```

**Ports used:**

| Port | Direction | Purpose |
|---|---|---|
| 8765/TCP | Pi → Server | KidBot API (HTTP) |
| 1234/TCP | localhost only | LM Studio API |
| 443/TCP | Server → Internet | Image search (OpenVerse, Wikipedia, NASA, iNaturalist) |

**Firewall:** Open port `8765` on the server machine's firewall for LAN access. Do **not** expose this port to the internet.

---

## 5. Server Setup

### 5.1 Install System Dependencies

```bash
# Ubuntu / Debian
sudo apt update
sudo apt install -y python3.11 python3-pip ffmpeg git
```

> **LM Studio** must be installed separately — download from [lmstudio.ai](https://lmstudio.ai), load the Gemma 4 E4B model, and start the Local Server before running KidBot.

### 5.2 Clone and Install

```bash
git clone https://github.com/CDRaab01/kidbot.git
cd kidbot
pip install -r requirements/server_requirements.txt
```

### 5.3 Set Up Models

```bash
# 1. Download and install LM Studio from https://lmstudio.ai
# 2. In LM Studio, search for and download: google/gemma-4-e4b
# 3. Go to the Local Server tab and load the model
# 4. Click "Start Server" — LM Studio listens on http://localhost:1234
# 5. Verify the server is running:
curl http://localhost:1234/v1/models

# 6. Download Kokoro ONNX model and voices
#    Place at: server/models/kokoro-v1.0.onnx
#              server/models/voices-v1.0.bin
```

### 5.4 Configure

```bash
# Optional — create a .env file or export in your shell:
export KIDBOT_API_KEY="your-secret-key"
export PERSIST_SESSIONS=1
export SESSION_TIMEOUT_HOURS=168       # 7 days (default); how long memory survives idle
export LOG_FILE=/var/log/kidbot/server.log
export CHILD_NAME=YourChild
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
After=network.target

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

### 5.7 Deploy via Docker (Windows / Docker Desktop)

The recommended deployment method on Windows is Docker Desktop.

```powershell
# 1. Install Docker Desktop from https://docker.com

# 2. Clone and configure:
git clone https://github.com/CDRaab01/kidbot.git
cd kidbot
copy .env.example .env
notepad .env   # set CHILD_NAME, LM_STUDIO_MODEL, etc.

# 3. Place model files:
#    server\models\kokoro-v1.0.onnx
#    server\models\voices-v1.0.bin

# 4. Create runtime directories:
mkdir logs
mkdir server\sessions

# 5. Start:
docker compose up -d
docker compose logs -f kidbot
```

`host.docker.internal` (used in `LM_STUDIO_URL`) resolves automatically on Docker Desktop — LM Studio runs on the Windows host and the container reaches it at `http://host.docker.internal:1234/v1`.

Once you see `Application startup complete`, the server is ready at `http://localhost:8765`.

---

## 6. Raspberry Pi Setup

### 6.1 Operating System

Flash **Raspberry Pi OS Lite (32-bit)** to a microSD card using Raspberry Pi Imager.

> **Board note:** The Pi Zero WH (ARMv6/BCM2835) *cannot* boot a 64-bit OS — always use the 32-bit image. The Pi Zero 2W supports both; 32-bit is used here so one image works on either board.

During imaging, configure:
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

### 6.3 Install Audio Driver (Pi Zero 2W)

The Pi Zero 2W uses the **mainline** `snd_soc_tlv320aic3x` kernel driver with a custom device-tree overlay. The seeed-voicecard DKMS driver is **not used** — it fails to build against kernel 6.18+ due to API changes.

The automated setup script handles everything:

```bash
cd /home/pi/kidbot/pi_setup
sudo bash setup_2w.sh
sudo reboot
```

The script:
- Compiles and installs a custom `aic3104-soundcard.dtbo` device-tree overlay
- Enables the overlay and configures I2S clock forcing in `/boot/firmware/config.txt`
- Sets MICBIAS to 2.5 V via the DTS property `ai3x-micbias-vg = <2>`
- Sets initial PCM volume and saves ALSA state with `alsactl store 1`

After reboot, verify the codec is loaded:
```bash
aplay -l          # should show "aic3104" device
amixer sget PCM   # should show [xx%] volume control
```

**System packages needed:**
```bash
sudo apt install -y python3-pip python3-venv portaudio19-dev mpg123 pulseaudio-utils alsa-utils
```

> `pulseaudio-utils` provides `paplay`, which is used for volume blip sounds. PipeWire (the default audio server in Bookworm) holds the ALSA device after first use; `paplay` routes through PipeWire's PulseAudio compatibility layer and avoids "device busy" errors.

> **Pi Zero WH:** the seeed-voicecard DKMS driver may work on older kernels (pre-6.18). Use the standard Seeed installation instructions if your Pi Zero WH is on a pre-6.18 kernel image. For the Pi Zero 2W on current OS images, always use `setup_2w.sh`.

### 6.4 Install Pi Client Dependencies

```bash
sudo apt install -y mpg123 pulseaudio-utils alsa-utils python3-pip python3-venv portaudio19-dev
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

A ready-to-use service file is included in the repo at `pi_setup/kidbot.service`:

```ini
[Unit]
Description=KidBot voice assistant
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/kidbot
ExecStart=/usr/bin/python3 -m pi_client
Restart=on-failure
RestartSec=5
Environment=KIDBOT_SERVER=http://192.168.1.100:8765
Environment=KIDBOT_API_KEY=
Environment=KIDBOT_LOG_FILE=/home/pi/kidbot/logs/kidbot.log
Environment=STARTUP_VOLUME=45

[Install]
WantedBy=multi-user.target
```

```bash
# Install and enable
sudo cp pi_setup/kidbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kidbot

# Check status
sudo systemctl status kidbot

# View logs
journalctl -u kidbot -f
```

Edit `KIDBOT_SERVER` in the service file to match your server's IP address. Edit `KIDBOT_API_KEY` if you have API key authentication enabled.

---

## 7. Model Setup

### 7.1 LLM — Gemma 4 E4B (LM Studio)

LM Studio is a desktop application that serves LLM models via an OpenAI-compatible API.

**Setup:**
1. Download LM Studio from [lmstudio.ai](https://lmstudio.ai)
2. In the Discover tab, search for `google/gemma-4-e4b` and download it
3. Open the Local Server tab (⇆ icon), select the model, and click **Start Server**
4. LM Studio listens on `http://localhost:1234/v1`

**Configuration:**

| Env Variable | Value |
|---|---|
| `LM_STUDIO_URL` | `http://127.0.0.1:1234/v1` (bare Python) or `http://host.docker.internal:1234/v1` (Docker) |
| `LM_STUDIO_MODEL` | `google/gemma-4-e4b` (must match the model ID shown in LM Studio) |

**RAM requirements (Gemma 4 E4B):**

| Mode | RAM needed | Speed |
|---|---|---|
| CPU (default) | ~6 GB | ~1–3 tok/s on modern CPU |
| GPU offload | 4+ GB VRAM | ~10–30 tok/s |

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
Raspberry Pi Zero WH / Pi Zero 2W — 40-pin header (identical pinout)
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
export VOL_MAX=85          # max PCM % — NS4150 amp clips above ~85%
export ALSA_CONTROL=PCM    # amixer control name — AIC3104 DAC (default "PCM")
export STARTUP_VOLUME=45   # PCM % for boot/shutdown chimes
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
- Conversation history is held in memory (or optionally in a local SQLite file). When `PERSIST_SESSIONS=1`, the same SQLite row also stores a small `facts` dict — durable things the child volunteered (age, pet, favourites, fears, nickname) that the bot uses to feel more personal. To inspect or wipe, see *Long-term memory* in §12 Troubleshooting. The image-search HTTPS calls and (optionally) the LLM-judged conversation smoke test are the only outbound traffic.
- The Whisper STT, Gemma LLM, and Kokoro TTS all run locally — no API keys, no cloud.

---

## 10. Starting & Stopping

### Start Everything

```bash
# 1. Ensure LM Studio is running with the Gemma 4 E4B model loaded
#    (Open LM Studio → Local Server tab → select model → Start Server)

# 2. Start the KidBot server
cd /path/to/kidbot
python -m server.main

# 3. The Pi client starts automatically on boot via systemd (kidbot.service)
#    Or manually:
python3 -m pi_client
```

### Stop

```bash
# Server: Ctrl+C or
sudo systemctl stop kidbot

# Pi client:
sudo systemctl stop kidbot
# or: the Pi handles SIGINT / SIGTERM cleanly (stops playback, plays shutdown chime, GPIO cleanup, display off)
```

### Test Without Pi Hardware

**Desktop GUI (Windows/Linux/Mac):**
```bash
# Start the server first, then:
cd /path/to/kidbot
python test_gui.py
```
The test GUI connects to `http://localhost:8765` by default. Change `SERVER_URL` at the top of `test_gui.py` to target a remote server.

**Keyboard harness (on the Pi itself, no physical buttons needed):**
```bash
cd /home/pi/kidbot
python3 scripts/keyboard_test.py

# Controls:
#   SPACE         — first press starts recording; press again to stop and send
#   + or =        — volume up
#   -             — volume down
#   q or Ctrl+C   — quit (plays shutdown chime)
```
The keyboard harness runs `VolumeRocker(use_gpio=False)` so it does not conflict with GPIO pins held by a stopped service.

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
python3 -m pi_client

# File (configured via KIDBOT_LOG_FILE in the service file)
export KIDBOT_LOG_FILE=/home/pi/kidbot/logs/kidbot.log
python3 -m pi_client

# systemd
journalctl -u kidbot -f
```

### Session Inspection

```bash
# See how many sessions are active (no direct endpoint — check logs)
grep "New session" /var/log/kidbot/server.log | wc -l

# SQLite inspection (if PERSIST_SESSIONS=1; default path in 0.5+ is
# server/sessions/sessions.db, inside the mounted Docker volume)
sqlite3 server/sessions/sessions.db "SELECT session_id, last_active, facts FROM sessions;"
```

---

## 12. Troubleshooting

### Server won't start

| Symptom | Likely Cause | Fix |
|---|---|---|
| `LLM server not reachable` | LM Studio not running | Open LM Studio, load the model, and start the Local Server |
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
# Should show the AIC3104 codec as a capture device

# Check mainline driver is loaded
lsmod | grep snd_soc_tlv320

# Test recording directly (run before PipeWire starts, e.g. before kidbot boots)
arecord -D plughw:1,0 -c 1 -r 16000 -f S16_LE -d 3 test.wav
aplay -D plughw:1,0 test.wav

# If "device busy": PipeWire holds the device. Route through PipeWire:
parecord --channels=1 --rate=16000 --format=s16le test.raw
# or restart PipeWire:
systemctl --user restart pipewire
```

> **Pi Zero 2W note:** If `arecord -l` shows no capture devices, the DTS overlay may not be loaded. Re-run `pi_setup/setup_2w.sh` and reboot. Check `dmesg | grep aic3104` for driver errors.

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
| Volume stuck — only a few levels | Using `Line` or `Master` control | Switch to `PCM` control (0–127 range) — set `ALSA_CONTROL=PCM` |
| Logs show "Could not read ALSA volume" | amixer not installed | `sudo apt install alsa-utils` |
| Volume changes but bar not visible | Display not initialised | Check SPI is enabled and luma.lcd installed |
| Button fires multiple times per press | Mechanical bounce | Increase `VOL_STEP` env var or add hardware debounce cap |
| Sound distorts at high volume | Amp clipping | Set `VOL_MAX=85` — the NS4150 amp on the ReSpeaker HAT clips above ~85% PCM |

```bash
# List available ALSA controls
amixer scontrols

# Test volume read manually (AIC3104 PCM control)
amixer sget PCM

# Test volume set manually
amixer sset PCM 60%
```

### Volume blip not playing / "device busy" on aplay

Raspberry Pi OS Bookworm uses PipeWire as the audio server. PipeWire acquires `hw:1,0` (the AIC3104 device) after the first audio client connects and holds it exclusively. Direct `aplay -D plughw:1,0` will fail with "Device or resource busy" while PipeWire is running.

```bash
# Identify what holds the ALSA device
fuser /dev/snd/*
# PID will typically be pipewire

# Volume blips use paplay (PulseAudio API — PipeWire compatible)
# If blips are silent, ensure pulseaudio-utils is installed:
sudo apt install pulseaudio-utils

# Test paplay directly
paplay /usr/share/sounds/alsa/Front_Left.wav

# If PipeWire is not running (e.g. fresh boot before any audio client):
# aplay -D plughw:1,0 will work — this is how startup/shutdown chimes play
```

> The startup and shutdown chimes (`aplay -D plughw:1,0`) play at boot before PipeWire has a client, and at shutdown after PipeWire has been stopped. Any in-session sound (volume blip) must use `paplay`.

### Audio playback silence / distortion

```bash
# Check mpg123 is installed
which mpg123

# Test directly
mpg123 /path/to/test.mp3

# Adjust volume
amixer sget PCM
amixer sset PCM 60%
alsamixer
```

### LLM responses are slow

| Symptom | Fix |
|---|---|
| >10 s per response | Upgrade to larger CPU, or enable GPU with `WHISPER_DEVICE=cuda` |
| LM Studio not responding | Check LM Studio → Local Server tab — model may need reloading |
| Choppy streaming | Check WiFi signal strength on Pi |

### Pi performance notes

| Board | CPU | `DISPLAY_FPS` | Notes |
|---|---|---|---|
| Pi Zero WH | 1× ARMv6 @ 700 MHz | 8 (default) | Lower to 5 if audio crackles |
| Pi Zero 2W | 4× ARMv8 @ 1 GHz | 10 | Raise for smoother animation |

```bash
# Override display frame rate
export DISPLAY_FPS=10   # Pi Zero 2W
export DISPLAY_FPS=5    # Pi Zero WH if audio crackles

# Confirm the Pi is not throttling due to heat or low voltage
vcgencmd get_throttled    # 0x0 = healthy; anything else = throttling
vcgencmd measure_temp     # should be < 70 °C
```

> The Pi Zero WH cannot run a 64-bit OS. If you see `Kernel panic` or a blank screen on boot, verify you flashed the **32-bit** Raspberry Pi OS image.

### Rate limit errors (HTTP 429)

The API enforces 5 requests/minute on chat endpoints. If testing rapidly, add delays between requests or use the test GUI which handles this gracefully.

### Session history lost after restart

Set `PERSIST_SESSIONS=1` to enable SQLite persistence. The Pi derives a stable session id from its hostname, so the same conversation is restored across reboots. Sessions are cleaned up after `SESSION_TIMEOUT_HOURS` of inactivity (default **168** — 7 days; pre-0.5 deployments used a 30-minute default that wiped memory after lunch).

```bash
# Shorter retention if you want the bot to "forget" sooner:
export SESSION_TIMEOUT_HOURS=24
```

### Long-term memory (durable facts)

Beyond the rolling conversation history, the bot remembers a small set of durable facts the child mentions in their own words (age, pet name, fear, favourites, nickname). These are extracted with simple pattern matching — no extra LLM call — and persisted alongside the session in SQLite. Newer values overwrite older ones (so a new age replaces the old). To inspect or clear them:

```bash
sqlite3 server/sessions/sessions.db "SELECT session_id, facts FROM sessions;"
sqlite3 server/sessions/sessions.db "UPDATE sessions SET facts='{}' WHERE session_id='kidbot';"
```

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
sudo systemctl restart kidbot
```

### Changing the LLM Model

```bash
# 1. In LM Studio, download the new model from the Discover tab
# 2. In the Local Server tab, select the new model and click Start Server
# 3. Update the env var and restart:
export LM_STUDIO_MODEL=<model-id-shown-in-lm-studio>
sudo systemctl restart kidbot
```

Or set it permanently in `.env`:
```bash
LM_STUDIO_MODEL=google/gemma-4-e4b
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

All 243 tests should pass before deploying.
