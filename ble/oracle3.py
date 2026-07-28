#!/usr/bin/env python3
"""Learn the console's command shape, then sweep every 2-letter name.

AT? is the only token that has answered anything. Either the vocabulary is
tiny and locked, or we have the wrong shape. Phase 1 varies the syntax around
a known-good word; phase 2 assumes 2-character names and enumerates them.
"""
import asyncio, itertools, string
from bleak import BleakClient, BleakScanner

W6 = "69400003-b5a3-f393-e0a9-e50e24dcca99"
W7 = "7f510006-b5a3-f393-e0a9-e50e24dcca9e"
N7 = "7f510005-b5a3-f393-e0a9-e50e24dcca9e"
BORING = "ERROR:invalidCMD"

SHAPES = ["AT?", "at?", "At?", "AT ?", "AT??", "AT?\r", "AT", "AT\r",
          "AT+?", "AT+VER?", "ATVER?", "AT VER?", "AT?VER", "AT=?", "AT=",
          "AT?0", "AT?1", "AT?ALL", "AT?HELP", "AT?AT", "?AT", "??", "?",
          "AT+", "AT-", "AT.", "AT:", "AT_", "AT0?", "AT1?", "ATA?", "ATT?"]

async def probe(c, box, text, wait=0.3):
    box.clear()
    await c.write_gatt_char(W7, (text + "\n").encode(), response=True)
    await asyncio.sleep(wait)
    return b"".join(box).decode("ascii", "replace").strip()

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
    async with BleakClient(dev) as c:
        await c.write_gatt_char(W6, (dev.name or "")[-6:].encode(), response=True)
        await asyncio.sleep(0.6)
        box = []
        await c.start_notify(N7, lambda _s, d: box.append(bytes(d)))

        print("--- phase 1: syntax around AT ---", flush=True)
        for s in SHAPES:
            txt = await probe(c, box, s)
            if txt != BORING:
                print(f"  HIT {s!r} -> {txt!r}", flush=True)

        print("--- phase 2: every 2-letter name ---", flush=True)
        hits = 0
        for a, b in itertools.product(string.ascii_uppercase, repeat=2):
            name = a + b
            txt = await probe(c, box, name + "?", wait=0.15)
            if txt != BORING:
                hits += 1
                print(f"  HIT {name}? -> {txt!r}", flush=True)
        print(f"phase 2 done, {hits} hits", flush=True)

asyncio.run(main())
