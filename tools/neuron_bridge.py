#!/usr/bin/env python3
"""Neuron bridge: FTDI <-> pipe protocol for ofxCaravanViewer.

Reads spike bytes from UART (ch D), emits spike events to stdout.
Reads bias/monitor commands from stdin, programs DACs via bit-bang.

Protocol (stdout -> OF app):
    READY
    LIMIT <spikes_per_sec>   # link ceiling: above this, reported rates are low
    DROPS <n>                # chip lost n>0 spikes this window -> RATE is an underestimate
    STALLS <n>               # link saturated: readout is back-pressuring the array
    S <neuron_id> <timestamp_us>
    DBG_P <synapse_id> <weight> <exc|nexc>
    DBG_S <synapse_id> <neuron_id> <exc|nexc>
    RATE <spikes_per_sec>
    BOK <name> <voltage>
    MOK <neuron_id>
    POK <synapse_id> <weight> <exc|nexc>
    SOK <synapse_id> <neuron_id> <exc|nexc>

Protocol (OF app -> stdin):
    B <name> <voltage>
    M <neuron_id>
    P <synapse_id> <weight> <exc|nexc>
    S <synapse_id> <neuron_id> <exc|nexc>
    T [count]
    TRIGRUN <hz>          # regular spike train at hz, paced by the bridge (not the GUI frame loop)
    TRIGSTOP              # stop the regular spike train
    POISSON <hz>          # start on-chip Poisson generation on the staged route
    POISSONSTOP           # stop on-chip Poisson generation
    SYNFIRE <laps>        # start RISC-V synfire chain for <laps> rounds
    SYNFIRESTOP           # stop synfire chain
    COINC <synA> <synB> <neuron> <dt_us>   # one coincidence trial (A then B after dt)
    MASK <bits>           # bit i set = stream neuron i (default 0xFFFF = all)
    ECGRUN <N|V> [neuron] [weight]  # loop one delta-coded ECG beat for bias tuning
    ECGSTOP               # stop the ECG loop
    RECORD [path]         # dump spikes to recordings/spikes_<stamp>.txt (id, ticks, us)
    RECORDSTOP            # close the recording
    QUIT
"""

import os
import sys
import time
import threading
import select

# Save real stdout before anything prints to it
_real_stdout_fd = os.dup(1)
_real_stdout = os.fdopen(_real_stdout_fd, "w", buffering=1)  # line-buffered

# Redirect sys.stdout to stderr so library prints don't pollute the pipe
sys.stdout = sys.stderr

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FIRMWARE_DIR = os.path.join(SCRIPT_DIR, "firmware")
sys.path.insert(0, FIRMWARE_DIR)

import pyftdi.serialext
from pyftdi.ftdi import Ftdi
from io import StringIO
from riscvprog import AD5664RBitBang, Config

# Bias table: (dac_id, address, name)
BIAS_TABLE = [
    (1, 0b000, "vtaup"),
    (1, 0b001, "vthrdp"),
    (1, 0b010, "vepulseextp"),
    (1, 0b011, "vipulseextp"),
    (2, 0b000, "vleakn"),
    (2, 0b001, "vtaun"),
    (2, 0b010, "vrefn"),
    (2, 0b011, "free"),
    (3, 0b000, "lna_iref"),
    (3, 0b001, "JInhWp1"),
    (3, 0b010, "JInhWp2"),
    (3, 0b011, "JInhWp3"),
    (4, 0b000, "JExcWn0"),
    (4, 0b001, "JExcWn1"),
    (4, 0b010, "JExcWn2"),
    (4, 0b011, "JExcWn3"),
    (5, 0b000, "VREF"),
    (5, 0b001, "VB1"),
    (5, 0b010, "VB2"),
    (5, 0b011, "TUNEp"),
    (6, 0b000, "buffermonp"),
    (6, 0b001, "JInhWp0"),
    (6, 0b010, "vthrdn"),
    (6, 0b011, "ifdcp"),
]

BIAS_LOOKUP = {name.lower(): (dac_id, addr, name) for dac_id, addr, name in BIAS_TABLE}

VREFP = 1.79
VREFN = 0.89
HALF_PERIOD_S = 2e-6
UART_CMD_PROGRAM_SYNC = 0xF0
UART_CMD_SPIKE_SETUP = 0xE0
UART_CMD_SEND_SPIKE = 0xE1
UART_CMD_MONITOR_SYNC = 0xD0
UART_CMD_POISSON_START = 0xE2
UART_CMD_POISSON_STOP = 0xE3
UART_CMD_SYNFIRE_START = 0xE4
UART_CMD_SYNFIRE_STOP = 0xE5
UART_CMD_SPIKE_MASK = 0xEC   # [0xEC, b0, b1, b2] -> mask = b0 | b1<<6 | (b2&0xF)<<12
UART_CMD_TRAIN_START = 0xED  # [0xED, b3,b2,b1,b0] -> regular train, 24-bit tick period;
                             # stopped by UART_CMD_POISSON_STOP (0xE3)
UART_CMD_COINCIDENCE = 0xE6
UART_CMD_SET_RECUR = 0xE9
UART_CMD_RECUR_CTRL = 0xEA
REC_SYN = 1  # dedicated recurrent synapse index (matches firmware)

# Reservoir continuous-play state (background thread that loops one delta trace)
_reservoir_thread = None
_reservoir_stop = threading.Event()
_narma_thread = None
_narma_stop = threading.Event()
_reservoir_lock = threading.Lock()  # serialize port writes with the main loop

# Regular (non-Poisson) stimulation is paced ON-CHIP by Timer0 (cmd 0xED), for the
# same reason Poisson is: the host cannot pace it. port.write() contends with the
# reader thread for the serial lock -- measured blocking up to 182 ms -- so a
# host-threaded 200 Hz train delivered only 24-121 Hz depending on reader load, and
# the GUI's own frame loop was worse still (capped at ofSetFrameRate(60)).
# Spike recorder. The reader thread writes; the stdin thread opens/closes, so the
# handle is guarded. Timestamps come from the chip's Timer0 tick, wrap-corrected.
_rec_lock = threading.Lock()
_rec_file = None
_rec_count = 0

# Live per-neuron output-spike counter (reader thread increments; XORPROBE snapshots
# deltas per corner). Plain ints -> CPython increment is atomic enough for counting.
_live_counts = [0] * 16

RESERVOIR_WREC = "reservoir_wrec.txt"
RESERVOIR_DELTA = "reservoir_delta.txt"
RESERVOIR_IN_NEURONS = list(range(9))   # delta input -> neurons 0-8; 9-15 fire only via recurrence


def _find_file(path):
    import os
    for p in (path, os.path.join("ofxCaravanViewer", "bin", path)):
        if os.path.exists(p):
            return p
    return path


_BIAS_NMOS = ("vleakn", "vtaun", "vrefn", "JExcWn0", "JExcWn1", "JExcWn2", "JExcWn3")


def apply_bias_dict(dac, biases):
    """Set all DAC biases from a name->voltage dict (mirrors the 'B' command)."""
    for name, voltage in biases.items():
        m = BIAS_LOOKUP.get(str(name).lower())
        if m is None:
            continue
        dac_id, addr, real = m
        try:
            v = float(voltage)
        except (TypeError, ValueError):
            continue
        if 0 <= v <= VREFN and real in _BIAS_NMOS:
            dac.set_dac_voltage(dac_id=dac_id, voltage=v, vref=VREFN,
                                half_period_s=HALF_PERIOD_S, address=addr)
        elif 0 <= v <= VREFP:
            if real == "lna_iref":
                dac.power_down_dac(dac_id=dac_id, address=addr, mode=0b00,
                                   half_period_s=HALF_PERIOD_S)
            dac.set_dac_voltage(dac_id=dac_id, voltage=v, vref=VREFP,
                                half_period_s=HALF_PERIOD_S, address=addr)


def _load_ecg(cls):
    """Read a delta event list: lines of `t_frac channel` (0=UP/exc, 1=DOWN/inh)."""
    ev = []
    with open(_find_file(ECG_BEAT_FILES[cls])) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tf, ch = line.split()[:2]
            ev.append((float(tf), int(ch)))
    return ev


