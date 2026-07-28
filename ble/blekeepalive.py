#!/usr/bin/env python3
"""Test whether the module's ~14s disconnect is an idle timeout that ordinary
GATT traffic resets, or a hard timer that drops every client regardless.

If reading a harmless GAP characteristic on a short interval holds the link
open indefinitely, a permanent BLE connection is possible without ever writing
an unknown byte to the tank.

Writes nothing. Reads only 2A00 (device name), which is inert.
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
DEVICE_NAME = "00002a00-0000-1000-8000-00805f9b34fb"

RUN_SECS = int(sys.argv[1]) if len(sys.argv) > 1 else 120
POKE_EVERY = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0

t0 = time.monotonic()
gone = asyncio.Event()


def stamp():
    return f"+{time.monotonic() - t0:6.2f}s"


def on_disconnect(_c):
    print(f"{stamp()}  *** DISCONNECTED BY PEER ***", flush=True)
    gone.set()


def notify_cb(label):
    def cb(_s, data: bytearray):
        hexs = " ".join(f"{b:02x}" for b in data)
        txt = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
        print(f"{stamp()}  {label} len={len(data)}  {hexs}  |{txt}|", flush=True)
    return cb


async def main():
    dev = await find_module(25.0)
    if dev is None:
        print("not advertising -- needs the button dance")
        return

    async with BleakClient(dev, disconnected_callback=on_disconnect) as client:
        print(f"{stamp()}  connected", flush=True)
        for label, uuid in [("A", "69400002-b5a3-f393-e0a9-e50e24dcca99"),
                            ("B", "7f510005-b5a3-f393-e0a9-e50e24dcca9e")]:
            await client.start_notify(uuid, notify_cb(label))
        print(f"{stamp()}  subscribed both; poking 2a00 every {POKE_EVERY}s", flush=True)

        deadline = time.monotonic() + RUN_SECS
        while time.monotonic() < deadline and not gone.is_set():
            try:
                v = await client.read_gatt_char(DEVICE_NAME)
                print(f"{stamp()}  read 2a00 ok -> {v.decode(errors='replace')}", flush=True)
            except Exception as e:
                print(f"{stamp()}  read failed: {e}", flush=True)
                break
            try:
                await asyncio.wait_for(gone.wait(), timeout=POKE_EVERY)
            except asyncio.TimeoutError:
                pass

        if not gone.is_set():
            print(f"{stamp()}  STILL CONNECTED after {RUN_SECS}s -- idle timeout is resettable",
                  flush=True)


asyncio.run(main())
