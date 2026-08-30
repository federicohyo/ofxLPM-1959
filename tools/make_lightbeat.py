#!/usr/bin/env python3
# make_lightbeat.py -- synthesize "lightbeat": an electronic beat of light that
# stands in for the 1959 film in ofxLPM-1959.
#
# Two files come out, next to the ones the film uses:
#
#   bin/data/lightbeat.s16   interleaved stereo S16LE at 48 kHz -- the audio.
#                            The app detects clicks on its LEFT channel and
#                            turns each into one TRIG to the die.
#   bin/data/lightbeat.mp4   the picture: a bar of light touring a field of
#                            receptive fields on a closed path; every drum
#                            hit is EMITTED when the light crosses the field
#                            of that voice from its preferred direction, like
#                            a complex cell answering the moving bar. The
#                            light IS the sound IS the neuron's input.
#
# The trick is loudness layering, on two channels with different jobs:
#
#   LEFT  = the detection channel. Percussion only, each voice a fast pulse
#           plus a slow monotone tail: kick peaks 0.95, snare 0.48, hat 0.32.
#           No oscillators anywhere -- a sustained sine re-arms the detector's
#           peak follower on every cycle and would fire it over and over.
#           The tails decay slower than the 50 ms follower, so the follower
#           just rides them down and every hit fires exactly once.
#   RIGHT = the same percussion plus the musical layers (sub bass groove,
#           a quiet arp). The detector never reads this channel; the room
#           hears the downmix, so the beat sounds full while the neuron
#           still sees clean clicks.
#
# The film detector fires when the signal falls `filmThreshold` below its peak
# follower, so the `,` and `.` keys pick WHICH voice drives the neuron:
# ~0.60 -> kicks only, ~0.42 -> kicks + snares, <=0.28 -> the full hat
# groove. The table printed at the end shows the exact map for this render.
#
# Only stdlib + numpy + ffmpeg are needed. Run from anywhere:
#
#   tools/make_lightbeat.py
#
# Then copy bin/data/settings.json_lightbeat over settings.json and restart
# the app.

import os
import subprocess
import sys
import wave

import numpy as np

# --- musical / render constants ---------------------------------------------
BPM = 124.0
BARS = 32
SR = 48000
FPS = 30
WIDTH, HEIGHT = 1280, 720

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "bin", "data")
OUT_S16 = os.path.join(DATA, "lightbeat.s16")
OUT_MP4 = os.path.join(DATA, "lightbeat.mp4")
TMP_WAV = "/tmp/lightbeat_mix.wav"

BEAT = 60.0 / BPM
BAR = 4.0 * BEAT
DURATION = BARS * BAR
N = int(round(DURATION * SR))

# voice peak amplitudes -- the layering the threshold keys select from.
# Kick is 0.85 (not 0.95) so that after a fire the captured follower level,
# (A - threshold), is low enough that its own decay cannot re-cross the
# threshold once the refractory has expired, for every threshold >= 0.10.
AMP_KICK, AMP_SNARE, AMP_HAT = 0.85, 0.50, 0.34

# detector configuration, mirroring the film settings in settings.json_lightbeat
DET_FOLLOWER_FALL_MS = 50.0
# 110 ms: after a fire the follower is captured at (peak - threshold) on the
# click's falling slope, and its own 50 ms decay re-crosses `threshold`
# about 50*ln((A-thr)/thr) ms later -- 100 ms for the 0.85 kick at thr 0.10.
# Refractory must sit past the worst case we ship: 110 ms covers thresholds
# >= 0.10, and the 16th-note hats at 124 BPM (121 ms apart) still fire.
# settings.json_lightbeat carries the same value.
DET_REFRACTORY_MS = 110.0
DET_BASELINE_HZ = 0.5


# ---------------------------------------------------------------------------
# the beat: event lists, then synthesis
# ---------------------------------------------------------------------------

def build_events():
    # Kicks and snares never coincide: each threshold layer then ADDS events
    # at new times (thr low -> snares appear between kicks), which is what
    # makes the `,` key feel like it is adding a voice to the neuron's part.
    kicks, snares, hats = [], [], []
    for bar in range(BARS):
        t0 = bar * BAR
        for beat in range(4):
            tb = t0 + beat * BEAT
            if beat in (0, 2):
                kicks.append(tb)                   # half-time pulse
            else:
                snares.append(tb)                  # backbeat
            hats.append(tb + 0.5 * BEAT)           # offbeat "and"
        if bar % 4 == 3:                           # 16th-note fill, last beat
            for k in (1, 3):                       # 3.5 already plays the regular offbeat
                hats.append(t0 + 3 * BEAT + k * 0.25 * BEAT)
    return (np.array(kicks), np.array(snares), np.array(hats))