def _ecg_loop(port, stop_ev, events, neuron, weight):
    """Replay one delta-coded beat into `neuron`, forever, until stopped.

    Pacing is host-side, so inter-event timing carries whatever jitter the serial lock
    imposes (port.write can block for milliseconds). That is fine for BIAS TUNING on the
    scope -- the morphology and the UP/DOWN mix are what matter -- but it is NOT a timing
    measurement. Use the reservoir scripts for data.
    """
    # weight on synapse 0, both polarities: UP drives exc, DOWN drives inh
    port.write(bytes([UART_CMD_PROGRAM_SYNC, 0, weight & 0x0F, 1]))
    port.write(bytes([UART_CMD_PROGRAM_SYNC, 0, weight & 0x0F, 0]))
    while not stop_ev.is_set():
        t0 = time.monotonic()
        cur = None
        for tf, ch in events:
            deadline = t0 + tf * ECG_T
            while not stop_ev.is_set() and time.monotonic() < deadline:
                stop_ev.wait(0.0005)
            if stop_ev.is_set():
                return
            if ch != cur:
                port.write(bytes([UART_CMD_SPIKE_SETUP, 0, neuron & 0x0F,
                                  1 if ch == 0 else 0]))
                cur = ch
            port.write(bytes([UART_CMD_SEND_SPIKE]))
        stop_ev.wait(ECG_GAP)


def _setup_reservoir(port, density, rec_w, in_weight, inh_frac=0.2):
    """Program input synapse + a cross-coupled recurrent net (wrec at `density` conns/neuron,
    `inh_frac` inhibitory) and enable recurrence. Shared by NARMAPROBE and the NARMA stream loop.
    Returns the conn list."""
    regenerate_wrec(density, RESERVOIR_WREC, inh_frac)
    conns = []
    try:
        with open(_find_file(RESERVOIR_WREC)) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    s, d, c, e = (int(x) for x in line.split()[:4])
                    conns.append((s, d, c, e))
    except OSError:
        pass
    with _reservoir_lock:
        port.write(bytes([UART_CMD_PROGRAM_SYNC, 0, in_weight & 0x0F, 1]))
        port.write(bytes([UART_CMD_PROGRAM_SYNC, 0, in_weight & 0x0F, 0]))
        port.write(bytes([UART_CMD_PROGRAM_SYNC, REC_SYN, rec_w & 0x0F, 1]))
        port.write(bytes([UART_CMD_PROGRAM_SYNC, REC_SYN, rec_w & 0x0F, 0]))
        for s, d, c, e in conns:
            port.write(bytes([UART_CMD_SET_RECUR, s & 0x0F, d & 0x0F, (c & 0x0F) | ((e & 1) << 4)]))
    return conns


def _narma_loop(port, stop_ev, in_neurons, weight, density, rec_w, t_step=0.05, kmax=8):
    """Stream a continuous NARMA-like input (random u in [0,0.5]) into the input neurons with the
    cross-coupled recurrent net ON, so biases/recurrence can be tuned live on the raster. Emits a
    NARMALOOP marker each timestep (the vertical bar). Host-paced u; the array runs continuously."""
    _setup_reservoir(port, density, rec_w, weight)
    with _reservoir_lock:
        port.write(bytes([UART_CMD_RECUR_CTRL, 1]))
    emit(f"NARMAOK density={density} rec_w={rec_w} in={in_neurons}")
    rngstate = 12345
    while not stop_ev.is_set():
        rngstate = (1103515245 * rngstate + 12345) & 0x7FFFFFFF   # portable LCG (no Math.random ban)
        u = (rngstate / 0x7FFFFFFF) * 0.5                          # u in [0,0.5]
        n_in = int(round(u / 0.5 * kmax))
        with _reservoir_lock:
            for k in in_neurons:
                port.write(bytes([UART_CMD_SPIKE_SETUP, 0, k & 0x0F, 1]))
                for _ in range(n_in):
                    port.write(bytes([UART_CMD_SEND_SPIKE]))
        emit("NARMALOOP")                                         # raster marker (vertical bar)
        stop_ev.wait(t_step)
    with _reservoir_lock:
        port.write(bytes([UART_CMD_RECUR_CTRL, 0]))


def regenerate_wrec(per_neuron, path, inh_frac=0.2):
    """Regenerate the recurrent connectivity file at `per_neuron` connections/neuron. `inh_frac` is
    the fraction of recurrent connections that are INHIBITORY (e=0); the rest excitatory (e=1). The
    default 0.2 (80% exc) is excitation-dominated -> runaway gain; raise inh_frac toward 0.5-0.7 for
    a contractive echo-state reservoir."""
    import random
    rng = random.Random(3)
    lines = []
    for s in range(16):
        for d in rng.sample([x for x in range(16) if x != s], min(int(per_neuron), 15)):
            e = 0 if rng.random() < inh_frac else 1
            lines.append(f"{s} {d} 1 {e}")
    with open(path, "w") as f:
        f.write(f"# RECURLEVEL {per_neuron}/neuron; count=1, REC_SYN weight 15\n")
        f.write("\n".join(lines) + "\n")
    return len(lines)


def _reservoir_loop(port, stop_ev, wrec_path, delta_path, in_neurons, weight, recurrent=True):
    """Program the input synapse and continuously loop one delta trace into the input
    neurons (UP->exc syn0, DOWN->inh syn0). If recurrent, also program W_rec + the
    recurrent synapse and enable recurrence; if not, force recurrence OFF (feedforward,
    used by Stim-N bias tuning to match reservoir_run_randproj.py exactly)."""
    conns = []
    try:
        with open(_find_file(wrec_path)) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    s, d, c, e = (int(x) for x in line.split()[:4])
                    conns.append((s, d, c, e))
    except OSError:
        pass
    ev = []
    T = 2.0
    try:
        with open(_find_file(delta_path)) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#"):
                    if "T_ms=" in line:
                        T = float(line.split("T_ms=")[1].split()[0]) / 1000.0
                    continue
                if line:
                    t, ch = line.split()[:2]
                    ev.append((float(t) / 1000.0, int(ch)))
    except OSError:
        pass
    with _reservoir_lock:
        # input synapse (UP->exc syn0, DOWN->inh syn0) always programmed
        port.write(bytes([UART_CMD_PROGRAM_SYNC, 0, weight & 0x0F, 1]))
        port.write(bytes([UART_CMD_PROGRAM_SYNC, 0, weight & 0x0F, 0]))
        if recurrent:
            for syn, exc in ((REC_SYN, 1), (REC_SYN, 0)):
                port.write(bytes([UART_CMD_PROGRAM_SYNC, syn, weight & 0x0F, exc]))
            for s, d, c, e in conns:
                port.write(bytes([UART_CMD_SET_RECUR, s & 0x0F, d & 0x0F,
                                  (c & 0x0F) | ((e & 1) << 4)]))
            port.write(bytes([UART_CMD_RECUR_CTRL, 1]))
        else:
            port.write(bytes([UART_CMD_RECUR_CTRL, 0]))   # feedforward
    emit(f"RESERVOIROK recur={int(recurrent)} conns={len(conns) if recurrent else 0} "
         f"events={len(ev)} T={T:.2f}")
    while not stop_ev.is_set():
        t0 = time.time()
        emit("RESLOOP")   # marks the start of each ECG delta-trace loop
        for tf, ch in ev:
            if stop_ev.is_set():
                break
            while time.time() - t0 < tf and not stop_ev.is_set():
                time.sleep(0.001)
            exc = 1 if ch == 0 else 0
            with _reservoir_lock:
                for neu in in_neurons:
                    port.write(bytes([UART_CMD_SPIKE_SETUP, 0, neu & 0x0F, exc]))
                    port.write(bytes([UART_CMD_SEND_SPIKE]))
    with _reservoir_lock:
        port.write(bytes([UART_CMD_RECUR_CTRL, 0]))
    emit("RESERVOIRSTOPPED")
