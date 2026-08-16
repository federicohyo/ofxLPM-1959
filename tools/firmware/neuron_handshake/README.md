# Pinned firmware for the 1959 exhibition

`neuron_handshake_25MHz_exhibition.hex` — **flash this before running the installation.**
Built `F_CPU_MHZ=25` from the `.c` snapshot beside it. sha256 prefix `1caf789f00ac01f3`.

    cd firmware/neuron_handshake && make clean hex F_CPU_MHZ=25   # to rebuild from source
    ./.venv-meas/bin/python3 run_neuron_test.py --clock 25        # flash + engage the DLL

The app itself flashes nothing (README §1), so whatever image is on the die is what the
installation runs. This directory exists so that is never in doubt.

## Why this is pinned, and what would break it

The installation's behaviour depends on how much charge each input spike injects, which
is `I_syn × pulse_width`. That width was **measured with Timer0 on 2026-08-15**, and it
is not what the firmware comment claims:

| pulse_mult | measured width | firmware comment |
|---|---|---|
| **1 (default)** | **29.6 µs** | 1.0 µs |
| 4 | 36.4 µs | 4.0 µs |
| 12 | 54.4 µs | 12.0 µs |
| 63 | 169.4 µs | 63.0 µs |

Structure is `width ≈ 27.4 µs + 2.25 µs × pulse_mult`: a large fixed term, which is
`delay_loop` running from XIP flash at ~131 cycles/instruction, plus a small
proportional one. So the real default pulse is **~30× longer than documented**.

A planned change makes `sendspike()` SRAM-resident (`.ramtext`) to remove that fixed
term, which would take the default pulse to roughly **0.7 µs** — about **40× less charge
per spike**. That is a large, audible change in how the array responds, and it is why
this image is pinned rather than tracking the main firmware tree.

**Do not replace this hex with a newer build without re-tuning the installation.**

## Provenance

Snapshot taken before the SRAM `sendspike` change. It includes the session's added
diagnostic commands (`BURST` 0xDD, `PULSEBENCH` 0xDE, `ARRAYBENCH` 0xEF,
`ARRAYOPT` 0xE8, `LIFRAM` 0xE7), none of which alter `sendspike`, the AER drain or
the main loop — they only add commands the installation never sends. Behaviour for the
exhibition is therefore identical to the image the piece was developed against.
