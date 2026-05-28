import os

# Server address — set KIDBOT_SERVER env var on the Pi to override
SERVER_URL = os.getenv("KIDBOT_SERVER", "http://192.168.1.100:8765")

# GPIO (BCM numbering)
BUTTON_PIN = 17   # push-to-talk button
LED_PIN = 27      # status LED

# Audio recording
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 1024
MAX_RECORD_SECONDS = 10

# The ReSpeaker 2-Mic HAT device name substring to match
MIC_DEVICE_HINT = "aic3104"

# API key — must match KIDBOT_API_KEY on the server; empty = disabled
API_KEY = os.getenv("KIDBOT_API_KEY", "")

# Logging
LOG_FILE = os.getenv("KIDBOT_LOG_FILE", "")   # path to log file; empty = stdout only

# Display (Waveshare 2.4" ILI9341, 320×240)
DISPLAY_DC        = int(os.getenv("DISPLAY_DC", "25"))
DISPLAY_BL        = int(os.getenv("DISPLAY_BL", "24"))
DISPLAY_SPI_PORT  = int(os.getenv("DISPLAY_SPI_PORT", "0"))
_raw_rst = os.getenv("DISPLAY_RST", "")
DISPLAY_RST       = int(_raw_rst) if _raw_rst.strip() else None  # None avoids LED_PIN=27 conflict
IMAGE_DISPLAY_SECONDS = int(os.getenv("IMAGE_DISPLAY_SECONDS", "8"))
DISPLAY_FPS       = int(os.getenv("DISPLAY_FPS", "8"))   # 8 fps suits Pi Zero WH single-core

# Volume rocker (BCM numbering)
VOL_UP_PIN   = int(os.getenv("VOL_UP_PIN",   "5"))   # physical pin 29
VOL_DOWN_PIN = int(os.getenv("VOL_DOWN_PIN", "6"))   # physical pin 31
VOL_STEP     = int(os.getenv("VOL_STEP",     "5"))   # percent per press (~6 hardware steps on AIC3104 PCM control, 0-127 range = 3 dB)
VOL_MIN      = int(os.getenv("VOL_MIN",      "0"))
VOL_MAX      = int(os.getenv("VOL_MAX",      "85"))   # PCM >85% (~107/127) drives the NS4150 amp into clipping
ALSA_CONTROL = os.getenv("ALSA_CONTROL",     "PCM")
STARTUP_VOLUME = int(os.getenv("STARTUP_VOLUME", "45"))  # PCM % for boot/shutdown chimes
