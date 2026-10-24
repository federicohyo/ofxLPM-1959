#!/usr/bin/env python3
"""Housekeeping-SPI / flash bond-pad diagnostic for the COB die.

Read-only. Never erases or programs the flash. Run it on a known-good die and on the
suspect die, then diff the two reports:

    python3 check_pads.py --label working
    python3 check_pads.py --label broken
    python3 check_pads.py --compare padcheck_working.json padcheck_broken.json

When the flash group fails, use --probe to find the single open pad. It keeps
issuing JEDEC reads so the four lines stay busy; clip a scope/meter on the mother
board and watch which one is dead:

    python3 check_pads.py --probe 20
    # then probe: die pad 24 -> J24.15 / J15.3 / U13.1 (CS)
    #             die pad 25 -> J24.13 / J15.5 / U13.6 (SCK)
    #             die pad 26 -> J24.11 / J15.6 / U13.5 (SI)
    #             die pad 27 -> J24.9  / J15.2 / U13.2 (SO)
    # CS/SCK/IO0 should always burst (the die drives them); the flat line is the
    # open bond. J15 is a 2.54mm header, the easiest place to clip.

What it can and cannot localise
-------------------------------
The only channel we have is the FTDI 4-wire SPI, so pads can only be tested in
groups, never one at a time:

  HK SPI link   -> die pads 32 (SDO), 33 (SDI), 34 (CSB), 35 (SCK)
                   Reading the fixed IDs exercises all four at once.
  Flash link    -> die pads 24 (flash_csb), 25 (flash_clk), 26 (flash_io0),
                   27 (flash_io1). Reached via the HK SPI "pass-through" mode.

If HK IDs read fine but JEDEC reads 0xffffff, the fault is in the flash group
(24-27) or the flash chip; if HK IDs read 0xffffff, the fault is in the HK group
(32-35) or the die is dead/unclocked/unpowered. Within a failing group the script
cannot say which single pad -- that needs a probe on the COB pad. But it cuts the
search from 8 pads to 4, and the good-vs-bad diff often makes the culprit obvious.
"""

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from riscvprog import HKSPI, Config  # noqa: E402

R_ENA, R_BYP, R_OUT, R_FB = 0x08, 0x09, 0x11, 0x12
R_RESET = 0x0B

# efabless manufacturer ID is fixed. The product ID is NOT: the stock Caravel
# harness is 0x10, but this die (ofxLPM-1959) reports 0x11. So only mfg gates.
MFG_OK = 0x0456

# Caravel power-on defaults (Table 9). Reported, not enforced: a clock already
# engaged will have changed 0x11/0x12, so a mismatch is not a failure.
DEFAULTS = {0x08: 0x02, 0x09: 0x01, 0x0A: 0x00, 0x0B: 0x00, 0x11: 0x12, 0x12: 0x04}

BRANDS = {
    0xC8: "GigaDevice GD25",
    0xEF: "Winbond W25Q",
    0x20: "Micron/ST",
    0x9D: "ISSI",
    0x01: "Spansion/Cypress",
    0x1F: "Atmel/Adesto",
    0xBF: "SST",
    0x89: "ESMT",
    0x0B: "XTX",
}


def hx(v):
    return None if v is None else f"{v:02x}"


def hk_read(hk, reg):
    return int.from_bytes(hk.slave.exchange([Config.CARAVEL_REG_READ, reg], 1), "big")


def hk_write(hk, reg, val):
    hk.slave.write([Config.CARAVEL_REG_WRITE, reg, val])


def read_hk(hk):
    out = {}
    mfg = hk.slave.exchange([Config.CARAVEL_STREAM_READ, 0x01], 2)
    out["mfg"] = int.from_bytes(mfg, "big")
    out["product"] = hk_read(hk, 0x03)
    proj = hk.slave.exchange([Config.CARAVEL_STREAM_READ, 0x04], 4)
    out["project_id"] = int.from_bytes(proj, "big")
    out["regs"] = {f"0x{a:02x}": hk_read(hk, a) for a in range(0x08, 0x13)}
    return out


def read_flash(hk):
    hk.slave.write([Config.CARAVEL_PASSTHRU, Config.CMD_RESET_CHIP])
    time.sleep(0.05)
    status = hk.slave.exchange([Config.CARAVEL_PASSTHRU, Config.CMD_READ_STATUS], 1)
    jedec = hk.slave.exchange([Config.CARAVEL_PASSTHRU, Config.CMD_JEDEC_DATA], 3)
    hk.slave.write([Config.CARAVEL_PASSTHRU, 0xAB])  # release from deep power-down
    time.sleep(0.05)
    uid = hk.slave.exchange([Config.CARAVEL_PASSTHRU, 0x4B, 0, 0, 0, 0], 8)
    j = int.from_bytes(jedec, "big")
    return {
        "status": int.from_bytes(status, "big"),
        "jedec": f"{j:06x}",
        "mfg_byte": jedec[0],
        "brand": BRANDS.get(jedec[0], f"unknown 0x{jedec[0]:02x}")
                 if jedec[0] not in (0x00, 0xFF) else None,
        "uid": uid.hex(),
    }


