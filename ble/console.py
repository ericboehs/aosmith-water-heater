#!/usr/bin/env python3
"""Probe the 7f51 pipe's ASCII console for a command vocabulary.

It answers ERROR:invalidCMD to BD-framed binary, which means it is a
line-oriented text interface, not the DFU service we assumed. Only
enumeration/read words here -- nothing that could reset or erase.
"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner

W6, N7 = "69400003-b5a3-f393-e0a9-e50e24dcca99", "7f510005-b5a3-f393-e0a9-e50e24dcca9e"
W7 = "7f510006-b5a3-f393-e0a9-e50e24dcca9e"

WORDS = ["help", "?", "HELP", "AT", "at", "version", "ver", "info",
         "status", "get", "list", "cmd", "commands", "menu", "id",
         "mac", "name", "dsn", "read", "show", "debug", "sys"]

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
    t0 = time.monotonic()
    def log(m):
        print(f"[{time.monotonic()-t0:6.2f}s] {m}", flush=True)
    seen = []
    async with BleakClient(dev) as c:
        t0 = time.monotonic()
        await c.write_gatt_char(W6, (dev.name or "")[-6:].encode(), response=True)
        await asyncio.sleep(0.6)
        def on(_s, d):
            seen.append(bytes(d))
        await c.start_notify(N7, on)
        log("authenticated, console subscribed")
        for w in WORDS:
            for term in ("\n", "\r\n"):
                seen.clear()
                try:
                    await c.write_gatt_char(W7, (w + term).encode(), response=True)
                except Exception as e:
                    log(f"{w!r} write failed: {e}")
                    continue
                await asyncio.sleep(0.6)
                for r in seen:
                    txt = r.decode("ascii", "replace").rstrip("\n\r")
                    if txt != "ERROR:invalidCMD":
                        log(f"{w!r}{term!r} -> {txt!r}")
                if not seen:
                    log(f"{w!r}{term!r} -> (silence)")
        log("done")

asyncio.run(main())
