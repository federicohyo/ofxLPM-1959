#!/usr/bin/env python3
"""Trigger-driven replay-server for capturing the piece without the hardware.

Speaks the app's three TCP ports (the same protocol tools/chip_server.py
relays from the plinth):

  9100  spike/log   -- READY, and an "S 14 <us>" line for every replayed reset
  9101  membrane    -- "seconds,volts" at 4 kHz
  9102  commands    -- reads the app's commands; "T" (one trigger per tape
                       click the app detected) is answered with one real
                       neuron-14 response

The membrane data is REAL measured neuron-14 voltage from the die (the bench
scope captures behind the paper's figures): every "response" is one intact
recorded cycle -- rest, staircase rise, reset crash. What is reconstructed is
only the sequencing: one response per trigger, the way the installation's
synapse answers the tape. Between triggers the membrane holds its real
post-crash level. Nothing is synthesised and nothing is time-stretched; the
waveforms are the die's own.

The 1:1 trigger-to-spike ratio this produces is what the installation's
self-healing loop settles to on the bench (87 : 88 measured), so the sentinel
stays happy too.

Usage:
  replay_chip_server.py [--port 9100] [--max-ms 90] csv [csv ...]
"""
import argparse
import collections
import socketserver
import threading
import time

import numpy as np


def load_responses(paths, max_ms, rate):
    """Cut the recordings into whole cycles: [previous crash .. this crash]."""
    volts = np.concatenate([np.loadtxt(p, delimiter=",", skiprows=2)[:, 1]
                             for p in paths])
    crashes = np.nonzero(np.diff(volts) < -0.3)[0] + 1
    segs, bounds = [], []
    start = 0
    for c in crashes:
        segs.append(volts[start:c + 1])
        bounds.append((start, c))
        start = c
    segs = [s for s in segs if len(s) <= max_ms * rate / 1000]
    return volts, segs, bounds, rate


class State:
    def __init__(self, segs, rate):
        self.segs = segs
        self.rate = rate
        self.spike_conns = []
        self.lock = threading.Lock()
        self.stream_lock = threading.Lock()
        self.t0 = None
        self.i = 0                    # samples streamed since t0
        self.trig_q = collections.deque(maxlen=2)
        self.seg = None                # response being played
        self.seg_i = 0
        self.last_value = segs[0][0] if segs else 0.0
        self.trig_count = 0
        self.spike_count = 0

    def next_seg(self):
        if not hasattr(self, "_order") or not self._order:
            rng = np.random.default_rng()
            self._order = list(rng.permutation(len(self.segs)))
        return self.segs[self._order.pop()]

    def broadcast(self, line):
        data = (line + "\n").encode()
        with self.lock:
            dead = []
            for s in self.spike_conns:
                try:
                    s.sendall(data)
                except OSError:
                    dead.append(s)
            for s in dead:
                self.spike_conns.remove(s)


class SpikeHandler(socketserver.BaseRequestHandler):
    """9100 -- the die's own report."""

    def handle(self):
        st = self.server.state
        with st.lock:
            st.spike_conns.append(self.request)
        print("spike/log client connected", flush=True)
        try:
            self.request.sendall(b"READY\n")
            while True:
                if not self.request.recv(4096):
                    break
                time.sleep(0.2)
        except OSError:
            pass
        finally:
            with st.lock:
                if self.request in st.spike_conns:
                    st.spike_conns.remove(self.request)
            print("spike/log client gone", flush=True)


class MembraneHandler(socketserver.BaseRequestHandler):
    """9101 -- seconds,volts at the board's own rate, trigger-driven."""

    def handle(self):
        st = self.server.state
        rate = st.rate
        print("membrane client connected", flush=True)
        with st.stream_lock:
            if st.t0 is None:
                st.t0 = time.monotonic()
            while True:
                if st.seg is not None and st.seg_i >= len(st.seg):
                    st.seg = None            # response finished
                if st.seg is None:
                    if st.trig_q:
                        st.trig_q.popleft()
                        st.seg = st.next_seg()
                        st.seg_i = 0
                if st.seg is not None:
                    v = st.seg[st.seg_i]
                    crashed = st.seg_i == len(st.seg) - 1
                    st.seg_i += 1
                    if crashed:
                        st.spike_count += 1
                        t_us = int(st.i / rate * 1e6)
                        st.broadcast(f"S 14 {t_us}")
                else:
                    v = st.last_value
                st.last_value = v
                t = st.i / rate
                try:
                    self.request.sendall(f"{t:.4f},{v:.6f}\n".encode())
                except OSError:
                    print("membrane client gone", flush=True)
                    return
                st.i += 1
                due = st.t0 + st.i / rate
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -0.5:
                    st.t0 = time.monotonic() - st.i / rate


class CmdHandler(socketserver.BaseRequestHandler):
    """9102 -- commands out of the app. 'T' is one trigger: answer it."""

    def handle(self):
        st = self.server.state
        buf = b""
        try:
            while True:
                data = self.request.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip() == b"T":
                        st.trig_q.append(1)
                        st.trig_count += 1
        except OSError:
            pass


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(port, handler, state):
    srv = ReusableTCPServer(("127.0.0.1", port), handler)
    srv.state = state
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--max-ms", type=float, default=90.0,
                    help="only use responses up to this long (default 90)")
    args = ap.parse_args()

    volts, segs, bounds, rate = load_responses(args.csv, args.max_ms, 4000.0)
    st = State(segs, rate)

    serve(args.port, SpikeHandler, st)
    serve(args.port + 1, MembraneHandler, st)
    serve(args.port + 2, CmdHandler, st)

    durs = [len(s) / rate * 1000 for s in segs]
    print(f"{len(volts)} samples = {len(volts)/rate:.1f} s at {rate:.0f} Hz, "
          f"{len(segs)} responses ({min(durs):.0f}-{max(durs):.0f} ms), "
          f"ports {args.port}-{args.port+2}", flush=True)
    while True:
        time.sleep(5)
        print(f"streamed {st.i} samples, triggers {st.trig_count}, "
              f"spikes {st.spike_count}, pending {len(st.trig_q)}", flush=True)


if __name__ == "__main__":
    main()
