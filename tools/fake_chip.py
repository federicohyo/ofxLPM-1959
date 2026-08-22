#!/usr/bin/env python3
"""
Fake chip server for testing the remote-chip path in ofxLPM-1959.
Listens on the same three TCP ports as chip_server.py and sends
enough data to exercise the NetworkBridge/draw/audio plumbing.

Usage:
    ./fake_chip.py
    # then in settings.json set  "remoteChip": "127.0.0.1:9100:9101:9102"
"""

import math
import socket
import threading
import time

SPIKE_PORT = 9100
MEMBRANE_PORT = 9101
CMD_PORT = 9102
RATE_HZ = 20.0         # simulated neuron firing rate
SAMPLE_HZ = 1000.0     # simulated ADC rate
VOLTS_BASE = 0.3       # resting potential
VOLTS_THR = 0.8        # spikes from here
RESET_CRASH = -0.5     # crash depth
RECOVERY_TAU = 0.015   # exponential recovery after reset
REFRACTORY_S = 0.002


def spike_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.bind(("0.0.0.0", SPIKE_PORT))
    sock.listen(5)
    print(f"[fake] spike port {SPIKE_PORT}", flush=True)

    while True:
        cl, addr = sock.accept()
        print(f"[fake] spike client {addr}", flush=True)
        try:
            cl.sendall(b"READY\n")
            ts = 0
            while True:
                # one spike every 1/RATE_HZ seconds
                cl.sendall(f"S 14 {ts}\n".encode())
                ts += int(1e6 / RATE_HZ)
                time.sleep(1.0 / RATE_HZ)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def membrane_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.bind(("0.0.0.0", MEMBRANE_PORT))
    sock.listen(5)
    print(f"[fake] membrane port {MEMBRANE_PORT}", flush=True)

    while True:
        cl, addr = sock.accept()
        print(f"[fake] membrane client {addr}", flush=True)
        t = 0.0
        # LIF state
        v = VOLTS_BASE
        last_spike_t = -1.0
        integrating = True
        try:
            while True:
                dt = 1.0 / SAMPLE_HZ
                t += dt
                if integrating:
                    v += (0.02 - v * 0.15) * dt  # leak toward 0.13 V with input current
                    if v >= VOLTS_THR:
                        v = RESET_CRASH
                        integrating = False
                        last_spike_t = t
                else:
                    # recovery: exponential back to rest
                    elapsed = t - last_spike_t
                    if elapsed < REFRACTORY_S:
                        v = RESET_CRASH + (VOLTS_BASE - RESET_CRASH) * (elapsed / REFRACTORY_S)
                    else:
                        v = VOLTS_BASE + 0.01 * math.sin(t * 3.0)
                        integrating = True

                cl.sendall(f"{t:.3f},{v:.3f}\n".encode())
                time.sleep(dt)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def cmd_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.bind(("0.0.0.0", CMD_PORT))
    sock.listen(5)
    print(f"[fake] cmd port {CMD_PORT}", flush=True)
    while True:
        cl, addr = sock.accept()
        print(f"[fake] cmd client {addr}", flush=True)
        try:
            while True:
                data = cl.recv(4096)
                if not data:
                    break
                for line in data.decode().splitlines():
                    if not line.strip():
                        continue
                    print(f"[fake] cmd <- {line.strip()}", flush=True)
                    if line.startswith("B "):
                        cl.sendall(f"BOK{line[1:]}\n".encode())
                    elif line.startswith("M "):
                        cl.sendall(f"MOK{line[2:]}\n".encode())
                    elif line.startswith("P "):
                        cl.sendall(f"POK{line[1:]}\n".encode())
                    elif line.startswith("S "):
                        cl.sendall(f"SOK{line[1:]}\n".encode())
                    elif line.startswith("T"):
                        cl.sendall(b"RECORDO\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def main():
    threads = [
        threading.Thread(target=spike_server, daemon=True),
        threading.Thread(target=membrane_server, daemon=True),
        threading.Thread(target=cmd_server, daemon=True),
    ]
    for t in threads:
        t.start()
    print("[fake] listening on 127.0.0.1:9100/9101/9102", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
