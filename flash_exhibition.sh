#!/usr/bin/env bash
# One command to put the installation's pinned firmware on the die and clock it.
#
# The app flashes nothing itself, so whatever image is on the chip is what the piece
# runs. This flashes the image pinned in tools/firmware/neuron_handshake/ -- NOT whatever
# happens to be in the main firmware build tree, which moves with development and whose
# input pulse width differs (see that directory's README).
#
#   ./flash_exhibition.sh            # flash + clock to 25 MHz, leave the DACs alone
#   ./flash_exhibition.sh --with-dac # also run the default DAC pass (rarely wanted:
#                                    # it resets all 23 channels and clobbers tuned biases)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
HEX="$HERE/tools/firmware/neuron_handshake/neuron_handshake_25MHz_exhibition.hex"
EXPECT_SHA="1caf789f00ac01f3"
PY="$ROOT/.venv-meas/bin/python3"
CLOCK=25

[[ -f "$HEX" ]] || { echo "ERROR: pinned image missing: $HEX" >&2; exit 1; }
[[ -x "$PY"  ]] || { echo "ERROR: bench venv missing: $PY" >&2; exit 1; }

GOT="$(sha256sum "$HEX" | cut -c1-16)"
if [[ "$GOT" != "$EXPECT_SHA" ]]; then
  echo "ERROR: pinned image changed. expected $EXPECT_SHA, got $GOT" >&2
  echo "       If this was deliberate, the installation needs re-tuning -- the input" >&2
  echo "       pulse width sets charge per spike. See tools/firmware/.../README.md" >&2
  exit 1
fi

# Only one process may own the FTDI. The bracket keeps pkill from matching this script.
pkill -f "[n]euron_bridge.py" 2>/dev/null || true
pkill -f "[i]nh_scope.py"     2>/dev/null || true
sleep 1

echo "flashing the pinned exhibition image (sha $GOT) and clocking to ${CLOCK} MHz"
DAC_FLAG="--skip-dac"
[[ "${1:-}" == "--with-dac" ]] && DAC_FLAG=""
cd "$ROOT"
# flashing happens on the crystal and the DLL is engaged afterwards; run_neuron_test
# handles that order, and a CPU reset follows every HK-SPI sequence.
"$PY" run_neuron_test.py --clock "$CLOCK" --hex "$HEX" $DAC_FLAG

cat <<EOF

done. the die now runs the pinned image at ${CLOCK} MHz.

  next:  cd "$HERE" && make RunRelease
  note:  CARAVAN_CLK_MHZ must be ${CLOCK} for the app and its bridge.
         The DLL does NOT survive a power cycle -- rerun this after unplugging.
EOF