def write_roundtrip(hk):
    """Toggle the CPU-reset bit and read it back. This register is known to read
    back (cpu_reset_hold writes 0x0b=1 and we can read it). Confirms the write
    (SDI) path, not just reads. Leaves the CPU held in reset afterwards."""
    hk_write(hk, R_RESET, 0x01)
    hi = hk_read(hk, R_RESET)
    hk.cpu_reset_hold()
    return {"wrote_1_read": hi, "roundtrip": (hi & 1) == 1}


# Probe helpers: die pad -> where the same net is reachable on the mother board.
# (net names from mother_efabless; J15 is a 2.54mm 1x6 header, easiest to clip.)
PROBE_POINTS = {
    "24 flash_csb": "J24 pin15 / J15 pin3 / U13 pin1 (/CS)",
    "25 flash_clk": "J24 pin13 / J15 pin5 / U13 pin6 (SCK)",
    "26 flash_io0": "J24 pin11 / J15 pin6 / U13 pin5 (SI)",
    "27 flash_io1": "J24 pin9  / J15 pin2 / U13 pin2 (SO)",
}


def probe_lines(hk, seconds):
    """Keep issuing JEDEC reads so pads 24-27 (and their nets) toggle. Scope each
    line at J15/U13: the one that never moves is the open bond. Note CS/CLK/IO0
    should always burst (the die drives them); IO1 should burst too once the flash
    answers -- if IO1 alone is flat, pad 27 is open."""
    print(f"\nPROBE mode for {seconds:.0f}s -- scope these lines while reads repeat:")
    for p, where in PROBE_POINTS.items():
        print(f"    die pad {p:14s} -> {where}")
    print("  (J15 pin1=WP# and pin4=HOLD# must sit at ~3.3V; not needed to read the ID)")
    t_end = time.time() + seconds
    n = ok = 0
    try:
        while time.time() < t_end:
            j = int.from_bytes(
                hk.slave.exchange([Config.CARAVEL_PASSTHRU, Config.CMD_JEDEC_DATA], 3), "big")
            n += 1
            ok += 1 if j != 0xFFFFFF else 0
            time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    print(f"  {n} reads issued, {ok} got a JEDEC reply.")


def collect(url=None, probe=0):
    r = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "url": url}
    with HKSPI(ftdi_device=url) as hk:
        hk_write(hk, R_BYP, 0x01)
        hk_write(hk, R_ENA, 0x02)
        hk_write(hk, R_OUT, 0x12)
        hk_write(hk, R_FB, 0x04)
        hk.cpu_reset_hold()
        time.sleep(0.1)

        print("Reading housekeeping registers (pads 32/33/34/35) ...")
        r.update(read_hk(hk))
        print(f"  mfg     = 0x{r['mfg']:04x}   (want 0x{MFG_OK:04x})")
        print(f"  product = 0x{r['product']:02x}")
        print(f"  proj id = 0x{r['project_id']:08x}")
        for k, v in r["regs"].items():
            d = DEFAULTS.get(int(k, 16))
            tag = "" if d is None or v == d else f"   (default 0x{d:02x})"
            print(f"  reg {k} = 0x{v:02x}{tag}")

        print("Reading flash via pass-through (pads 24/25/26/27) ...")
        fl = read_flash(hk)
        r["flash"] = fl
        print(f"  status  = 0x{fl['status']:02x}")
        print(f"  JEDEC   = {fl['jedec']}   {fl['brand'] or '(no flash response)'}")
        print(f"  UID     = {fl['uid']}")

        print("Write/readback test (CPU reset register) ...")
        r["roundtrip"] = write_roundtrip(hk)
        print(f"  wrote 1 -> read 0x{r['roundtrip']['wrote_1_read']:02x}   "
              f"roundtrip={r['roundtrip']['roundtrip']}")

        if probe:
            probe_lines(hk, probe)

        hk.cpu_reset_release()

    r["hk_ok"] = r["mfg"] == MFG_OK
    r["flash_ok"] = flash_ok_of(r)
    return r


def mfg_byte_of(r):
    b = r["flash"].get("mfg_byte")
    if b is None:  # reports written before mfg_byte existed
        j = r["flash"].get("jedec")
        b = int(j[:2], 16) if j else 0xFF
    return b


def flash_ok_of(r):
    # A real NOR flash manufacturer byte is never all-zero or all-one.
    return mfg_byte_of(r) not in (0x00, 0xFF)


