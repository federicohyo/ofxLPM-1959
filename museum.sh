#!/bin/sh
# 1959 at the museum, two DP monitors off the dock:
#
#   DP-2-2  x=3840  the app (start.sh): spiral + Hubel & Wiesel film. Its
#                   soundtrack goes out through PulseAudio, and that is what
#                   stimulates the die via the chip server on the Pi.
#   DP-2-1  x=1920  the two-minute story film, muted, looping. The subtitles
#                   are burned in, so it needs no sound.
#
# Swap APP_X and VIDEO_X to trade the two monitors. Stop: focus the app and
# type quit, then pkill -x ffplay.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

APP_X=3840        # DP-2-2, the 24"
VIDEO_X=1920      # DP-2-1, the 27"
VIDEO="$HERE/video/1959_loop.mp4"

[ -f "$VIDEO" ] || { echo "ERROR: no $VIDEO" >&2; exit 1; }

# a clean start (by process name: the paths here all contain "ofxLPM-1959")
pkill -x ofxLPM-1959 2>/dev/null || true
pkill -x ffplay 2>/dev/null || true
sleep 1

# no blanking, no lock, no suspend while the piece runs (it lives on AC)
xset s off -dpms 2>/dev/null || true
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null || true
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing 2>/dev/null || true
gsettings set org.gnome.settings-daemon.plugins.power lid-close-ac-action nothing 2>/dev/null || true

# the app on DP-2-2: start.sh launches it and holds its volume at 100%
"$HERE/start.sh" &
WID=$(xdotool search --sync --onlyvisible --name "^1959$" | head -1)
xdotool windowmove "$WID" "$APP_X" 0
wmctrl -i -a "$WID"
wmctrl -i -r "$WID" -b add,fullscreen

# the story film on DP-2-1, muted, looping. GNOME fullscreens a window on the
# monitor it is sitting on, so: bring it up, put it there, then fullscreen.
ffplay -an -loop 0 -hide_banner -loglevel quiet "$VIDEO" &
FWID=$(xdotool search --sync --class ffplay | head -1)
xdotool windowactivate --sync "$FWID"
sleep 1
xdotool windowmove "$FWID" "$VIDEO_X" 0
xdotool windowsize "$FWID" 1920 1080
sleep 2
wmctrl -i -r "$FWID" -b add,fullscreen

# focus back on the app (cal / quit keys), cursor on the laptop own screen
sleep 2
wmctrl -i -a "$WID"
xdotool mousemove 960 540

wait
