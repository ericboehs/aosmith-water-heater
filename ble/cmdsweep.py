#!/usr/bin/env python3
"""Does the 6940 pipe answer ANY command, or is it inert in this state?

Authenticated, daemon stopped, and F2 still gets nothing. Sweep the two
command ranges we have evidence for -- A0..AF (reads) and F0..FF (session/
management) -- with well-formed minimal frames.

Deliberately excludes 0x40 (block write) and 0x07 (commit), the two commands
known to change tank state, and does not touch anything outside those ranges:
this is somebody's only water heater, not a fuzzing target.
"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner
sys.path.insert(0, "/home/ericboehs/ble")
from icomm_ble import crc8, fmt

W6 = "69400003-b5a3-f393-e0a9-e50e24dcca99"
N6 = "69400002-b5a3-f393-e0a9-e50e24dcca99"
SKIP = {0x40, 0x07}

def frames_for(cmd):
    """Both minimal shapes we have seen: bare, and the '05 01' query form."""
    a = bytes([0xBD, cmd, 0x04])
    b = bytes([0xBD, cmd, 0x05, 0x01])
    return [a + bytes([crc8(a)]), b + bytes([crc8(b)])]

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
    hits = 0
    async with BleakClient(dev) as c:
        t0 = time.monotonic()
        await c.write_gatt_char(W6, (dev.name or "")[-6:].encode(), response=True)
        await asyncio.sleep(0.6)
        box = []
        await c.start_notify(N6, lambda _s, d: box.append(bytes(d)))
        print("authenticated; sweeping A0..AF and F0..FF", flush=True)
        for base in (0xA0, 0xF0):
            for cmd in range(base, base + 16):
                if cmd in SKIP:
                    continue
                for f in frames_for(cmd):
                    box.clear()
                    try:
                        await c.write_gatt_char(W6, f, response=True)
                    except Exception as e:
                        print(f"  {fmt(f)} write failed: {e}", flush=True)
                        continue
                    await asyncio.sleep(0.25)
                    if box:
                        hits += 1
                        print(f"  HIT {fmt(f)} -> "
                              f"{' | '.join(fmt(r) for r in box)}", flush=True)
        print(f"\nsweep done at {time.monotonic()-t0:.0f}s, {hits} replies",
              flush=True)

asyncio.run(main())
