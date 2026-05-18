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
MIC_DEVICE_HINT = "seeed"

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
