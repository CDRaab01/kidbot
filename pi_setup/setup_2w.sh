#!/bin/bash
# KidBot Pi Zero 2W setup script
# Run on a fresh Raspberry Pi OS Lite (32-bit) install
# Usage: cd ~/kidbot && sudo bash pi_setup/setup_2w.sh
#
# Pi Zero 2W notes:
#   - ARMv8 quad-core 1 GHz — use 32-bit OS for HAT compatibility
#   - DISPLAY_FPS=10 (smoother animation vs WH's 8)
#   - Uses mainline snd_soc_tlv320aic3x driver (seeed-voicecard fails on 6.18+)
#   - MICBIAS set via DTS property ai3x-micbias-vg — no i2cset needed
#   - Mixer applied by control name (numids differ from WH, alsactl restore skipped)

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
    mpg123 ffmpeg device-tree-compiler

# ── 2. AIC3104 DTS overlay (mainline driver) ─────────────────────
# seeed-voicecard DKMS fails on kernel 6.18+ (API changes).
# The mainline snd_soc_tlv320aic3x module is already in the kernel;
# we just need a DTS overlay to wire it up and enable MICBIAS.
echo "[2/8] Building aic3104-soundcard DTS overlay..."
if ! test -f /boot/firmware/overlays/aic3104-soundcard.dtbo; then
    python3 - << 'PYEOF'
dts = """/dts-v1/;
/plugin/;
/ {
    compatible = "brcm,bcm2835";
    fragment@0 {
        target = <&i2s>;
        __overlay__ { status = "okay"; };
    };
    fragment@1 {
        target = <&i2c1>;
        __overlay__ {
            #address-cells = <1>;
            #size-cells = <0>;
            status = "okay";
            aic3104: aic3104@18 {
                #sound-dai-cells = <0>;
                compatible = "ti,tlv320aic3104";
                reg = <0x18>;
                status = "okay";
                ai3x-micbias-vg = <2>;
                clocks = <&clk_24mhz>;
                clock-names = "mclk";
            };
        };
    };
    fragment@2 {
        target-path = "/";
        __overlay__ {
            clk_24mhz: clk_24mhz {
                compatible = "fixed-clock";
                #clock-cells = <0>;
                clock-frequency = <24000000>;
            };
            sound {
                compatible = "simple-audio-card";
                simple-audio-card,format = "i2s";
                simple-audio-card,name = "aic3104-soundcard";
                status = "okay";
                simple-audio-card,cpu { sound-dai = <&i2s>; };
                simple-audio-card,codec {
                    sound-dai = <&aic3104>;
                    clocks = <&clk_24mhz>;
                    clock-names = "mclk";
                };
            };
        };
    };
};
"""
with open("/tmp/aic3104-soundcard.dts", "w") as f:
    f.write(dts)
PYEOF
    dtc -@ -I dts -O dtb \
        -o /boot/firmware/overlays/aic3104-soundcard.dtbo \
        /tmp/aic3104-soundcard.dts
    rm -f /tmp/aic3104-soundcard.dts
    echo "  Overlay compiled and installed."
else
    echo "  Overlay already present, skipping."
fi

# ── 3. /boot/firmware/config.txt ─────────────────────────────────
echo "[3/8] Patching /boot/firmware/config.txt..."
CONFIG=/boot/firmware/config.txt

add_if_missing() {
    grep -qF "$1" "$CONFIG" || echo "$1" >> "$CONFIG"
}

add_if_missing "dtparam=i2s=on"
add_if_missing "dtoverlay=i2s-mmap"
add_if_missing "dtoverlay=aic3104-soundcard"
add_if_missing "gpio=18=a0"

# ── 4. ALSA default card ──────────────────────────────────────────
echo "[4/8] Setting ALSA default card..."
cp "$SCRIPT_DIR/common/asound.conf" /etc/asound.conf

# ── 5. ALSA mixer settings ────────────────────────────────────────
# Applied by control name so numid changes across kernel versions don't matter.
# Requires the ReSpeaker HAT to be attached and the overlay loaded.
# If the card isn't present the script warns and continues — re-run after reboot.
echo "[5/8] Applying ALSA mixer settings..."
if aplay -l 2>/dev/null | grep -q aic3104; then
    # Capture: Line1L/Line1R fully differential, PGA 40 dB
    amixer -c 1 -q cset name='Left Line1L Mux'             differential
    amixer -c 1 -q cset name='Right Line1R Mux'            differential
    amixer -c 1 -q cset name='Left PGA Mixer Line1L Switch'   on
    amixer -c 1 -q cset name='Right PGA Mixer Line1R Switch'  on
    amixer -c 1 -q cset name='PGA Capture Switch'           on,on
    amixer -c 1 -q cset name='PGA Capture Volume'           80,80
    # Playback: DAC → Line output → NS4150 amp
    amixer -c 1 -q cset name='Left Line Mixer DACL1 Switch'   on
    amixer -c 1 -q cset name='Right Line Mixer DACL1 Switch'  on
    amixer -c 1 -q cset name='Line Playback Switch'         on,on
    amixer -c 1 -q cset name='Line Playback Volume'         118,118
    amixer -c 1 -q cset name='Line DAC Playback Volume'     118,118
    echo "  Mixer configured."
else
    echo "  WARNING: aic3104-soundcard not loaded (HAT not attached or reboot needed)."
    echo "  After rebooting with the HAT attached, re-run:"
    echo "    sudo bash $0"
fi

# ── 6. ALSA state save ────────────────────────────────────────────
echo "[6/8] Saving ALSA state for next boot..."
mkdir -p /var/lib/alsa
if aplay -l 2>/dev/null | grep -q aic3104; then
    alsactl store 1
    echo "  Saved to /var/lib/alsa/asound.state"
else
    echo "  Skipped (card not present — run: sudo alsactl store 1 after reboot)"
fi

# ── 7. Environment variables ──────────────────────────────────────
echo "[7/8] Setting environment variables in /home/pi/.bashrc..."
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