UART_DBG_PROGRAM_WEIGHT = 0x71
UART_DBG_SPIKE_SETUP = 0x72

# ---- Core clock -------------------------------------------------------------
# This SoC has NO software UART divider (0x20000000 is unmapped here; see
# firmware/defs.h:40). The LiteX divisor is fixed at synthesis, so the baud rate
# is welded to the core clock: baud = 960 * f_MHz. Timer0 also runs at f_MHz.
#
# The DLL reaches only 100/N MHz for N=2..7. 50 MHz (N=2) is the ceiling.
# The DLL does NOT survive a power cycle (the chip always powers up on the 10 MHz
# crystal), so we engage it here on every start. Flash is non-volatile, so once the
# 50 MHz image is flashed it persists across power cycles; only the DLL-engage below
# has to run each power-up, and it runs automatically whenever CLK_MHZ != 10.
# The firmware must be built to match:  make F_CPU_MHZ=50 hex
# DEFAULT 50 MHz: this is the operating point for the reservoir / vowel work, and all
# 16 neurons are live here (at 10 MHz only ~6 are). NOTE: synaptic efficacy is
# clock-dependent -- a GAIN shift, not a kill (the old "50 MHz kills inhibition" note
# was wrong; it works once biases are retuned per clock, confirmed at bench 2026-07-10,
# see the synapse-efficacy memory). The fast-readout features (SRAM drain, 21-bit
# timestamps, mask, drop/stall flags, REC, on-chip pacing) are clock-independent.
# Set CARAVAN_CLK_MHZ=10 to stay on the bare crystal (no DLL). This MUST match the
# firmware's F_CPU_MHZ or every pulse width and Timer0 constant is off by the ratio.
CLK_MHZ = int(os.environ.get("CARAVAN_CLK_MHZ", "50"))
BAUD = 960 * CLK_MHZ

# DLL registers (Caravel housekeeping SPI)
R_ENA, R_BYP, R_OUT, R_FB = 0x08, 0x09, 0x11, 0x12
FB_LOCK = 10          # loop never locks below ~90 MHz VCO; fb=4 silently kills the CPU
OUT_DIV = {50: 0x12, 33: 0x1B, 25: 0x24, 20: 0x2D}

# A spike is one 4-byte packet, 10 bits per byte on the wire (8N1). Above this
# aggregate rate the firmware's ring buffer cannot drain into the UART and spikes
# are dropped silently, so every rate the host reports becomes an UNDERESTIMATE.
SPIKE_LIMIT_HZ = BAUD // 40      # 1200 spk/s at 48000 baud (50 MHz core)

# One canonical ECG beat per class, delta-encoded (UP -> excitatory synapse,
# DOWN -> inhibitory). Looped by ECGRUN so biases can be tuned on the scope while
# the neuron sees the real stimulus. A V (PVC) beat carries proportionally more
# DOWN events, which is what used to silence neurons -- the effect being retuned.
ECG_BEAT_FILES = {"N": "ecg_beat_N.txt", "V": "ecg_beat_V.txt"}
ECG_T = 2.0        # presentation length (s), matches reservoir_run --tpresent
ECG_GAP = 0.3      # quiet gap between repeats, lets the membrane settle

_ecg_thread = None
_ecg_stop = threading.Event()

