#!/bin/bash
# KidBot Pi Zero 2W setup script
# Run on a fresh Raspberry Pi OS Lite (32-bit) install
# Usage: cd ~/kidbot && sudo bash pi_setup/setup_2w.sh
#
# Pi Zero 2W notes:
#   - ARMv8 quad-core 1 GHz — supports 32-bit or 64-bit OS
#   - Use 32-bit for hardware compatibility with Pi Zero WH
#   - DISPLAY_FPS=10 (smoother animation vs WH's 8)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIDBOT_SERVER="${KIDBOT_SERVER:-http://192.168.1.100:8765}"
DISPLAY_FPS=10

echo "========================================"
echo "  KidBot Setup — Pi Zero 2W"
echo "========================================"

# ── 1. System packages ────────────────────────────────────────────
echo "[1/8] Installing system packages..."
apt-get update -q
apt-get install -y -q \
    git python3-pip python3-venv \
    portaudio19-dev i2c-tools alsa-utils \
    mpg123 ffmpeg

# ── 2. ReSpeaker / AIC3104 driver ────────────────────────────────
echo "[2/8] Installing seeed-voicecard (AIC3104) driver..."
if ! lsmod | grep -q snd_soc_aic3x 2>/dev/null && \
   ! ls /boot/firmware/overlays/aic3104-soundcard.dtbo &>/dev/null; then
    TMP=$(mktemp -d)
    git clone --depth=1 https://github.com/HinTak/seeed-voicecard.git "$TMP/seeed-voicecard"
    cd "$TMP/seeed-voicecard"
    bash install.sh
    cd -
    rm -rf "$TMP"
    echo "  Driver installed. A reboot will be needed."
else
    echo "  Driver already present, skipping."
fi

# ── 3. /boot/firmware/config.txt ─────────────────────────────────
echo "[3/8] Patching /boot/firmware/config.txt..."
CONFIG=/boot/firmware/config.txt

add_if_missing() {
    grep -qF "$1" "$CONFIG" || echo "$1" >> "$CONFIG"
}

add_if_missing "dtoverlay=i2s-mmap"
add_if_missing "dtoverlay=aic3104-soundcard"
add_if_missing "gpio=18=a0"

# ── 4. ALSA default card ──────────────────────────────────────────
echo "[4/8] Setting ALSA default card..."
cp "$SCRIPT_DIR/common/asound.conf" /etc/asound.conf

# ── 5. rc.local — MICBIAS ────────────────────────────────────────
echo "[5/8] Installing rc.local (MICBIAS enable)..."
cp "$SCRIPT_DIR/common/rc.local" /etc/rc.local
chmod +x /etc/rc.local

# ── 6. ALSA mixer state ───────────────────────────────────────────
echo "[6/8] Restoring ALSA mixer state..."
mkdir -p /var/lib/alsa
cp "$SCRIPT_DIR/common/asound.state" /var/lib/alsa/asound.state
alsactl restore 1 2>/dev/null || echo "  (will apply after reboot)"

# ── 7. Environment variables ──────────────────────────────────────
echo "[7/8] Setting environment variables in ~/.bashrc..."
BASHRC="/home/pi/.bashrc"
grep -qF "KIDBOT_SERVER" "$BASHRC" || \
    echo "export KIDBOT_SERVER=$KIDBOT_SERVER" >> "$BASHRC"
grep -qF "DISPLAY_FPS" "$BASHRC" || \
    echo "export DISPLAY_FPS=$DISPLAY_FPS" >> "$BASHRC"

# ── 8. systemd service ────────────────────────────────────────────
echo "[8/8] Installing kidbot-pi systemd service..."
cat > /etc/systemd/system/kidbot-pi.service << EOF
[Unit]
Description=KidBot Pi Client
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/kidbot
Environment=KIDBOT_SERVER=$KIDBOT_SERVER
Environment=DISPLAY_FPS=$DISPLAY_FPS
ExecStart=/usr/bin/python3 -m pi_client.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable kidbot-pi

echo ""
echo "========================================"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Edit KIDBOT_SERVER in /etc/systemd/system/kidbot-pi.service"
echo "     (current: $KIDBOT_SERVER)"
echo "  2. sudo reboot"
echo "  3. After reboot: sudo systemctl start kidbot-pi"
echo "========================================"
