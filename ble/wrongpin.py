#!/usr/bin/env python3
"""Is the watchdog satisfied by the CORRECT pin, or by any write at all?

The link held 45s with the real pin and died at 14.5s without one -- but the
no-pin run also skipped a 6-byte write entirely. If a deliberately wrong pin
also holds the link, the module is not validating anything and we are not
authenticated.
"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner

W6 = "69400003-b5a3-f393-e0a9-e50e24dcca99"
PIN = sys.argv[1] if len(sys.argv) > 1 else "000000"

async def main():
    dev = None
    for _ in range(3):
        for d in await BleakScanner.discover(timeout=10.0):
            if d.name and d.name.upper().startswith("ICOMM"):
                dev = d
                break
        if dev:
            break
    if not dev:
        print("not advertising")
        return
    gone = asyncio.Event()
    async with BleakClient(dev, disconnected_callback=lambda _: gone.set()) as c:
        t0 = time.monotonic()
        await c.write_gatt_char(W6, PIN.encode(), response=True)
        print(f"wrote pin {PIN!r} ({len(PIN)} bytes)", flush=True)
        try:
            await asyncio.wait_for(gone.wait(), timeout=40)
            print(f"module hung up at {time.monotonic()-t0:.2f}s", flush=True)
        except asyncio.TimeoutError:
            print(f"still connected at {time.monotonic()-t0:.2f}s", flush=True)

asyncio.run(main())