def onsets_to_track(onsets, length, gen):
    """Return an index->amplitude impulse track at onset times."""
    idx = np.minimum((onsets * SR).astype(int), length - 1)
    tr = np.zeros(length)
    tr[idx] = 1.0
    return tr


def click(t, width_ms):
    """Raised-cosine pulse, exactly zero outside its width. No oscillation,
    so the detector's follower fires on it once and is never re-armed."""
    w = width_ms * 1e-3
    out = np.zeros_like(t)
    m = t < w
    out[m] = 0.5 * (1.0 + np.cos(np.pi * (2.0 * t[m] / w - 1.0)))
    return out


def lp_noise_tail(t, tau_s, cutoff_hz, rng):
    """Lowpassed noise under a slow exponential. Slower than the 50 ms
    follower, monotone-looking on its scale, so the follower rides it down
    instead of firing on it."""
    noise = rng.standard_normal(len(t))
    k = 1.0 - np.exp(-2.0 * np.pi * cutoff_hz / SR)
    out = np.empty_like(t)
    acc = 0.0
    for i in range(len(t)):
        acc += (noise[i] - acc) * k
        out[i] = acc
    out /= np.max(np.abs(out)) + 1e-9
    return out * np.exp(-t / tau_s)


def place(buf, sig, onset, amp):
    i = int(onset * SR)
    j = min(i + len(sig), len(buf))
    buf[i:j] += amp * sig[: j - i]


def synth_audio():
    rng = np.random.default_rng(1959)
    kicks, snares, hats = build_events()

    t_long = np.arange(int(0.60 * SR)) / SR
    t_short = np.arange(int(0.10 * SR)) / SR

    # LEFT: detection channel. Click + slow tail per voice, nothing else.
    # Every click's peak must clear (peak - threshold) at the moment it ends,
    # so tails start low; every tail then decays slower than the 50 ms
    # follower (kick) or so far below its peak that its fall can never
    # re-reach a threshold (snare, hat).
    left = np.zeros(N)
    for t in kicks:
        place(left, AMP_KICK * (click(t_long, 4.0) + 0.25 * lp_noise_tail(t_long, 0.13, 70.0, rng)), t, 1.0)
    for t in snares:
        place(left, AMP_SNARE * (click(t_short, 3.0) + 0.10 * np.exp(-t_short / 0.03)), t, 1.0)
    for t in hats:
        place(left, AMP_HAT * (click(t_short, 2.0) + 0.09 * np.exp(-t_short / 0.015)), t, 1.0)

    # RIGHT: the same percussion rebuild, plus sub bass and a quiet arp --
    # everything the neuron does not need to see. The room hears (L+R)/2.
    right = np.zeros(N)
    for t in kicks:
        f_body = 48.0
        body = np.sin(2 * np.pi * f_body * t_long) * np.exp(-t_long / 0.18)
        place(right, 0.80 * click(t_long, 4.0) + 0.55 * body, t, 1.0)
    for t in snares:
        noise = rng.standard_normal(len(t_short))
        band = noise - np.convolve(noise, np.ones(24) / 24.0, mode="same")
        band /= np.max(np.abs(band)) + 1e-9
        place(right, 0.42 * click(t_short, 3.0) + 0.30 * band * np.exp(-t_short / 0.07), t, 1.0)
    for t in hats:
        place(right, 0.28 * click(t_short, 1.0), t, 1.0)

    roots = [55.0, 55.0, 43.65, 49.0]           # A1 A1 F1 G1, one per 4-bar phrase
    for step in range(BARS * 8):
        t0 = step * 0.5 * BEAT
        f = roots[(step // 32) % 4]
        t = np.arange(min(int(0.45 * SR), N - int(t0 * SR))) / SR
        if len(t) == 0:
            continue
        note = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)
        place(right, note * np.exp(-t / 0.16), t0, 0.30)

    arp_notes = [220.0, 261.63, 329.63, 440.0, 329.63, 261.63]
    for i, t0 in enumerate(np.arange(0, DURATION, 0.5 * BEAT)):
        f = arp_notes[i % len(arp_notes)]
        t = np.arange(min(int(0.3 * SR), N - int(t0 * SR))) / SR
        if len(t) == 0:
            continue
        bell = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.10)
        place(right, bell, t0, 0.10)

    # no "mastering": the chosen peaks ARE the instrument's layers
    np.clip(left, -1.0, 1.0, out=left)
    np.clip(right, -1.0, 1.0, out=right)
    return left, right, (kicks, snares, hats)


