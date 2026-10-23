#!/bin/sh
# Cycle the calling app's PulseAudio stream to the next output:
#   jack (analog) -> bluetooth -> hdmi -> jack ...
#
# Usage: switch_audio_output.sh <pid-of-app>
# Called by the app itself (key O) so the move lands in the app's log.
PID="${1:-}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# Find the app's own stream by process id (precise, unlike name matching).
SID=$(pactl list sink-inputs | awk -v p="$PID" '
    /^Sink Input #/ {id = substr($3, 2)}
    index($0, "application.process.id = \"" p "\"") {print id; exit}')
if [ -z "$SID" ]; then
    echo "audio output: no mixer stream for pid $PID"
    exit 1
fi

# Which sink the stream is on right now (a sink index number).
CURIDX=$(pactl list sink-inputs | awk -v s="$SID" '
    /^Sink Input #/ {id = substr($3, 2); me = (id == s)}
    me && $1 == "Sink:" {print $2; exit}')

# Make sure an HDMI sink exists (created once per session; harmless if the
# device is busy or already served -- the grep below covers the common case).
pactl list short sinks | grep -q hdmi || \
    pactl load-module module-alsa-sink device=hw:0,3 sink_name=hdmi_out >/dev/null 2>&1

# Candidates in cycle order. sort -k2 gives analog < bluetooth < hdmi.
ORDER=$(pactl list short sinks | awk '
    $2 ~ /analog-stereo/ {print $1 " analog"}
    $2 ~ /^bluez_sink/   {print $1 " bluetooth"}
    $2 ~ /hdmi/          {print $1 " hdmi"}' | sort -k2 | awk '{print $1}')

TOTAL=$(printf '%s\n' $ORDER | grep -c .)
[ "$TOTAL" -ge 1 ] || { echo "audio output: no sinks found"; exit 1; }

POS=0; N=0
for i in $ORDER; do
    N=$((N + 1))
    [ "$i" = "$CURIDX" ] && POS=$N
done
NPOS=$((POS % TOTAL + 1))

N=0; TARGET=""
for i in $ORDER; do
    N=$((N + 1))
    [ "$N" = "$NPOS" ] && TARGET=$i
done
[ -n "$TARGET" ] || TARGET=$(printf '%s\n' $ORDER | head -1)

NAME=$(pactl list short sinks | awk -v t="$TARGET" '$1 == t {print $2}')
if pactl move-sink-input "$SID" "$TARGET"; then
    echo "audio output -> $NAME"
else
    echo "audio output: could not move to $NAME"
    exit 1
fi
