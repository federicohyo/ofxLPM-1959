# `1959` — the film and the die, in one window

In 1959 David Hubel and Torsten Wiesel lowered an electrode into the visual
cortex of a cat and moved a bar of light across its field of view. A single
neuron answered, and its spikes were audible as clicks on tape.

This app plays that recording into a fabricated SkyWater 130 nm die containing
sixteen analog spiking neurons. Every click the detector finds on the tape
becomes one spike delivered to **neuron 14** through its excitatory synapse.
The neuron's membrane comes back on the analog monitor pin, and that is the
picture on the left. The film is on the right. The visitor hears both: the
cat's cortex in one ear and the silicon in the other, sixty-seven years apart,
almost in unison.

One window, one thing to start.

```
   left half                                right half
   ┌──────────────────────────┐   ┌──────────────────────────┐
   │   the die's membrane,    │   │   the 1959 film, whose   │
   │   spiral + scope strip   │   │   soundtrack is driving  │
   │   (coro-pixhawk's        │   │   the die                │
   │    picture, verbatim)    │   │                          │
   └──────────────────────────┘   └──────────────────────────┘
      sound: left = the tape          sound: right = the die
```

## Running it

```bash
./prepare.sh                  # once: provision video.mp4 + audio.s16
make -j$(nproc)
cd bin && ./ofxLPM-1959
```

Before launching, in this order:

1. **Bring the chip up.** This app does not flash anything. Flash and clock the
   die with the chip's own bring-up first, and make sure `CARAVAN_CLK_MHZ`
   matches both the flashed image and the clock the biases were tuned at — synaptic efficacy is clock dependent, so a mismatch
   is a silent gain change, not an error.
2. **Free the FTDI.** `pkill -f "[n]euron_bridge.py"`, and close the GUI. Only
   one process may own it, and this app spawns its own bridge.
3. **Plug the ADC board in.** `/dev/ttyACM0` by default. The app reopens the
   port on its own if it is not there yet or goes away mid-run.

Once running it brings the die up by itself, in stages, and says so on screen
until the room is going:

```
biases (bias_n14_lpm_v1.biases)  →  monitor N14  →  PROG S14/S0 W15 exc
   →  ROUTE S0 → N14 exc  →  stimulation
```

It waits for `neuron_bridge.py` to say `READY` before it touches a DAC, and
`PROG` always precedes `ROUTE` — programming a weight re-runs `spikesetup` on
the chip and clobbers whatever route was staged.

Two synapses are programmed, one is fired: `chipProgSyn` (14) is the bench
sequence as it is written down, `chipSynapse` (0) is the one actually routed
and fired. Weighting only the first would fire an unweighted synapse and the
neuron would sit there silent.

## Keys

| | |
|---|---|
| `-` `=` | chip channel level, logged so it can be written back to `settings.json` |
| `↑` `↓` | scope gain |
| `←` `→` | membrane detector threshold (logs the resulting rate) |
| `,` `.` | film detector threshold (logs the resulting rate) |
| `[` `]` | spiral period |
| `V` | flip the scope trace |
| space | pause film and video; the die stops being stimulated |
| type `cal` | the full calibration overlay |
| type `quit` | exit (Escape is disabled) |

`settings.json` is re-read within a second of being saved, so most of it can be
set by ear or by eye with the piece running.

## The three paths

Three devices, three separate links:

| | |
|---|---|
| FTDI | `neuron_bridge.py` — UART commands out, AER spike stream back |
| `/dev/ttyACM0` | the monitor pin, sampled at **1 kHz** by a Pixhawk 2.4.8 (STM32F427) and streamed as `seconds,volts` over USB CDC |
| sound card | the tape out on the left, the ticks the measured resets strike on the right |

The board's rate is load bearing: it sets the spiral's timebase and the
detector's follower. The app measures the arriving line rate every five seconds
and logs `board confirmed at 1000 Hz` once, or warns every window while it
disagrees with `deviceRateHz` by more than 15%. The board firmware is
currently 1 kHz; earlier images were 400 Hz, and `traceAlphaQuiet` has to be
rescaled inversely with the rate (0.12 at 400 Hz, 0.048 at 1 kHz).

## Where the code comes from

This repository is self-contained on purpose — it runs on its own rather than
only on the bench it was written on — but most of it is not original to it, and
the originals are where it is maintained:

| | |
|---|---|
| `src/Detector.h`, `RingBuffer.h`, `Settings.h` | the installation's signal path, from `ofxLPM/coro/src`. The detector is not a reimplementation: it is the same code that drives the gallery piece next door. |
| `src/NeuronBridge.{h,cpp}` | the FTDI child process and its pipe protocol, from the chip viewer. |
| `tools/neuron_bridge.py`, `tools/firmware/riscvprog.py` | the driver itself — DLL, DACs, UART, AER. See [`tools/README.md`](tools/README.md). |

All are verbatim copies, so a `diff` against the originals shows only real
drift. `src/HubelDetector.h` is the one line that is not: a shim renaming the
detector's `SpikeEvent` to `HubelSpikeEvent`, because `NeuronBridge.h` has a
`SpikeEvent` of its own — the AER address stream.

Edit `OF_ROOT` in `config.make` if openFrameworks does not live where mine
does. `pip install pyftdi` for the chip side.

## Without the chip, or without the film

- `"chipEnabled": false` runs the picture and the sound with the die untouched:
  no bridge is spawned, no DAC is written, nothing takes the FTDI. For testing
  the projection and the speakers, and for leaving the bench free while you do.
- `"tickSource": "aer"` strikes the tick from the chip's own digital report of
  each spike instead of the analog reset, which keeps the right channel alive
  if the ADC board is not connected. `"both"` strikes on either.
- `bin/data/video.mp4` (25 MB) and `bin/data/audio.s16` (127 MB) are not in
  git. The film is somebody else's work and is not redistributed here; the PCM
  is derived from it and there is no reason to store it. Put your own copy of
  the film at `bin/data/video.mp4` and `./prepare.sh` decodes the soundtrack
  beside it. (On the bench it also finds the copies the sibling apps already
  have and links to those instead of making a third.)
