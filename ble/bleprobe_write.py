#!/usr/bin/env python3
"""Probe the tank module's two write pipes for anything that elicits a reply.

The module hangs up ~14s after connecting unless it hears its own protocol, and
GAP reads don't reset that timer, so the only way forward is to write. Each
connection gives roughly a 14s budget; this walks a candidate list across as
many reconnects as it takes and records which payloads produced notifications.

Payloads are deliberately inert-looking: line terminators, AT probes, and short
framing bytes. Nothing here resembles a credential push or a factory reset
command, but the protocol is unknown, so this is genuinely exploratory.

Usage: bleprobe_write.py [seconds_per_probe]
"""
import asyncio
import os
import sys
import time
from bleak import BleakClient, BleakScanner

# The module uses a BLE random static address. Set ICOMM_ADDR to yours, or
# leave it unset and let the scripts match on the advertised iCOMM name.
ADDR = os.environ.get("ICOMM_ADDR", "")


async def find_module(timeout=20.0):
    """The module by address if ICOMM_ADDR is set, else the first iCOMM-*."""
    if ADDR:
        return await BleakScanner.find_device_by_address(ADDR, timeout=timeout)
    found = await BleakScanner.discover(timeout=timeout)
    return next((d for d in found
                 if d.name and d.name.upper().startswith("ICOMM")), None)

PIPE_A_WRITE = "69400003-b5a3-f393-e0a9-e50e24dcca99"
PIPE_A_NOTIFY = "69400002-b5a3-f393-e0a9-e50e24dcca99"
PIPE_B_WRITE = "7f510006-b5a3-f393-e0a9-e50e24dcca9e"
PIPE_B_NOTIFY = "7f510005-b5a3-f393-e0a9-e50e24dcca9e"

SETTLE = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0

PAYLOADS = [
    ("crlf",        b"\r\n"),
    ("at",          b"AT\r\n"),
    ("query",       b"?\r\n"),
    ("nul",         b"\x00"),
    ("one",         b"\x01"),
    ("aa55",        b"\xaa\x55"),
    ("stx",         b"\x02\x00\x03"),
    ("json",        b'{"cmd":"info"}'),
    ("hello",       b"hello\r\n"),
    ("at_gmr",      b"AT+GMR\r\n"),
]

PIPES = [("A", PIPE_A_WRITE), ("B", PIPE_B_WRITE)]

# (pipe, payload_name) pairs still to try, consumed across reconnects.
todo = [(pl, pw, n, b) for (pl, pw) in PIPES for (n, b) in PAYLOADS]

results = []
_replies = []


def hexdump(data):
    hexs = " ".join(f"{x:02x}" for x in data)
    txt = "".join(chr(x) if 32 <= x < 127 else "." for x in data)
    return f"{hexs}  |{txt}|"


def make_cb(label):
    def cb(_s, data: bytearray):
        _replies.append((label, bytes(data)))
        print(f"        <<< {label} {hexdump(data)}", flush=True)
    return cb


async def one_connection():
    """Burn through as much of `todo` as this connection's ~14s allows."""
    # BlueZ drops the device object from its cache shortly after a disconnect,
    # so every reconnect needs a fresh discovery. A scan started too soon after
    # the drop often comes back empty even though the module is still
    # advertising, hence the retries -- an empty scan here says nothing about
    # whether the module is actually quiet.
    target = None
    for attempt in range(5):
        target = await find_module(15.0)
        if target is not None:
            break
        print(f"  scan {attempt + 1}/5 found nothing; retrying", flush=True)
        await asyncio.sleep(4)
    if target is None:
        print("  could not find the module after 5 scans")
        return False

    gone = asyncio.Event()
    async with BleakClient(target, disconnected_callback=lambda _c: gone.set()) as client:
        await client.start_notify(PIPE_A_NOTIFY, make_cb("A-notify"))
        await client.start_notify(PIPE_B_NOTIFY, make_cb("B-notify"))
        print("  connected, notifications on", flush=True)

        while todo and not gone.is_set():
            pipe_label, pipe_uuid, name, payload = todo[0]
            _replies.clear()
            try:
                await client.write_gatt_char(pipe_uuid, payload, response=False)
                status = "written"
            except Exception as e:
                status = f"WRITE FAILED: {type(e).__name__}: {e}"
            print(f"  pipe {pipe_label}  {name:8s} {hexdump(payload):46s} {status}", flush=True)

            try:
                await asyncio.wait_for(gone.wait(), timeout=SETTLE)
            except asyncio.TimeoutError:
                pass

            if _replies:
                results.append((pipe_label, name, payload, list(_replies)))
            todo.pop(0)

    return True


async def main():
    attempts = 0
    while todo and attempts < 12:
        attempts += 1
        print(f"--- connection {attempts} ({len(todo)} probes left) ---", flush=True)
        if not await one_connection():
            break
        await asyncio.sleep(2)

    print("\n=== PAYLOADS THAT GOT A REPLY ===")
    if not results:
        print("none -- module stayed silent for every probe")
    for pipe, name, payload, replies in results:
        print(f"  pipe {pipe} {name} -> {len(replies)} notification(s)")
        for label, data in replies:
            print(f"      {label}: {hexdump(data)}")


asyncio.run(main())
