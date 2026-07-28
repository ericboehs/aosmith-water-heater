#!/usr/bin/env python3
"""Authenticated but inert -- maybe 6940 wants a different encoding.

The app builds frames as uppercase space-separated hex TEXT and only converts
to raw bytes at the library boundary; the sibling 7f51 pipe is plainly ASCII.
Try the text forms on 6940 before concluding the protocol itself is wrong.
"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner
sys.path.insert(0, "/home/ericboehs/ble")
from icomm_ble import crc8, fmt

W6 = "69400003-b5a3-f393-e0a9-e50e24dcca99"
N6 = "69400002-b5a3-f393-e0a9-e50e24dcca99"
N7 = "7f510005-b5a3-f393-e0a9-e50e24dcca9e"

RAW = bytes([0xBD, 0xF2, 0x05, 0x01, 0xD8])

CANDIDATES = [
    ("raw bytes",            RAW),
    ("hex text spaced",      b"BD F2 05 01 D8"),
    ("hex text spaced + nl", b"BD F2 05 01 D8\n"),
    ("hex text spaced + crlf", b"BD F2 05 01 D8\r\n"),
    ("hex text packed",      b"BDF20501D8"),
    ("hex text packed + nl", b"BDF20501D8\n"),
    ("hex text lower",       b"bd f2 05 01 d8"),
    ("base64 of raw",        b"vfIFAdg="),
    ("raw + nl",             RAW + b"\n"),
    ("no BD, raw",           bytes([0xF2, 0x05, 0x01])),
    ("hex text no BD",       b"F2 05 01"),
    ("hex text no BD + nl",  b"F2 05 01\n"),
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
        print("not advertising")
        return
    async with BleakClient(dev) as c:
        await c.write_gatt_char(W6, (dev.name or "")[-6:].encode(), response=True)
        await asyncio.sleep(0.6)
        box = []
        # Listen on both pipes: a reply could plausibly come back on either.
        await c.start_notify(N6, lambda _s, d: box.append(("6940", bytes(d))))
        await c.start_notify(N7, lambda _s, d: box.append(("7f51", bytes(d))))
        print("authenticated; trying encodings", flush=True)
        for label, payload in CANDIDATES:
            box.clear()
            try:
                await c.write_gatt_char(W6, payload, response=True)
            except Exception as e:
                print(f"  {label:24s} write failed: {e}", flush=True)
                continue
            await asyncio.sleep(1.2)
            if box:
                for pipe, r in box:
                    txt = r.decode("ascii", "replace").strip()
                    print(f"  HIT {label:24s} -> [{pipe}] {fmt(r)}  {txt!r}",
                          flush=True)
            else:
                print(f"      {label:24s} silent", flush=True)

asyncio.run(main())
