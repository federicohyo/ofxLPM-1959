# `tools/` — the FTDI driver, vendored

`neuron_bridge.py` is the single owner of the FT4232H. It engages the DLL to the
requested core clock, programs the 23 bias DACs channel by channel over a
bit-banged SPI, translates line commands into UART bytes for the RISC-V core,
and streams the AER spikes back. The app spawns it as a child process and talks
to it over a pipe; it never touches the FTDI itself.

`firmware/riscvprog.py` is the layer under it: `AD5664RBitBang` for the DACs,
`HKSPI` for the Caravel housekeeping SPI that carries the clock and the CPU
reset.

Both are **verbatim copies**, taken from the chip's own repository where they
are maintained. They are here so this repository runs on its own rather than
only on the bench it was written on. Nothing has been edited, so a `diff`
against the originals shows only real drift.

If you are working on that bench, set `bridgePath` in `bin/data/settings.json`
to the maintained copy instead of this one — two copies of a driver drift, and
the one that matters is the one the rest of the chip's tooling shares.

## What it needs

```bash
pip install pyftdi
./setup_ftdi_udev.sh    # from the chip repo, or add your own udev rule
```

The app never sends the ECG, reservoir, synfire or coincidence commands, so the
data files those load are not needed here.
