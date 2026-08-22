#!/usr/bin/env bash
# Install the udev rules and the systemd service that keep the piece served.
#
# After this, the plinth machine needs no command at all: plug the die and the
# scope in and the stream comes up on 9100/9101/9102; unplug either and it goes
# away. Run once, on the plinth machine, from the deployed tree.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

[[ -x "$ROOT/.venv/bin/python3" ]] || { echo "ERROR: no venv at $ROOT/.venv" >&2; exit 1; }
[[ -f "$ROOT/run_neuron_test.py" ]] || { echo "ERROR: no flasher at $ROOT/run_neuron_test.py" >&2; exit 1; }

# The service names its paths and its user absolutely, because systemd has no
# notion of where this tree is. Rewrite them for wherever it actually landed.
sed -e "s#/home/hyo/chip1959#$ROOT#g" \
    -e "s#^User=hyo\$#User=$USER#" \
    -e "s#^Group=hyo\$#Group=$USER#" \
    "$HERE/chip1959.service" | sudo tee /etc/systemd/system/chip1959.service >/dev/null

sudo cp "$HERE/70-chip1959.rules" /etc/udev/rules.d/70-chip1959.rules
# 11-ftdi.rules said the same thing about permissions, less specifically.
sudo rm -f /etc/udev/rules.d/11-ftdi.rules

sudo systemctl daemon-reload
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --subsystem-match=tty

echo
echo "installed. the devices now pull the service in by themselves:"
echo "  systemctl status chip1959          # is it up"
echo "  journalctl -u chip1959 -f          # what it is saying"
echo
echo "there is nothing to enable -- unplug either device and it stops,"
echo "plug both in and it starts, at boot included."
