#!/usr/bin/env bash
# Try to recover a wedged USB serial/audio device without rebooting the Pi.
# Usage: sudo ./scripts/recover-usb.sh [/dev/ttyACM0]
set -euo pipefail

DEV="${1:-/dev/ttyACM0}"

echo "==> Stopping Q-Remote (releases serial + audio)..."
systemctl stop q-remote-v3.service 2>/dev/null || systemctl stop q-remote.service 2>/dev/null || true
sleep 1

if [[ -e "$DEV" ]]; then
    USB_NODE="$(readlink -f "/sys/class/tty/$(basename "$DEV")/device" 2>/dev/null | sed 's|/tty/ttyACM.*||' || true)"
    if [[ -n "$USB_NODE" && -d "$USB_NODE" ]]; then
        BUS_ID="$(basename "$USB_NODE")"
        echo "==> USB unbind/bind for $BUS_ID"
        echo "$BUS_ID" > /sys/bus/usb/drivers/usb/unbind 2>/dev/null || true
        sleep 2
        echo "$BUS_ID" > /sys/bus/usb/drivers/usb/bind 2>/dev/null || true
        sleep 2
    fi
fi

echo "==> Reloading cdc_acm driver..."
modprobe -r cdc_acm 2>/dev/null || true
sleep 1
modprobe cdc_acm 2>/dev/null || true
sleep 2

echo "==> If still missing: unplug AIOC USB, wait 5s, plug back in."
ls -la /dev/ttyACM* 2>/dev/null || echo "(no ttyACM yet)"
aplay -l 2>/dev/null | grep -i allinone || true

echo "==> Starting Q-Remote..."
systemctl start q-remote-v3.service 2>/dev/null || systemctl start q-remote.service
sleep 3
systemctl is-active q-remote-v3.service 2>/dev/null || systemctl is-active q-remote.service 2>/dev/null || true
curl -s http://127.0.0.1:8080/api/health || true
echo
