#!/usr/bin/env python3
"""Try the next-generation heat pump register map.

Every well-formed read we have sent used block 26 or 13 -- the legacy map. The
bundle shows NEXT_GEN_HEAT_PUMP* devices use a different one entirely, and the
app's first authenticated read for them is a 16-entry block-11 section read.
This tank is an HPS10, so that is plausibly the only map its firmware serves.
"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner
sys.path.insert(0, "/home/ericboehs/ble")
from icomm_ble import crc8, fmt

W6 = "69400003-b5a3-f393-e0a9-e50e24dcca99"
N6 = "69400002-b5a3-f393-e0a9-e50e24dcca99"
N7 = "7f510005-b5a3-f393-e0a9-e50e24dcca9e"

def read(block, offset, count):
    b = bytes([0xBD, 0xA0, 0x07, block, offset, count])
    return b + bytes([crc8(b)])

PROBES = [
    ("next-gen block 11 section (the app's first read)", read(0x0B, 0x00, 0x10)),
    ("next-gen block 11 offset 0, single",               read(0x0B, 0x00, 0x01)),
    ("next-gen block 1 (max setpoint)",                  read(0x01, 0x00, 0x01)),
    ("next-gen block 2 (current fault)",                 read(0x02, 0x00, 0x01)),
    ("legacy block 26 section, as the app sends it",     read(0x1A, 0x00, 0x02)),
    ("F2 again, for a same-connection control",
     bytes([0xBD, 0xF2, 0x05, 0x01, 0xD8])),
]

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
        print("not advertising -- re-enter pairing mode")
        return
    async with BleakClient(dev) as c:
        await c.write_gatt_char(W6, (dev.name or "")[-6:].encode(), response=True)
        await asyncio.sleep(0.6)
        box = []
        await c.start_notify(N6, lambda _s, d: box.append(("6940", bytes(d))))
        await c.start_notify(N7, lambda _s, d: box.append(("7f51", bytes(d))))
        print("authenticated", flush=True)
        for label, payload in PROBES:
            box.clear()
            await c.write_gatt_char(W6, payload, response=True)
            await asyncio.sleep(2.5)
            if box:
                for pipe, r in box:
                    print(f"  HIT {label}\n      {fmt(payload)} -> [{pipe}] {fmt(r)}",
                          flush=True)
            else:
                print(f"      silent: {label}  ({fmt(payload)})", flush=True)

asyncio.run(main())
