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