# Regular spike train (cmd 0xED) period bounds, in Timer0 ticks.
TRAIN_TICKS_MIN = 2000 * (CLK_MHZ // 10)   # 200 us -> 5 kHz max, any clock
TRAIN_TICKS_MAX = 0xFFFFFF                 # 24-bit wire format

# Bit 6 of packet byte 0: firmware's sticky "I overflowed the ring" flag.
SPIKE_DROP_FLAG = 0x40
# Bit 5: the flush waited on the UART FIFO -- the link is saturated and the array
# is being back-pressured. This fires BEFORE the ring ever overflows, so it, not
# DROPS, is the signal that the displayed rates stopped being free-running.
SPIKE_STALL_FLAG = 0x20

# Packet bytes 1..3 carry 7 bits each: a 21-bit Timer0 tick. Wrap = 2**21 ticks
# (209 ms at 10 MHz, 41.9 ms at 50 MHz), comfortably beyond the FTDI latency timer.
TS_BITS = 21
TS_WRAP = 1 << TS_BITS
TS_MASK = TS_WRAP - 1


class LockedPort:
    """Serialize access to the FTDI serial handle.

    pyftdi's port is backed by one USB handle and is NOT thread-safe. The spike
    reader thread sits in read() while the stdin thread issues write() for every
    command, and the writes were being lost -- e.g. the 0xEC neuron mask never
    reached the chip, though the identical bytes worked single-threaded.
    """

    def __init__(self, port):
        self._p = port
        self._lock = threading.Lock()

    def read(self, n):
        with self._lock:
            return self._p.read(n)

    def write(self, data):
        with self._lock:
            return self._p.write(data)

    def close(self):
        with self._lock:
            return self._p.close()


def engage_dll(mhz):
    """Lock the DLL to `mhz` and reset the CPU so firmware reboots on the new clock.

    Must run before the serial port is opened: the reset reboots the firmware,
    and the baud only matches once the core is actually at `mhz`.
    """
    from riscvprog import HKSPI

    out_reg = OUT_DIV[mhz]
    with HKSPI() as hk:
        w = lambda a, v: hk.slave.write([Config.CARAVEL_REG_WRITE, a, v])
        w(R_ENA, 0x00)          # disable while reprogramming
        w(R_FB, FB_LOCK)        # VCO ~100 MHz; anything less never locks
        w(R_OUT, out_reg)       # core = 100 / N
        w(R_ENA, 0x01)          # enable, keep DCO off
        time.sleep(0.4)         # let the loop lock
        w(R_BYP, 0x00)          # switch core off the crystal onto the DLL
        time.sleep(0.1)
        hk.cpu_reset_hold()
        time.sleep(0.15)
        hk.cpu_reset_release()
    time.sleep(1.5)             # firmware runs blink() before it streams

# Timer0 ticks at the CORE clock, so every seconds->ticks conversion below must use
# the real clock. These are sent to the chip already in ticks; the firmware cannot
# rescale them (it does not know what the host meant).
TIMER_HZ = CLK_MHZ * 1_000_000

# Poisson mean-ISI bounds, in ticks.
#   MIN scales: it is a real-time floor (200 us => 5 kHz max mean rate).
#   MAX does NOT scale: 5e6 is the firmware's OVERFLOW GUARD, keeping
#   umul32(mean, neglog_lut_max=799) inside 32 bits (5e6*799 = 3.995e9).
# So the slowest reachable mean rate is TIMER_HZ/5e6: 2 Hz at 10 MHz, 10 Hz at 50 MHz.
POISSON_MEAN_TICKS_MIN = 2000 * (CLK_MHZ // 10)     # 200 us -> 5 kHz, any clock
POISSON_MEAN_TICKS_MAX = 5_000_000                  # hard: firmware overflow guard
POISSON_HZ_MIN = TIMER_HZ / POISSON_MEAN_TICKS_MAX  # 2 Hz @10, 10 Hz @50
POISSON_HZ_MAX = TIMER_HZ / POISSON_MEAN_TICKS_MIN  # 5 kHz at any clock

# Coincidence dt: 300 ms of real time, matching the firmware clamp (which scales).
# 24-bit wire format caps at 16777215 ticks.
COINC_DT_TICKS_MAX = min(3_000_000 * (CLK_MHZ // 10), 0xFFFFFF)


def parse_weight_token(token):
    """Parse 4-bit weight token from decimal or binary text.

    Accepted forms:
      - binary with prefix: 0b1010
      - 4-bit binary text: 1010, 0001
      - decimal: 0..15
    """
    t = token.strip().lower()
    if not t:
        raise ValueError("empty weight token")

    if t.startswith("0b"):
        return int(t[2:], 2)

    # Preserve existing behavior for 4-bit binary strings, including leading zeros.
    if len(t) == 4 and all(c in "01" for c in t):
        return int(t, 2)

    # Otherwise treat as decimal.
    return int(t, 10)


def emit(line):
    """Write a line to the real stdout (pipe to OF app)."""
    _real_stdout.write(line + "\n")
    _real_stdout.flush()


def find_ftdi_base_url():
    """Discover the FTDI 4232H base URL."""
    s = StringIO()
    Ftdi.show_devices(out=s)
    devlist = s.getvalue().splitlines()[1:-1]
    for dev in devlist:
        url = dev.split("(")[0].strip()
        name = "(" + dev.split("(")[1]
        if name == "(Quad RS232-HS)":
            return url.rsplit("/", 1)[0]
    return None


def spike_reader_thread(port, stop_event):
    """Read 4-byte spike packets from UART and emit S lines.

    Binary protocol (mixed packet stream):
        Debug program packet:
            Byte 0: 0x71
            Byte 1: syn_addr (0..15)
            Byte 2: weight (0..15)
            Byte 3: exc (0/1)
        Debug spike-setup packet:
            Byte 0: 0x72
            Byte 1: syn_addr (0..15)
            Byte 2: neu_addr (0..15)
            Byte 3: exc (0/1)
        Spike packet (self-synchronizing):
        Byte 0: 0x80 | (neuron_id & 0x0F)   — MSB=1 marks packet start
        Byte 1: (ts16 >> 14) & 0x7F          — MSB=0; ts16 = raw 16-bit Timer0 tick
        Byte 2: (ts16 >>  7) & 0x7F          — MSB=0  (10 MHz down-counter snapshot)
        Byte 3: (ts16      ) & 0x7F          — MSB=0  host converts ticks->us
    """
    # Timestamps: use BOTH clocks, each where it is right.
    #
    # The chip's tick (bytes 1..3, 7 bits each = 21 bits) is captured inside
    # aer_drain at tick resolution -- the true spike time. It wraps every 2**21
    # ticks: 209 ms at 10 MHz, 41.9 ms at 50 MHz.
    #
    # It used to be 16 bits, which wraps every 1.31 ms at 50 MHz -- SHORTER than
    # the FTDI's 16 ms latency timer. Packets arriving in one read() share a
    # host timestamp, so the host gap read 0 and no wraps were added: the
    # intervals aliased (a 120 spk/s train reconstructed as a 599 us burst).
    # 21 bits puts the wrap well beyond USB latency, so the host resolves it.
    #
    # The host clock alone is useless for fine structure (a whole batch shares one
    # time.monotonic(), collapsing a synfire chain into one raster column), so:
    # take the inter-spike DELTA from the chip tick, and use the host ONLY to
    # count whole wraps across long gaps.
    t0 = time.monotonic()
    abs_ticks = 0        # running spike time, in Timer0 ticks
    prev_ts = None       # previous 16-bit tick snapshot
    prev_host = None     # host time when the previous packet was parsed
    buf = bytearray()
    spike_count = 0
    drops = 0            # packets carrying the firmware's overflow flag, per RATE window
    stalls = 0           # packets flagged 'flush waited on the UART FIFO'
    last_rate_time = time.time()

    while not stop_event.is_set():
        data = port.read(256)
        if data:
            buf.extend(data)
        else:
            time.sleep(0.01)
            # fall through: RATE must still be emitted, otherwise a silent chip
            # leaves the GUI displaying the last nonzero rate forever.

        while len(buf) >= 4:
            # Debug packet: program_neuron_weight arguments
            if buf[0] == UART_DBG_PROGRAM_WEIGHT:
                syn_addr = buf[1] & 0x0F
                weight = buf[2] & 0x0F
                synapse_type = "exc" if (buf[3] & 0x01) else "nexc"
                emit(f"DBG_P {syn_addr} {weight} {synapse_type}")
                del buf[:4]
                continue

            # Debug packet: spikesetup arguments
            if buf[0] == UART_DBG_SPIKE_SETUP:
                syn_addr = buf[1] & 0x0F
                neu_addr = buf[2] & 0x0F
                synapse_type = "exc" if (buf[3] & 0x01) else "nexc"
                emit(f"DBG_S {syn_addr} {neu_addr} {synapse_type}")
                del buf[:4]
                continue

            # Scan for sync byte (MSB=1)
            if buf[0] & 0x80 == 0:
                buf.pop(0)  # discard, resync
                continue
            # Verify next 3 bytes are data (MSB=0)
            if any(b & 0x80 for b in buf[1:4]):
                buf.pop(0)  # false start, resync
                continue

            neuron = buf[0] & 0x0F
            # Bit 6 is the firmware's sticky overflow flag: spikes were lost before
            # this packet. We cannot infer that from the observed rate, because
            # dropping is what keeps the observed rate under the link ceiling.
            if buf[0] & SPIKE_DROP_FLAG:
                drops += 1
            if buf[0] & SPIKE_STALL_FLAG:
                stalls += 1
            ts = ((buf[1] << 14) | (buf[2] << 7) | buf[3]) & TS_MASK
            del buf[:4]

            now = time.monotonic()
            if prev_ts is None:
                abs_ticks = 0
            else:
                # Timer0 is a DOWN counter: an earlier spike has a HIGHER value.
                d = (prev_ts - ts) & TS_MASK         # exact, but modulo 2**21
                # How many whole wraps fit in the host-measured gap? Packets that
                # arrived in the same read() share `now`, giving gap 0 -> wraps 0,
                # which is right: they are less than one wrap apart by construction.
                gap_ticks = max(0.0, (now - prev_host) * TIMER_HZ)
                wraps = int(round((gap_ticks - d) / float(TS_WRAP)))
                abs_ticks += d + max(0, wraps) * TS_WRAP
            prev_ts, prev_host = ts, now
            abs_us = abs_ticks // CLK_MHZ              # ticks -> us

            emit(f"S {neuron} {abs_us}")
            spike_count += 1
            if 0 <= neuron <= 15:
                _live_counts[neuron] += 1        # live counter for XORPROBE

            if _rec_file is not None:
                with _rec_lock:
                    if _rec_file is not None:
                        _rec_file.write(f"{neuron}\t{abs_ticks}\t{abs_us}\n")
                        globals()["_rec_count"] += 1

        # Periodic rate report
        now = time.time()
        if now - last_rate_time >= 1.0:
            elapsed = now - last_rate_time
            rate = spike_count / elapsed if elapsed > 0 else 0
            emit(f"RATE {rate:.1f}")
            # Nonzero => the chip lost spikes in this window, so RATE is too low.
            emit(f"DROPS {drops}")
            # Stalls fire before any ring overflow: the link is the bottleneck
            # and the array is throttled, so these rates are not free-running.
            emit(f"STALLS {stalls}")
            spike_count = 0
            drops = 0
            stalls = 0
            last_rate_time = now


def main():
    # Find FTDI device
    base_url = find_ftdi_base_url()
    if not base_url:
        print("Error: No FTDI 4232H device found!", file=sys.stderr)
        emit("ERROR No FTDI 4232H device found")
        return 1

    print(f"FTDI device: {base_url}", file=sys.stderr)

    # Initialize DAC controller for bias programming on channels B/C.
    dac = AD5664RBitBang(
        Config.FTDI_B,
        Config.FTDI_C,
        frequency=200_000,
        cs_active_low=True,
    )

    # Raise the core clock BEFORE opening the port: engaging the DLL resets the
    # CPU, and the baud below is only correct once the core is at CLK_MHZ.
    if CLK_MHZ != 10:
        if CLK_MHZ not in OUT_DIV:
            print(f"Error: core clock {CLK_MHZ} MHz not reachable (100/N, N=2..7)",
                  file=sys.stderr)
            return 1
        print(f"Engaging DLL: core {CLK_MHZ} MHz, baud {BAUD}", file=sys.stderr)
        engage_dll(CLK_MHZ)

    # Open channel D serial. Baud is welded to the core clock: 960 * f_MHz.
    serial_url = base_url + "/4"
    port = LockedPort(
        pyftdi.serialext.serial_for_url(serial_url, baudrate=BAUD, timeout=0))

    # Flush stale data
    port.read(4096)

    emit("READY")
    # Tell the GUI where the link saturates, so it can flag rates it cannot trust.
    emit(f"LIMIT {SPIKE_LIMIT_HZ}")

    # Start spike reader thread
    stop_event = threading.Event()
    reader = threading.Thread(
        target=spike_reader_thread, args=(port, stop_event), daemon=True
    )
    reader.start()

    # Main loop: read commands from stdin
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            parts = line.split()
            cmd = parts[0].upper()

            if cmd == "QUIT":
                break

            elif cmd == "B" and len(parts) == 3:
                name = parts[1]
                try:
                    voltage = float(parts[2])
                except ValueError:
                    continue

                match = BIAS_LOOKUP.get(name.lower())
                if match is None:
                    continue
                
                
                #nmos reference is 0.9 (VREFN) and pmos is 1.78 (VREFP)
                dac_id, addr, real_name = match
                if 0 <= voltage <= VREFN and real_name in ("vleakn", "vtaun", "vrefn", "JExcWn0", "JExcWn1", "JExcWn2", "JExcWn3"):
                    dac.set_dac_voltage(
                        dac_id=dac_id,
                        voltage=voltage,
                        vref=VREFN,
                        half_period_s=HALF_PERIOD_S,
                        address=addr,
                    )
                elif 0 <= voltage <= VREFP and real_name in ("vtaup", "vthrdp", "vepulseextp", "vipulseextp", "JInhWp0", "JInhWp1", "JInhWp2", "JInhWp3", "VREF", "VB1", "VB2", "TUNEp", "buffermonp", "lna_iref", "vthrdn", "ifdcp"):
                    # lna_iref (DAC3 ch A) is powered down to high-Z at startup by
                    # run_neuron_test.py.  Explicitly power it up (mode=00 = normal)
                    # before writing the voltage, because a plain write may not reliably
                    # wake the AD5664R depending on silicon revision.
                    if real_name == "lna_iref":
                        dac.power_down_dac(
                            dac_id=dac_id,
                            address=addr,
                            mode=0b00,
                            half_period_s=HALF_PERIOD_S,
                        )
                    dac.set_dac_voltage(
                        dac_id=dac_id,
                        voltage=voltage,
                        vref=VREFP,
                        half_period_s=HALF_PERIOD_S,
                        address=addr,
                    )
                    emit(f"BOK {real_name} {voltage:.4f}")

            elif cmd == "M" and len(parts) == 2:
                try:
                    neuron = int(parts[1])
                except ValueError:
                    continue

                if 0 <= neuron <= 15:
                    port.write(bytes([UART_CMD_MONITOR_SYNC, neuron]))
                    emit(f"MOK {neuron}")
                    
            elif cmd == "P" and len(parts) == 4:
                try:
                    syn_addr = int(parts[1])
                    weight = parse_weight_token(parts[2])
                    synapse_type = parts[3].lower()
                except ValueError:
                    emit("ERROR Invalid P command format")
                    continue

                if not (0 <= syn_addr <= 15 and 0 <= weight <= 15 and synapse_type in ("exc", "nexc")):
                    emit("ERROR P args out of range")
                    continue

                synapse_bit = 1 if synapse_type == "exc" else 0
                # Framed command: sync, syn_addr, weight, synapse_type
                port.write(bytes([UART_CMD_PROGRAM_SYNC, syn_addr, weight, synapse_bit]))
                emit(f"POK {syn_addr} {weight} {synapse_type}")

            elif cmd == "S" and len(parts) == 4:
                # Framed command: sync, syn_addr, neu_addr, synapse_type
                try:
                    syn_addr = int(parts[1])
                    neu_addr = int(parts[2])
                    synapse_type = parts[3].lower()
                except ValueError:
                    emit("ERROR Invalid S command format")
                    continue

                if not (
                    0 <= syn_addr <= 15
                    and 0 <= neu_addr <= 15
                    and synapse_type in ("exc", "nexc")
                ):
                    emit("ERROR S args out of range")
                    continue

                synapse_bit = 1 if synapse_type == "exc" else 0
                port.write(bytes([UART_CMD_SPIKE_SETUP, syn_addr, neu_addr, synapse_bit]))
                emit(f"SOK {syn_addr} {neu_addr} {synapse_type}")

            elif cmd == "T" and len(parts) in (1, 2):
                # Send one or more explicit spike triggers using the current route.
                try:
                    count = int(parts[1]) if len(parts) == 2 else 1
                except ValueError:
                    emit("ERROR Invalid T command format")
                    continue

                if count <= 0:
                    emit("ERROR T count must be > 0")
                    continue

                port.write(bytes([UART_CMD_SEND_SPIKE]) * count)
                emit(f"TOK {count}")

            elif cmd == "POISSON" and len(parts) == 2:
                # Start on-chip Poisson generation at mean rate <hz> on the staged
                # route. Host computes the mean ISI in CORE-CLOCK ticks (TIMER_HZ)
                # and sends it as four 6-bit bytes (each < 0x40) so no data byte
                # collides with a command sync byte.
                try:
                    hz = float(parts[1])
                except ValueError:
                    emit("ERROR Invalid POISSON rate")
                    continue
                mean_ticks = int(round(TIMER_HZ / hz)) if hz > 0 else POISSON_MEAN_TICKS_MAX
                # Say so when the request is out of range. Silently clamping a rate is
                # exactly how a 5x tick-scaling error hides inside a plausible number.
                if hz > 0 and not (POISSON_HZ_MIN <= hz <= POISSON_HZ_MAX):
                    emit(f"WARN POISSON {hz:.3f} Hz outside reachable "
                         f"[{POISSON_HZ_MIN:.3f}, {POISSON_HZ_MAX:.0f}] Hz "
                         f"at {CLK_MHZ} MHz; clamping")
                mean_ticks = max(POISSON_MEAN_TICKS_MIN, min(POISSON_MEAN_TICKS_MAX, mean_ticks))
                b3 = (mean_ticks >> 18) & 0x3F
                b2 = (mean_ticks >> 12) & 0x3F
                b1 = (mean_ticks >> 6) & 0x3F
                b0 = mean_ticks & 0x3F
                port.write(bytes([UART_CMD_POISSON_START, b3, b2, b1, b0]))
                emit(f"POISSONOK {hz:.2f} {mean_ticks}")

            elif cmd == "POISSONSTOP":
                port.write(bytes([UART_CMD_POISSON_STOP]))
                emit("POISSONSTOPOK")

            elif cmd == "TRIGRUN" and len(parts) == 2:
                # Regular spike train at <hz>, paced ON-CHIP by Timer0. The period is
                # sent as a 24-bit tick count in four 6-bit bytes (each < 0x40), so no
                # payload byte can collide with a command sync byte.
                try:
                    hz = float(parts[1])
                except ValueError:
                    emit("ERROR Invalid TRIGRUN rate")
                    continue
                if hz <= 0:
                    emit("ERROR TRIGRUN rate must be > 0")
                    continue
                ticks = int(round(TIMER_HZ / hz))
                if not (TRAIN_TICKS_MIN <= ticks <= TRAIN_TICKS_MAX):
                    lo = TIMER_HZ / TRAIN_TICKS_MAX
                    hi = TIMER_HZ / TRAIN_TICKS_MIN
                    emit(f"WARN TRIGRUN {hz:.3f} Hz outside reachable "
                         f"[{lo:.2f}, {hi:.0f}] Hz at {CLK_MHZ} MHz; clamping")
                    ticks = max(TRAIN_TICKS_MIN, min(TRAIN_TICKS_MAX, ticks))
                port.write(bytes([UART_CMD_TRAIN_START,
                                  (ticks >> 18) & 0x3F, (ticks >> 12) & 0x3F,
                                  (ticks >> 6) & 0x3F, ticks & 0x3F]))
                emit(f"TRIGRUNOK {TIMER_HZ / ticks:.2f} {ticks}")

            elif cmd == "RECORD":
                # Dump spikes to a text file: neuron_id, Timer0 ticks, microseconds.
                global _rec_file, _rec_count
                with _rec_lock:
                    if _rec_file is not None:
                        _rec_file.close(); _rec_file = None
                    if len(parts) == 2:
                        path = parts[1]
                    else:
                        # Recordings live in recordings/, not the repo root.
                        os.makedirs("recordings", exist_ok=True)
                        path = time.strftime("recordings/spikes_%Y%m%d_%H%M%S.txt")
                    try:
                        f = open(path, "w", buffering=1)
                    except OSError as e:
                        emit(f"ERROR RECORD cannot open {path}: {e}")
                        continue
                    f.write(f"# neuron_id\tticks\tmicroseconds\n")
                    f.write(f"# core_clock_MHz={CLK_MHZ} tick_ns={1000.0/CLK_MHZ:.1f} "
                            f"baud={BAUD} link_ceiling_spk_s={SPIKE_LIMIT_HZ}\n")
                    f.write("# time is the chip's Timer0 tick captured in aer_drain,\n")
                    f.write("# wrap-corrected with the host clock; t=0 at the first spike.\n")
                    _rec_file = f
                    _rec_count = 0
                emit(f"RECORDOK {os.path.abspath(path)}")

            elif cmd == "RECORDSTOP":
                with _rec_lock:
                    n = _rec_count
                    if _rec_file is not None:
                        path = _rec_file.name
                        _rec_file.close(); _rec_file = None
                        emit(f"RECORDSTOPOK {n} spikes -> {os.path.abspath(path)}")
                    else:
                        emit("RECORDSTOPOK 0 spikes (not recording)")

            elif cmd == "ECGRUN" and len(parts) in (2, 3, 4):
                # Loop one canonical ECG beat (N or V) into a neuron, for bias tuning.
                global _ecg_thread
                cls = parts[1].upper()
                if cls not in ECG_BEAT_FILES:
                    emit(f"ERROR ECGRUN class must be N or V, got {parts[1]}")
                    continue
                neuron = int(parts[2]) & 0x0F if len(parts) >= 3 else 0
                weight = int(parts[3]) & 0x0F if len(parts) == 4 else 15
                if _ecg_thread is not None and _ecg_thread.is_alive():
                    _ecg_stop.set(); _ecg_thread.join(timeout=1.0)
                try:
                    events = _load_ecg(cls)
                except OSError as e:
                    emit(f"ERROR ECGRUN cannot read beat file: {e}")
                    continue
                _ecg_stop.clear()
                _ecg_thread = threading.Thread(target=_ecg_loop,
                                               args=(port, _ecg_stop, events, neuron, weight),
                                               daemon=True)
                _ecg_thread.start()
                emit(f"ECGRUNOK {cls} neuron={neuron} weight={weight} "
                     f"events={len(events)} T={ECG_T}s")

            elif cmd == "ECGSTOP":
                if _ecg_thread is not None and _ecg_thread.is_alive():
                    _ecg_stop.set(); _ecg_thread.join(timeout=1.0)
                emit("ECGSTOPOK")

            elif cmd == "TRIGSTOP":
                port.write(bytes([UART_CMD_POISSON_STOP]))   # 0xE3 stops both trains
                emit("TRIGSTOPOK")

            elif cmd == "MASK" and len(parts) == 2:
                # Which neurons the chip should stream. Bit i set = stream neuron i.
                # Masked neurons are still acked on-chip, so array dynamics are
                # unchanged; they simply cost no ring slot and no UART bandwidth.
                try:
                    mask = int(parts[1], 0) & 0xFFFF
                except ValueError:
                    emit("ERROR Invalid MASK value")
                    continue
                port.write(bytes([UART_CMD_SPIKE_MASK,
                                  mask & 0x3F,
                                  (mask >> 6) & 0x3F,
                                  (mask >> 12) & 0x0F]))
                emit(f"MASKOK {mask}")

            elif cmd == "SYNFIRE" and len(parts) == 2:
                # Start synfire chain for <laps> rounds.
                try:
                    laps = int(parts[1])
                except ValueError:
                    emit("ERROR Invalid SYNFIRE laps")
                    continue
                laps = max(0, min(63, laps))  # 6-bit, 0 = 5 default in firmware
                port.write(bytes([UART_CMD_SYNFIRE_START, laps & 0x3F]))
                emit(f"SYNFIREOK {laps}")

            elif cmd == "SYNFIRESTOP":
                port.write(bytes([UART_CMD_SYNFIRE_STOP]))
                emit("SYNFIRESTOPOK")

            elif cmd == "RESERVOIR":
                global _reservoir_thread, _reservoir_stop
                if _reservoir_thread is not None and _reservoir_thread.is_alive():
                    emit("RESERVOIR already running")
                else:
                    _reservoir_stop = threading.Event()
                    _reservoir_thread = threading.Thread(
                        target=_reservoir_loop,
                        args=(port, _reservoir_stop, RESERVOIR_WREC, RESERVOIR_DELTA,
                              RESERVOIR_IN_NEURONS, 15),
                        daemon=True)
                    _reservoir_thread.start()

            elif cmd == "RESERVOIRSTOP":
                _reservoir_stop.set()
                emit("RESERVOIRSTOPOK")

            elif cmd == "SETRECUR" and len(parts) == 5:
                s, d, c, e = (int(parts[i]) for i in range(1, 5))
                with _reservoir_lock:
                    port.write(bytes([UART_CMD_SET_RECUR, s & 0x0F, d & 0x0F,
                                      (c & 0x0F) | ((e & 1) << 4)]))
                emit("SETRECUROK")

            elif cmd == "RECURCTRL" and len(parts) == 2:
                with _reservoir_lock:
                    port.write(bytes([UART_CMD_RECUR_CTRL, int(parts[1]) & 0x0F]))
                emit("RECURCTRLOK")

            elif cmd == "STIMNEURON" and len(parts) == 2:
                # load neuron id's per-neuron bias, then loop the ECG delta to it only
                try:
                    nid = int(parts[1]) & 0x0F
                except ValueError:
                    emit("ERROR STIMNEURON id"); continue
                import json
                try:
                    # prefer structured-tuned, then projection-tuned, then base
                    bp = None
                    for suf in ("_struct", "_feedproj", ""):
                        cand = _find_file(f"bias_synapse_characterization_super_n{nid}{suf}.biases")
                        if os.path.exists(cand):
                            bp = cand; tuned = suf.lstrip("_") or "base"; break
                    apply_bias_dict(dac, json.load(open(bp)))
                    emit(f"STIMNEURON bias n{nid} loaded ({tuned})")
                except Exception as e:
                    emit(f"STIMNEURON bias err {e}")
                if _reservoir_thread is not None and _reservoir_thread.is_alive():
                    _reservoir_stop.set(); _reservoir_thread.join(timeout=2)
                _reservoir_stop = threading.Event()
                # feedforward (recurrence OFF) with neuron nid's OWN projected delta
                # trace -> matches reservoir_run_randproj.py for on-scope bias tuning
                proj_delta = f"reservoir_delta_proj_n{nid}.txt"
                if not os.path.exists(_find_file(proj_delta)):
                    proj_delta = RESERVOIR_DELTA          # fallback to shared trace
                    emit(f"STIMNEURON WARN no per-neuron proj trace, using shared delta")
                _reservoir_thread = threading.Thread(
                    target=_reservoir_loop,
                    args=(port, _reservoir_stop, RESERVOIR_WREC, proj_delta, [nid], 15),
                    kwargs={"recurrent": False},
                    daemon=True)
                _reservoir_thread.start()
                emit(f"STIMNEURONOK {nid} feedforward proj")

            elif cmd == "RECURLEVEL" and len(parts) == 2:
                try:
                    per = float(parts[1])
                except ValueError:
                    emit("ERROR RECURLEVEL"); continue
                n = regenerate_wrec(per, _find_file(RESERVOIR_WREC))
                emit(f"RECURLEVELOK {per} {n}conns")

            elif cmd == "COINC" and len(parts) == 5:
                # One coincidence trial: input on synA then synB (both -> neuron),
                # separated by dt_us. dt is sent as a 24-bit tick count at the CORE
                # clock (TIMER_HZ) in four 6-bit bytes, so no data byte collides with
                # a command sync byte.
                try:
                    syn_a = int(parts[1]) & 0x0F
                    syn_b = int(parts[2]) & 0x0F
                    neuron = int(parts[3]) & 0x0F
                    dt_us = float(parts[4])
                except ValueError:
                    emit("ERROR Invalid COINC args")
                    continue
                dt_ticks = int(round(dt_us * (TIMER_HZ / 1_000_000.0)))   # us -> ticks at CLK_MHZ
                if dt_ticks > COINC_DT_TICKS_MAX:
                    emit(f"WARN COINC dt {dt_us:.1f} us exceeds 300 ms at "
                         f"{CLK_MHZ} MHz; clamping")
                dt_ticks = max(0, min(COINC_DT_TICKS_MAX, dt_ticks))  # firmware clamps to match
                d3 = (dt_ticks >> 18) & 0x3F
                d2 = (dt_ticks >> 12) & 0x3F
                d1 = (dt_ticks >> 6) & 0x3F
                d0 = dt_ticks & 0x3F
                port.write(bytes([UART_CMD_COINCIDENCE, syn_a, syn_b, neuron, d3, d2, d1, d0]))
                emit(f"COINCOK {syn_a} {syn_b} {neuron} {dt_us:.1f}")

            elif cmd == "XORPROBE" and len(parts) >= 2:
                # Present the 4 XOR corners (a-exc/b-inh detector) to `neuron` and report the
                # mean output-spike count per corner. A clean (1,0) detector fires on 10 and is
                # inhibited on 11 -> the GUI turns green when 10 is high and 11 is suppressed.
                try:
                    neuron = int(parts[1]) & 0x0F
                    weight = int(parts[2]) if len(parts) > 2 else 15
                    burst  = int(parts[3]) if len(parts) > 3 else 10
                    reps   = int(parts[4]) if len(parts) > 4 else 4
                    chan_a = int(parts[5]) if len(parts) > 5 else 0   # 0=exc, 1=inh
                    chan_b = int(parts[6]) if len(parts) > 6 else 1
                except ValueError:
                    emit("ERROR Invalid XORPROBE args")
                    continue
                exc_a = 1 if chan_a == 0 else 0
                exc_b = 1 if chan_b == 0 else 0
                with _reservoir_lock:
                    port.write(bytes([UART_CMD_PROGRAM_SYNC, 0, weight & 0x0F, 1]))   # exc syn0
                    port.write(bytes([UART_CMD_PROGRAM_SYNC, 0, weight & 0x0F, 0]))   # inh syn0
                means = {}
                for (a, b) in ((0, 0), (0, 1), (1, 0), (1, 1)):
                    acc = 0
                    for _ in range(reps):
                        c0 = _live_counts[neuron]
                        with _reservoir_lock:
                            for _i in range(burst):          # interleave a/b -> coincident
                                if a:
                                    port.write(bytes([UART_CMD_SPIKE_SETUP, 0, neuron, exc_a]))
                                    port.write(bytes([UART_CMD_SEND_SPIKE]))
                                if b:
                                    port.write(bytes([UART_CMD_SPIKE_SETUP, 0, neuron, exc_b]))
                                    port.write(bytes([UART_CMD_SEND_SPIKE]))
                        time.sleep(0.25)                     # window for firing + readout
                        acc += (_live_counts[neuron] - c0)
                        time.sleep(0.08)                     # settle between reps
                    means[(a, b)] = acc / max(1, reps)
                emit(f"XORRESULT {neuron} {means[(0,0)]:.2f} {means[(0,1)]:.2f} "
                     f"{means[(1,0)]:.2f} {means[(1,1)]:.2f}")

            elif cmd == "TXORPROBE" and len(parts) >= 2:
                # Recurrence memory-persistence probe (tuning aid for T-XOR). Fire a bit-a burst
                # (early) into `neuron`, then read the LATE window (no further input): does the
                # early bit's trace PERSIST? Measure {no-input, a-input} x {RECURCTRL off, on}
                # with a self-loop (SETRECUR neuron->neuron). Memory works when persistence
                # appears ONLY with recurrence (rec_a high), is input-gated (rec_0 low), and the
                # feedforward case decays (ff_a low). Emits: TXORRESULT ff_0 ff_a rec_0 rec_a.
                try:
                    neuron   = int(parts[1]) & 0x0F
                    in_w     = int(parts[2]) if len(parts) > 2 else 15
                    rec_w    = int(parts[3]) if len(parts) > 3 else 8
                    rec_cnt  = int(parts[4]) if len(parts) > 4 else 2
                    burst    = int(parts[5]) if len(parts) > 5 else 12
                except ValueError:
                    emit("ERROR Invalid TXORPROBE args")
                    continue
                reps, gap_s, late_s = 4, 0.12, 0.40
                with _reservoir_lock:
                    port.write(bytes([UART_CMD_PROGRAM_SYNC, 0, in_w & 0x0F, 1]))          # exc input
                    port.write(bytes([UART_CMD_PROGRAM_SYNC, REC_SYN, rec_w & 0x0F, 1]))   # recurrent syn
                    port.write(bytes([UART_CMD_SET_RECUR, neuron, neuron, (rec_cnt & 0x0F) | (1 << 4)]))
                res = {}
                for cond, rc in (("ff", 0), ("rec", 1)):
                    for lab, a in (("0", 0), ("a", 1)):
                        acc = 0.0
                        for _ in range(reps):
                            with _reservoir_lock:
                                port.write(bytes([UART_CMD_RECUR_CTRL, 0]))   # clear reverberation
                            time.sleep(0.25)
                            with _reservoir_lock:
                                port.write(bytes([UART_CMD_RECUR_CTRL, rc]))  # set the condition
                            time.sleep(0.05)
                            if a:
                                with _reservoir_lock:
                                    for _i in range(burst):                    # early bit-a burst
                                        port.write(bytes([UART_CMD_SPIKE_SETUP, 0, neuron, 1]))
                                        port.write(bytes([UART_CMD_SEND_SPIKE]))
                            time.sleep(gap_s)                                  # early response settles
                            c0 = _live_counts[neuron]
                            time.sleep(late_s)                                 # LATE window: persistence
                            acc += (_live_counts[neuron] - c0)
                        res[(cond, lab)] = acc / reps
                with _reservoir_lock:
                    port.write(bytes([UART_CMD_RECUR_CTRL, 0]))
                emit(f"TXORRESULT {res[('ff','0')]:.2f} {res[('ff','a')]:.2f} "
                     f"{res[('rec','0')]:.2f} {res[('rec','a')]:.2f}")

            elif cmd == "NARMAPROBE" and len(parts) >= 1:
                global _narma_thread, _narma_stop
                # Cross-coupled reservoir MEMORY-CAPACITY probe (NARMA tuning aid). Drive a random
                # input u(t), read per-neuron per-timestep counts, and measure how well the state
                # linearly encodes u(t), u(t-1), u(t-2) (max per-neuron corr^2 per lag). GREEN needs
                # MC0 (input encoded) AND MC1 (memory) -> a real reservoir, not just reverberation.
                # Emits: NARMARESULT MC0 MC1 MC2 rate.
                try:
                    density = int(parts[1]) if len(parts) > 1 else 3
                    rec_w   = int(parts[2]) if len(parts) > 2 else 6
                    in_w    = int(parts[3]) if len(parts) > 3 else 15
                except ValueError:
                    emit("ERROR Invalid NARMAPROBE args")
                    continue
                in_neurons = list(range(8)); M = 90; tstep = 0.1; kmax = 8
                if _narma_thread is not None and _narma_thread.is_alive():
                    _narma_stop.set(); time.sleep(0.15)     # stop the stream -> clean measurement
                port.write(bytes([UART_CMD_SPIKE_MASK, 0x3F, 0x3F, 0x0F]))   # unmask all
                _setup_reservoir(port, density, rec_w, in_w)
                with _reservoir_lock:
                    port.write(bytes([UART_CMD_RECUR_CTRL, 1]))
                time.sleep(0.3)
                u_seq = []; state = []; lcg = 98765
                for _t in range(M):
                    lcg = (1103515245 * lcg + 12345) & 0x7FFFFFFF
                    uu = (lcg / 0x7FFFFFFF) * 0.5
                    u_seq.append(uu); n_in = int(round(uu / 0.5 * kmax))
                    snap = list(_live_counts)
                    with _reservoir_lock:
                        for k in in_neurons:
                            port.write(bytes([UART_CMD_SPIKE_SETUP, 0, k, 1]))
                            for _ in range(n_in):
                                port.write(bytes([UART_CMD_SEND_SPIKE]))
                    time.sleep(tstep)
                    state.append([_live_counts[k] - snap[k] for k in range(16)])
                with _reservoir_lock:
                    port.write(bytes([UART_CMD_RECUR_CTRL, 0]))

                def _corr(xs, ys):
                    n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
                    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                    sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys)
                    return (sxy / (sxx * syy) ** 0.5) if sxx > 1e-9 and syy > 1e-9 else 0.0

                half = M // 2                            # steady state (after any accumulation),
                mcs = []                                  # so a reservoir that saturates over the
                for lag in (0, 1, 2):                     # stream scores low here too (honest green)
                    best = 0.0
                    for k in range(16):
                        xs = [state[t][k] for t in range(max(lag, half), M)]
                        ys = [u_seq[t - lag] for t in range(max(lag, half), M)]
                        c = _corr(xs, ys); best = max(best, c * c)
                    mcs.append(best)
                rate = sum(sum(state[t]) for t in range(half, M)) / ((M - half) * tstep) / 16.0
                emit(f"NARMARESULT {mcs[0]:.3f} {mcs[1]:.3f} {mcs[2]:.3f} {rate:.1f}")

            elif cmd == "NARMACOLLECT" and len(parts) >= 1:
                # Probe-IDENTICAL long-stream collection (fixes the old narma_collect.py readout bug:
                # it parsed host-side out_q "S " lines over a 0.1s window, which backlogs and smears
                # per-step counts -> MC0~=0). Here we reuse the exact NARMAPROBE drive + _live_counts
                # snapshot readout, extended to M steps, so ff and rec see an IDENTICAL fixed-seed u.
                # Emits one "NARMAROW t u c0..c15" per step, then
                # "NARMACOLLECTDONE M mc0 mc1 mc2 rate" (MC on the 2nd half of the LONG stream, so
                # saturation the 90-step probe hides shows up here). Args:
                #   NARMACOLLECT <recur 0|1> <M> <tstep_ms> <kmax> <density> <rec_w> <in_w> <inh_pct>
                try:
                    recur   = int(parts[1]) if len(parts) > 1 else 1
                    M       = int(parts[2]) if len(parts) > 2 else 800
                    tstep   = (int(parts[3]) if len(parts) > 3 else 100) / 1000.0
                    kmax    = int(parts[4]) if len(parts) > 4 else 8
                    density = int(parts[5]) if len(parts) > 5 else 15
                    rec_w   = int(parts[6]) if len(parts) > 6 else 10
                    in_w    = int(parts[7]) if len(parts) > 7 else 15
                    inh_pct = int(parts[8]) if len(parts) > 8 else 20      # % of recurrent conns inhibitory
                except ValueError:
                    emit("ERROR Invalid NARMACOLLECT args")
                    continue
                in_neurons = list(range(8))
                if _narma_thread is not None and _narma_thread.is_alive():
                    _narma_stop.set(); time.sleep(0.15)     # stop any live stream -> clean measurement
                port.write(bytes([UART_CMD_SPIKE_MASK, 0x3F, 0x3F, 0x0F]))   # unmask all (as probe)
                _setup_reservoir(port, density, rec_w, in_w, inh_frac=inh_pct / 100.0)
                with _reservoir_lock:
                    port.write(bytes([UART_CMD_RECUR_CTRL, 1 if recur else 0]))
                time.sleep(0.3)
                u_seq = []; state = []; lcg = 98765     # SAME seed as NARMAPROBE -> ff and rec match u
                for _t in range(M):
                    lcg = (1103515245 * lcg + 12345) & 0x7FFFFFFF
                    uu = (lcg / 0x7FFFFFFF) * 0.5
                    u_seq.append(uu); n_in = int(round(uu / 0.5 * kmax))
                    snap = list(_live_counts)
                    with _reservoir_lock:
                        for k in in_neurons:
                            port.write(bytes([UART_CMD_SPIKE_SETUP, 0, k, 1]))
                            for _ in range(n_in):
                                port.write(bytes([UART_CMD_SEND_SPIKE]))
                    time.sleep(tstep)
                    row = [_live_counts[k] - snap[k] for k in range(16)]
                    state.append(row)
                    emit(f"NARMAROW {_t} {uu:.6f} " + " ".join(str(c) for c in row))
                with _reservoir_lock:
                    port.write(bytes([UART_CMD_RECUR_CTRL, 0]))

                def _corr(xs, ys):
                    n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
                    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                    sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys)
                    return (sxy / (sxx * syy) ** 0.5) if sxx > 1e-9 and syy > 1e-9 else 0.0

                half = M // 2
                mcs = []
                for lag in (0, 1, 2):
                    best = 0.0
                    for k in range(16):
                        xs = [state[t][k] for t in range(max(lag, half), M)]
                        ys = [u_seq[t - lag] for t in range(max(lag, half), M)]
                        c = _corr(xs, ys); best = max(best, c * c)
                    mcs.append(best)
                rate = sum(sum(state[t]) for t in range(half, M)) / ((M - half) * tstep) / 16.0
                emit(f"NARMACOLLECTDONE {M} {mcs[0]:.3f} {mcs[1]:.3f} {mcs[2]:.3f} {rate:.1f}")

            elif cmd == "NARMASETUP" and len(parts) >= 1:
                # Wire the cross-coupled reservoir (regenerate wrec at density, load SETRECUR,
                # program REC_SYN) WITHOUT streaming — for the collection runner, which drives the
                # input and reads per-timestep itself. RECURCTRL is left OFF (caller sets it).
                try:
                    density = int(parts[1]) if len(parts) > 1 else 3
                    rec_w   = int(parts[2]) if len(parts) > 2 else 6
                    in_w    = int(parts[3]) if len(parts) > 3 else 15
                except ValueError:
                    emit("ERROR Invalid NARMASETUP args")
                    continue
                _setup_reservoir(port, density, rec_w, in_w)
                with _reservoir_lock:
                    port.write(bytes([UART_CMD_RECUR_CTRL, 0]))
                emit(f"NARMASETUPOK dens={density} rec_w={rec_w}")

            elif cmd == "NARMASTREAM":
                try:
                    density = int(parts[1]) if len(parts) > 1 else 3
                    rec_w   = int(parts[2]) if len(parts) > 2 else 6
                except ValueError:
                    emit("ERROR Invalid NARMASTREAM args")
                    continue
                if _narma_thread is not None and _narma_thread.is_alive():
                    emit("NARMA already running")
                else:
                    port.write(bytes([UART_CMD_SPIKE_MASK, 0x3F, 0x3F, 0x0F]))   # see all
                    _narma_stop = threading.Event()
                    _narma_thread = threading.Thread(
                        target=_narma_loop,
                        args=(port, _narma_stop, list(range(8)), 15, density, rec_w),
                        daemon=True)
                    _narma_thread.start()

            elif cmd == "NARMASTOP":
                if _narma_thread is not None:
                    _narma_stop.set()
                emit("NARMASTOPOK")

            else:
                emit(f"ERROR Unknown command: {line}")

    except (KeyboardInterrupt, BrokenPipeError):
        pass

    # Clean shutdown
    stop_event.set()
    reader.join(timeout=2)
    port.close()
    dac.close()
    return 0


if __name__ == "__main__":
    # Re-open stdin as text (in case launched with binary pipes)
    sys.stdin = os.fdopen(0, "r")
    sys.exit(main())
