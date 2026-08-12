#!/usr/bin/env bash
# Provision the two assets that are not in git, and decode the soundtrack.
#
# video.mp4 is somebody else's work and this repository is meant to go public,
# so it is not redistributed here. audio.s16 is derived from it and is ~127 MB,
# so there is no reason to store it either. Both already exist for the other
# apps; if one is there this links to it rather than making a third copy.
#
# The soundtrack has to exist as raw samples because the analysis needs the
# samples themselves and ofVideoPlayer only hands its audio to the sound card.
# The app memory-loads the PCM and is both the player and the analyser.
set -euo pipefail
cd "$(dirname "$0")/bin/data"

if [ ! -f video.mp4 ]; then
  for src in ../../../ofxCaravanViewer/bin/data/video.mp4 \
             ../../../ofxLPM/coro-hubel-wiesel/bin/data/video.mp4; do
    if [ -f "$src" ]; then
      ln -sf "$src" video.mp4
      echo "video.mp4 -> $src"
      break
    fi
  done
fi
[ -e video.mp4 ] || { echo "put the film at bin/data/video.mp4" >&2; exit 1; }

RATE="${1:-48000}"

if [ ! -f audio.s16 ]; then
  for src in ../../../ofxCaravanViewer/bin/data/audio.s16 \
             ../../../ofxLPM/coro-hubel-wiesel/bin/data/audio.s16; do
    if [ -f "$src" ]; then
      ln -sf "$src" audio.s16
      echo "audio.s16 -> $src  (assumes it was decoded at ${RATE} Hz)"
      break
    fi
  done
fi

if [ ! -e audio.s16 ]; then
  echo "decoding video.mp4 -> audio.s16 (S16LE stereo ${RATE} Hz)"
  gst-launch-1.0 -q filesrc location=video.mp4 ! decodebin ! audioconvert \
    ! audioresample ! "audio/x-raw,format=S16LE,channels=2,rate=${RATE},layout=interleaved" \
    ! filesink location=audio.s16
fi

python3 - "$RATE" <<'PY'
import os, sys
n = os.path.getsize("audio.s16") // 4
print(f"  {n} frames = {n/int(sys.argv[1]):.1f} s")
PY
