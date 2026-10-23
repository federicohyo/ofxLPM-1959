#!/bin/sh
# Launch 1959 and force its PulseAudio streams to full volume.
# The volume-restore feature keeps re-applying a stale 0% to every new stream
# of this app (RtAudio probing creates a few short-lived ones), so watch for
# the whole run and set each new stream once, then leave it alone.
cd "$(dirname "$0")/bin" || exit 1
./ofxLPM-1959 &
APP=$!
SEEN=""
while kill -0 "$APP" 2>/dev/null; do
    ID=$(pactl list sink-inputs 2>/dev/null | awk "/^Sink Input #/{id=substr(\$3,2)} /application.name/ && /ofxLPM-1959/{print id; exit}")
    if [ -n "$ID" ] && [ "$ID" != "$SEEN" ]; then
        pactl set-sink-input-volume "$ID" 100% 2>/dev/null
        pactl set-sink-input-mute "$ID" 0 2>/dev/null
        SEEN="$ID"
    fi
    sleep 1
done
wait $APP