def show_verdict(r):
    print("\nVERDICT")
    if r["hk_ok"] and r["flash_ok"]:
        print("  HK SPI and flash both respond. Bond pads 24-35 look connected.")
    elif r["hk_ok"] and not r["flash_ok"]:
        b = mfg_byte_of(r)
        if b == 0xFF:
            print("  HK SPI responds, flash does NOT. Data line idles HIGH (ffffff):")
            print("  nothing is driving it -> flash not selected, or a data pad is open.")
            print("  -> suspect die pads 24 (flash_csb), 25 (flash_clk),")
            print("     26 (flash_io0), 27 (flash_io1), or the flash / its pull-ups.")
        elif b == 0x00:
            print("  HK SPI responds, flash does NOT. Data line is held LOW (000000):")
            print("  unlike ffffff this is NOT a floating line -- something drives it low.")
            print("  -> likely die damage/leak (pass-through or pad 27), a shorted bond,")
            print("     or an unpowered flash clamping its SO pin. Re-check pad 27 first.")
        else:
            print("  HK SPI responds, flash does NOT (JEDEC "
                  f"{r['flash']['jedec']} is not a known ID).")
            print("  -> suspect die pads 24-27 or the flash itself.")
    elif not r["hk_ok"]:
        print("  HK SPI does not respond (all-ones or garbage).")
        print("  -> suspect die pads 32 (SDO), 33 (SDI), 34 (CSB), 35 (SCK),")
        print("     or the die is unclocked/unpowered (pads 21 resetb, 22 clock, power/GND).")


def compare(a, b):
    for r in (a, b):
        r["hk_ok"] = r["mfg"] == MFG_OK
        r["flash_ok"] = flash_ok_of(r)
    print("=" * 60)
    print(f"  working : {a.get('label', '?')}   {a['time']}")
    print(f"  suspect : {b.get('label', '?')}   {b['time']}")
    print("=" * 60)
    rows = [
        ("HK SPI ok", lambda r: str(r["hk_ok"])),
        ("flash ok", lambda r: str(r["flash_ok"])),
        ("mfg", lambda r: f"0x{r['mfg']:04x}"),
        ("product", lambda r: f"0x{r['product']:02x}"),
        ("proj id", lambda r: f"0x{r['project_id']:08x}"),
        ("flash status", lambda r: f"0x{r['flash']['status']:02x}"),
        ("flash JEDEC", lambda r: r["flash"]["jedec"]),
        ("flash brand", lambda r: str(r["flash"]["brand"])),
        ("write roundtrip", lambda r: str(r["roundtrip"]["roundtrip"])),
    ]
    for k, f in rows:
        va, vb = f(a), f(b)
        flag = "   <== DIFF" if va != vb else ""
        print(f"  {k:16s}  {va:>22s}   {vb:>22s}{flag}")
    print("  flash UIDs (unique per flash, not compared): "
          f"{a['flash']['uid']} / {b['flash']['uid']}")
    for addr, label in [("0x08", "ena"), ("0x09", "byp"), ("0x0b", "cpu reset"),
                        ("0x11", "out div"), ("0x12", "fb div")]:
        va, vb = a["regs"][addr], b["regs"][addr]
        flag = "   <== DIFF" if va != vb else ""
        print(f"  reg {addr} ({label:9s})  0x{int(va):02x}{'':>19s}0x{int(vb):02x}{flag}")
    print()
    show_verdict(b)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="chip", help="name for the JSON report")
    ap.add_argument("--url", default=None, help="FTDI URL (default: auto-discover)")
    ap.add_argument("--out", default=None, help="report path (default padcheck_<label>.json)")
    ap.add_argument("--compare", nargs=2, metavar=("WORKING_JSON", "SUSPECT_JSON"),
                    help="diff two saved reports and print the verdict")
    ap.add_argument("--probe", type=float, default=0, metavar="SECONDS",
                    help="after the report, keep reading the flash for SECONDS so you "
                         "can scope pads 24-27 / J15 / U13 and find the open bond")
    args = ap.parse_args()

    if args.compare:
        a = json.load(open(args.compare[0]))
        b = json.load(open(args.compare[1]))
        compare(a, b)
        return

    try:
        r = collect(args.url, probe=args.probe)
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        print("  The FTDI must be free: stop the GUI/bridge and check `lsusb | grep 0403`.")
        return 1
    r["label"] = args.label
    r["hk_ok"] = r["mfg"] == MFG_OK
    r["flash_ok"] = flash_ok_of(r)

    out = args.out or f"padcheck_{args.label}.json"
    with open(out, "w") as fh:
        json.dump(r, fh, indent=2)
    print(f"\nwrote {out}")
    show_verdict(r)


if __name__ == "__main__":
    main()
