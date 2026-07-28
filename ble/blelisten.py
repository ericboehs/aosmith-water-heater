#!/usr/bin/env python3
"""Connect to the tank's iCOMM BLE module, subscribe to both notify pipes and
log whatever it sends unprompted.

The profile is two Nordic-UART-style serial pipes (write + notify), so nothing
is readable by browsing -- the only way to learn the protocol is to listen and
then talk. This script only listens; it writes nothing to the tank.

nRF Connect saw the module drop the link ~14s after connecting, so the
disconnect callback timestamps that too.
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
LISTEN_SECS = int(sys.argv[1]) if len(sys.argv) > 1 else 90

PIPES = [
    ("A-notify", "69400002-b5a3-f393-e0a9-e50e24dcca99"),
    ("B-notify", "7f510005-b5a3-f393-e0a9-e50e24dcca9e"),
]

t0 = time.monotonic()
disconnected = asyncio.Event()


def stamp():
    return f"+{time.monotonic() - t0:6.2f}s"


def on_disconnect(_client):
    print(f"{stamp()}  *** DISCONNECTED BY PEER ***", flush=True)
    disconnected.set()


def make_cb(label):
    def cb(_sender, data: bytearray):
        hexs = " ".join(f"{b:02x}" for b in data)
        txt = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
        print(f"{stamp()}  {label}  len={len(data):3d}  {hexs}  |{txt}|", flush=True)
    return cb


async def main():
    print(f"scanning for {ADDR} ...", flush=True)
    dev = await find_module(20.0)
    if dev is None:
        print("NOT FOUND -- module is not advertising (pairing mode may have expired)")
        return

    print(f"found: {dev.name} @ {dev.address}", flush=True)

    async with BleakClient(dev, disconnected_callback=on_disconnect) as client:
        print(f"{stamp()}  connected", flush=True)

        # Confirm the GATT map matches what nRF Connect showed on the phone.
        for svc in client.services:
            print(f"  service {svc.uuid}", flush=True)
            for ch in svc.characteristics:
                print(f"    char {ch.uuid}  {','.join(ch.properties)}", flush=True)

        for label, uuid in PIPES:
            try:
                await client.start_notify(uuid, make_cb(label))
                print(f"{stamp()}  subscribed {label}", flush=True)
            except Exception as e:
                print(f"{stamp()}  subscribe {label} FAILED: {e}", flush=True)

        print(f"{stamp()}  listening {LISTEN_SECS}s (writing nothing) ...", flush=True)
        try:
            await asyncio.wait_for(disconnected.wait(), timeout=LISTEN_SECS)
        except asyncio.TimeoutError:
            print(f"{stamp()}  listen window ended, still connected", flush=True)

    print(f"{stamp()}  done", flush=True)


asyncio.run(main())
