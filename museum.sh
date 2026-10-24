#!/bin/sh
# 1959 at the museum, two DP monitors off the dock:
#
#   DP-2-2  x=3840  the app (start.sh): spiral + Hubel & Wiesel film. Its
#                   soundtrack goes out through PulseAudio, and that is what
#                   stimulates the die via the chip server on the Pi.
#   DP-2-1  x=1920  the movie screen (movie_screen.py + ffplay): the
#                   attract card until a visitor presses the mouse, then
#                   the story film, once, fully, with its narration. While
#                   it plays the app's sound is muted; when it ends, the
#                   card and the app's sound come back.
#
# Swap APP_X and VIDEO_X to trade the two monitors. Stop: focus the app and
# type quit, then pkill -f movie_screen.py.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

APP_X=3840        # DP-2-2, the 24"
VIDEO_X=1920      # DP-2-1, the 27"
VIDEO="$HERE/video/1959_master_1080p.mp4"
CARD="$HERE/video/titles/card-4.png"
LOG=/tmp/1959-movie.log

[ -f "$VIDEO" ] || { echo "ERROR: no $VIDEO" >&2; exit 1; }
[ -f "$CARD" ] || { echo "ERROR: no $CARD" >&2; exit 1; }

# a clean start (by process name: the paths here all contain "ofxLPM-1959")
pkill -x ofxLPM-1959 2>/dev/null || true
pkill -x ffplay 2>/dev/null || true
pkill -f movie_screen.py 2>/dev/null || true
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

# the movie screen on DP-2-1: the card until pressed, then the film once.
# The controller owns the card window and starts ffplay for the film; if
# it ever dies, this loop brings it back on the card, with the app unmuted.
(
    while :; do
        echo "$(date '+%F %T') movie screen starting" >>"$LOG"
        python3 "$HERE/movie_screen.py" >>"$LOG" 2>&1 || true
        echo "$(date '+%F %T') movie screen exited, restarting" >>"$LOG"
        sleep 2
    done
) &

# belt and braces: the controller places and fullscreens itself, but if
# the WM ignored that, put it there by hand (the window is already
# fullscreen, so a move can be refused -- never let that stop the piece)
CWID=$(xdotool search --sync --name "1959 movie screen" | head -1)
xdotool windowactivate --sync "$CWID" || true
sleep 1
xdotool windowmove "$CWID" "$VIDEO_X" 0 || true
xdotool windowsize "$CWID" 1920 1080 || true
sleep 1
wmctrl -i -r "$CWID" -b add,fullscreen || true
sleep 1
wmctrl -i -a "$WID" || true

# focus back on the app (cal / quit keys); cursor on the movie screen,
# where a visitor's press lands (the card window hides it)
xdotool mousemove 2880 540

wait
