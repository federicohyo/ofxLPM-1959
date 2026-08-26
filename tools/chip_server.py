#!/usr/bin/env python3
"""chip_server.py — relay neuron_bridge.py + membrane ADC over TCP.

Runs on the machine connected to the chip (e.g. Raspberry Pi near the plinth).
Exposes two TCP ports:
  - spikes port: lines from neuron_bridge.py stdout (S, RATE, DROPS, ...)
  - membrane port: "seconds,volts" lines from the Pixhawk ADC (/dev/ttyACM0).
    Lines are relayed whole and never parsed here, so the two-channel firmware
    ("seconds,ch1,ch2") passes through unchanged; picking a channel is the
    reader's business, and the GUI takes channel 1.

And one command port (read by the bridge): any line received here is piped into
neuron_bridge.py's stdin, so the remote GUI can send B, M, P, S, T, etc.

Usage:
  ./chip_server.py [--spike-port 9100] [--membrane-port 9101] [--cmd-port 9102]
                   [--bridge ../neuron_bridge.py] [--device /dev/ttyACM0]
"""

import argparse
import os
import select
import socket
import subprocess
import sys
import threading
import time


def relay_stdin_to_bridge(bridge_proc, cmd_port):
    """Read lines from cmd_port clients and write to bridge's stdin."""
    clients = []
    buf = b""
    cmd_port.listen(5)
    cmd_port.setblocking(False)
    while True:
        # Accept new clients
        try:
            cl, addr = cmd_port.accept()
            cl.setblocking(False)
            clients.append(cl)
            print(f"[cmd] client from {addr}", flush=True)
        except BlockingIOError:
            pass
        # Read from existing clients
        for cl in list(clients):
            try:
                data = cl.recv(4096)
                if not data:
                    clients.remove(cl)
                    cl.close()
                    continue
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    bridge_proc.stdin.write(line + b"\n")
                    bridge_proc.stdin.flush()
            except BlockingIOError:
                pass
            except Exception:
                clients.remove(cl)
                cl.close()
        time.sleep(0.001)


def relay_bridge_stdout(bridge_proc, spike_port):
    """Read bridge stdout lines and forward to all spike_port clients."""
    clients = []
    spike_port.listen(5)
    spike_port.setblocking(False)
    buf = b""
    while True:
        try:
            cl, addr = spike_port.accept()
            cl.setblocking(False)
            clients.append(cl)
            print(f"[spike] client from {addr}", flush=True)
        except BlockingIOError:
            pass
        # Read from bridge stdout
        try:
            data = os.read(bridge_proc.stdout.fileno(), 4096)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                payload = line + b"\n"
                for cl in list(clients):
                    try:
                        cl.send(payload)
                    except Exception:
                        clients.remove(cl)
                        cl.close()
        except BlockingIOError:
            pass
        time.sleep(0.001)


def relay_membrane(device_path, device_baud, membrane_port):
    """Read membrane ADC lines and forward to all membrane_port clients."""
    import serial
    ser = serial.Serial(device_path, device_baud, timeout=0)
    clients = []
    membrane_port.listen(5)
    membrane_port.setblocking(False)
    buf = b""
    count = 0
    t_last = time.monotonic()
    while True:
        try:
            cl, addr = membrane_port.accept()
            cl.setblocking(False)
            clients.append(cl)
            print(f"[membrane] client from {addr}", flush=True)
        except BlockingIOError:
            pass
        try:
            data = ser.read(ser.in_waiting or 1)
            if data:
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    payload = line + b"\n"
                    for cl in list(clients):
                        try:
                            cl.send(payload)
                        except Exception:
                            clients.remove(cl)
                            cl.close()
                    count += 1
                    now = time.monotonic()
                    if now - t_last >= 5.0:
                        print(f"[membrane] {count / (now - t_last):.0f} lines/s", flush=True)
                        count = 0
                        t_last = now
        except Exception:
            pass
        time.sleep(0.001)


def main():
    parser = argparse.ArgumentParser(description="Chip server: relay chip data over TCP")
    parser.add_argument("--spike-port", type=int, default=9100)
    parser.add_argument("--membrane-port", type=int, default=9101)
    parser.add_argument("--cmd-port", type=int, default=9102)
    parser.add_argument("--bridge", default="../tools/neuron_bridge.py")
    parser.add_argument("--device", default="/dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    # Start neuron_bridge.py as subprocess.
    # stderr is NOT piped: the bridge redirects all of its chatter there, and a
    # pipe nobody drains fills after 64 KB and blocks the bridge mid-stream.
    # Inherit our own stderr instead, so it lands in the log we are started with.
    bridge_proc = subprocess.Popen(
        [sys.executable, "-u", args.bridge],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        cwd=os.path.dirname(os.path.abspath(args.bridge)) or ".",
    )

    # Create TCP sockets
    spike_port = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    spike_port.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)
    spike_port.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    spike_port.bind(("0.0.0.0", args.spike_port))

    membrane_port = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    membrane_port.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)
    membrane_port.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    membrane_port.bind(("0.0.0.0", args.membrane_port))

    cmd_port = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    cmd_port.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)
    cmd_port.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    cmd_port.bind(("0.0.0.0", args.cmd_port))

    print(f"[chip_server] spike port {args.spike_port}", flush=True)
    print(f"[chip_server] membrane port {args.membrane_port}", flush=True)
    print(f"[chip_server] command port {args.cmd_port}", flush=True)

    threads = [
        threading.Thread(target=relay_bridge_stdout, args=(bridge_proc, spike_port), daemon=True),
        threading.Thread(target=relay_membrane, args=(args.device, args.baud, membrane_port), daemon=True),
        threading.Thread(target=relay_stdin_to_bridge, args=(bridge_proc, cmd_port), daemon=True),
    ]
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        bridge_proc.terminate()
        bridge_proc.wait()


if __name__ == "__main__":
    main()