def write_outputs_audio(left, right):
    inter = np.empty(N * 2, dtype="<i2")
    inter[0::2] = (np.clip(left, -1.0, 1.0) * 32767.0).astype("<i2")
    inter[1::2] = (np.clip(right, -1.0, 1.0) * 32767.0).astype("<i2")
    with open(OUT_S16, "wb") as f:
        inter.tofile(f)
    with wave.open(TMP_WAV, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(inter.tobytes())
    print(f"wrote {OUT_S16}  ({N} frames = {N/SR:.2f} s)")


# ---------------------------------------------------------------------------
# offline copy of the app's film detector (Detector.h, level norm off,
# background mult 0). Used to map filmThreshold -> which voices fire.
# ---------------------------------------------------------------------------

def detect(x, threshold):
    a_hp = np.exp(-2 * np.pi * DET_BASELINE_HZ / SR)
    k_fall = 1.0 - np.exp(-1.0 / (DET_FOLLOWER_FALL_MS * 1e-3 * SR))
    refr = int(DET_REFRACTORY_MS * 1e-3 * SR)
    hp = x_prev = peak = 0.0
    refrac_until = -1
    events = []
    for i, xin in enumerate(x):
        hp = a_hp * (hp + xin - x_prev)
        x_prev = xin
        vd = hp
        if vd > peak:
            peak = vd
        else:
            peak += (vd - peak) * k_fall
        fall = peak - vd
        if i >= refrac_until and fall >= threshold:
            events.append(i)
            peak = vd
            refrac_until = i + refr
    return np.array(events) / SR


def label_events(det_times, truths):
    names = ("kick", "snare", "hat")
    counts = {n: 0 for n in names}
    counts["other"] = 0
    for t in det_times:
        best, best_d = "other", 0.030
        for name, arr in zip(names, truths):
            if len(arr) == 0:
                continue
            d = np.min(np.abs(arr - t))
            if d < best_d:      # strict: on a tie the earlier (louder) voice wins
                best, best_d = name, d
        counts[best] += 1
    return counts


def report_thresholds(mix, truths):
    print("\nfilmThreshold -> what the neuron is fed (this render):")
    print(f"{'thr':>6} {'events':>7} {'Hz':>6}  voices")
    for thr in (0.60, 0.50, 0.42, 0.35, 0.30, 0.22, 0.15, 0.10, 0.07):
        ev = detect(mix.astype(np.float64), thr)
        c = label_events(ev, truths)
        hz = len(ev) / DURATION
        voices = "  ".join(f"{k}:{v}" for k, v in c.items() if v)
        print(f"{thr:6.2f} {len(ev):7d} {hz:6.2f}  {voices}")


# ---------------------------------------------------------------------------
# the picture: a light travelling across a field of receptive fields.
#
# A short bar of light tours the screen on a closed billiard path at constant
# speed (so time is distance). Scattered over the screen are receptive
# fields -- one per drum hit of one 4-bar loop of the music -- each drawn as
# a faint ring with a dash marking its preferred direction. The path is
# chosen so the bar crosses each field, from its preferred direction, exactly
# when that voice should sound: the beat is emitted BY the picture, light
# entering a field from the right direction and firing, like a complex cell.
# Two decoy fields sit on the path as well, but crossed from the wrong
# direction: the light passes and nothing fires.
# ---------------------------------------------------------------------------

LOOP_BARS = 4
FIELD_RHO = 0.10            # receptive field radius, units of half-height
VOICE_COLORS = {"k": np.array([0.55, 0.85, 1.00]),
                "s": np.array([1.00, 0.35, 0.60]),
                "h": np.array([1.00, 0.75, 0.30])}


def loop_events():
    """The hits of ONE 4-bar loop, as (time, voice), same pattern the audio
    synth plays (build_events is 8 identical loops of this)."""
    T_LOOP = LOOP_BARS * BAR
    events = []
    for bar in range(LOOP_BARS):
        t0 = bar * BAR
        for beat in range(4):
            tb = t0 + beat * BEAT
            events.append((tb, "k" if beat in (0, 2) else "s"))
            events.append((tb + 0.5 * BEAT, "h"))
    for k in (1, 3):                        # the fill, last beat of the loop
        events.append((3 * BAR + 3 * BEAT + k * 0.25 * BEAT, "h"))
    return sorted(events), T_LOOP


def billiard(step_x, step_y, off_x, off_y, t_loop):
    """Closed constant-speed path with wall reflections, done unfolded:
    the position is a triangle wave of a straight drift, so after t_loop the
    light is back where it started going the same way (step_x * t_loop is a
    whole number of wall-to-wall periods by construction at the call site).
    Returns pos_vel(t) -> (x, y, vx, vy) in centred half-height units."""
    WN = WIDTH / HEIGHT
    px, py = 4.0 * WN, 4.0
    speed = float(np.hypot(step_x, step_y))

    def at(t):
        X = (off_x + step_x * t) % px
        Y = (off_y + step_y * t) % py
        x = (X if X < 2 * WN else px - X) - WN
        y = (Y if Y < 2.0 else py - Y) - 1.0
        vx = (step_x if X < 2 * WN else -step_x) / speed
        vy = (step_y if Y < 2.0 else -step_y) / speed
        return x, y, vx, vy

    return at

def place_fields(events, t_loop):
    """Each hit of the 4-bar loop gets a field where the light happens to be
    at that moment, with a preferred direction equal to the light's there.
    Step sizes close the path (step * t_loop = a whole number of wall-to-wall
    periods), so every loop the light retraces the same geometry and the same
    fields fire. Offsets and step shape are searched so the fields spread
    out and keep off the walls. Two decoys sit on the path too, with their
    dash rotated away from the true crossing direction: the light passes and
    they stay dark."""
    WN = WIDTH / HEIGHT
    candidates = []
    for kx, ky in ((1, 1), (2, 1), (1, 2), (3, 1), (2, 3), (3, 2)):
        sx, sy = kx * 4 * WN / t_loop, ky * 4 / t_loop
        for ox in np.linspace(0, 4 * WN, 13, endpoint=False):
            for oy in np.linspace(0, 4, 13, endpoint=False):
                at = billiard(sx, sy, ox, oy, t_loop)
                pts = np.array([at(t)[:2] for t, _ in events])
                dd = np.sqrt(((pts[:, None, :] - pts[None, :, :]) ** 2).sum(-1))
                dd += np.eye(len(pts)) * 9.0
                margin = np.minimum(WN - np.abs(pts[:, 0]), 1 - np.abs(pts[:, 1]))
                # soft score: keep fields apart first, off the walls second
                score = dd.min() + 1.5 * np.clip(margin.min(), 0.0, FIELD_RHO)
                candidates.append((score, dd.min(), margin.min(), at))
    best, spacing, wall, at = max(candidates, key=lambda c: c[0])
    print(f"field layout: min spacing {spacing:.2f}, min wall margin {wall:.2f}"
          " (half-heights)")

    fields = []
    for t, voice in events:
        x, y, vx, vy = at(t)
        fields.append({"pos": (x, y), "dir": (vx, vy), "voice": voice,
                       "t_loop": t, "decoy": False, "E": 0.0})

    # two decoy fields, on the path between hits, dash rotated 75 degrees
    # away from the direction the light actually crosses them
    ts = [t for t, _ in events] + [events[0][0] + t_loop]
    gaps = sorted(((ts[i + 1] - ts[i], ts[i]) for i in range(len(ts) - 1)),
                  reverse=True)
    ang = np.radians(75.0)
    ca, sa = np.cos(ang), np.sin(ang)
    for gap, tstart in gaps[:4:2]:
        x, y, vx, vy = at(tstart + 0.5 * gap)
        fields.append({"pos": (x, y), "dir": (vx * ca - vy * sa, vx * sa + vy * ca),
                       "voice": "h", "t_loop": None, "decoy": True, "E": 0.0})
    return at, fields, t_loop


def render_video():
    n_frames = int(round(DURATION * FPS))
    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    xs = (xs - WIDTH / 2) / (HEIGHT / 2)      # units of half-height
    ys = (ys - HEIGHT / 2) / (HEIGHT / 2)
    acc = np.zeros((HEIGHT, WIDTH, 3), np.float32)
    scan = (0.94 + 0.06 * np.sin(ys * HEIGHT * np.pi / 3.0)).astype(np.float32)

    events_loop, t_loop = loop_events()
    light_at, fields, t_loop = place_fields(events_loop, t_loop)

    # static layer (rings + direction dashes) and per-field flash patches,
    # built once and reused for every frame
    base = np.zeros((HEIGHT, WIDTH, 3), np.float32)
    L = 0.16                                     # patch half-size, half-heights
    for f in fields:
        x0, y0 = f["pos"]
        dxv, dyv = f["dir"]
        col = VOICE_COLORS[f["voice"]] * (0.45 if f["decoy"] else 1.0)
        r = int(L * HEIGHT / 2)
        cx = int((x0 + WIDTH / HEIGHT) * HEIGHT / 2)
        cy = int((y0 + 1.0) * HEIGHT / 2)
        y1, y2 = max(0, cy - r), min(HEIGHT, cy + r)
        x1, x2 = max(0, cx - r), min(WIDTH, cx + r)
        pyy, pxx = ys[y1:y2, x1:x2] - y0, xs[y1:y2, x1:x2] - x0
        dist = np.hypot(pxx, pyy)
        ring = np.exp(-((dist - FIELD_RHO) ** 2) / (2 * 0.012 ** 2))
        u2, v2 = pxx * dxv + pyy * dyv, -pxx * dyv + pyy * dxv
        dash = (np.abs(u2) < 0.55 * FIELD_RHO) * np.exp(-(v2 / (0.10 * FIELD_RHO)) ** 2)
        mark = 0.16 * ring + 0.10 * dash
        base[y1:y2, x1:x2] += mark[..., None] * col[None, None, :]
        f["patch"] = (np.s_[y1:y2, x1:x2], ring)

    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
           "-i", TMP_WAV, "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-crf", "19", "-c:a", "aac", "-b:a", "160k", "-shortest", OUT_MP4]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # flat, global fire list: every field flashes once per loop, at its hit
    fire = sorted((n * t_loop + f["t_loop"], f)
                  for n in range(BARS // LOOP_BARS) for f in fields
                  if f["t_loop"] is not None)
    ev_i = 0
    t_prev = 0.0
    for fr in range(n_frames):
        t = fr / FPS
        dt = t - t_prev
        t_prev = t
        while ev_i < len(fire) and fire[ev_i][0] <= t:
            fire[ev_i][1]["E"] += 1.0
            ev_i += 1
        for f in fields:
            f["E"] *= np.exp(-dt / 0.30)

        bx, by, bvx, bvy = light_at(t % t_loop)
        # the bar of light is oriented PERPENDICULAR to its motion, like the
        # 1959 stimulus a complex cell answers
        a = (xs - bx) * (-bvy) + (ys - by) * bvx       # along the bar
        c = (xs - bx) * bvx + (ys - by) * bvy          # along the motion
        core = np.exp(-(c / 0.028) ** 2) * np.exp(-np.maximum(0, np.abs(a) - 0.14) ** 2 / 0.01)
        glow = np.exp(-(a ** 2 + c ** 2) / 0.006) * 0.35

        flash = np.zeros((HEIGHT, WIDTH, 3), np.float32)
        for f in fields:
            if f["E"] > 0.01:
                sl, ring = f["patch"]
                flash[sl] += (f["E"] * 0.9 * ring)[..., None] \
                    * VOICE_COLORS[f["voice"]][None, None, :]

        acc = acc * 0.86 + (core * 1.4 + glow)[..., None] * np.array([0.9, 0.95, 1.0]) \
            + flash
        frame = (1.0 - np.exp(-(acc + base) * scan[..., None] * 1.4)) * 255.0
        proc.stdin.write(frame.astype(np.uint8).tobytes())
        if fr % 300 == 0:
            print(f"  frame {fr}/{n_frames}", flush=True)

    proc.stdin.close()
    proc.wait()
    print(f"wrote {OUT_MP4}")


def main():
    os.makedirs(DATA, exist_ok=True)
    left, right, truths = synth_audio()
    write_outputs_audio(left, right)
    report_thresholds(left, truths)
    if "--no-video" not in sys.argv:
        render_video()
    print("\ndone. To use: cp bin/data/settings.json_lightbeat bin/data/settings.json"
          " and restart the app.")


if __name__ == "__main__":
    sys.exit(main())
