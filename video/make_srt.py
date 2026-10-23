#!/usr/bin/env python3
"""Generate 1959.srt: the narration as subtitles.

Timings mirror video/Makefile's beat table:
  beat2 starts at 8 s  (narration +1.2), beat3 at 40 s (+0.8),
  beat4 at 77 s (+0.5), beat5 at 98 s (+0.6), beat6 at 119 s (+0.5).
Sentences share each beat's narration window proportionally to their length.
"""
import re

BEATS = [
    (8.0, 1.2, 24.8, "voice/beats/beat2.txt"),
    (40.0, 0.8, 32.1, "voice/beats/beat3.txt"),
    (77.0, 0.5, 18.7, "voice/beats/beat4.txt"),
    (98.0, 0.6, 17.9, "voice/beats/beat5.txt"),
    (119.0, 0.5, 18.0, "voice/beats/beat6.txt"),
]


def sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


def ts(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


n = 0
for start, offset, dur, path in BEATS:
    text = open(path).read().replace("\n", " ")
    sents = sentences(text)
    total = sum(len(s) for s in sents)
    t = start + offset
    for s in sents:
        d = dur * len(s) / total
        n += 1
        print(f"{n}\n{ts(t)} --> {ts(t + d - 0.05)}\n{s}\n")
        t += d
