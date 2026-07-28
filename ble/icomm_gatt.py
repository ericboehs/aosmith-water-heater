#!/usr/bin/env python3
"""Dump the module's GATT tree and walk the app's real session handshake.

Decompiling the app turned up the full authentication sequence, which is more
than the single "session" frame we had been sending:

    BD F2 05 01                 list stored pair keys
    BD F0 16 <18 bytes>         store our pair key (only if not already listed)
    BD F4 04                    ask for a random number
    BD F1 19 01 <20 bytes>      HMAC-SHA1(pairKeyString, randomBytes)

Frame length (byte 3) is the total frame length including the CRC, which now
checks out against all four of these plus the block read/write builders.

This only sends the three read-ish frames (F2/F4) plus the PIN. It does not
store a pair key and does not touch tank configuration.
"""
import asyncio
import sys
import time
from bleak import BleakClient, BleakScanner

SVC = "69400001-b5a3-f393-e0a9-e50e24dcca99"
NOTIFY = "69400002-b5a3-f393-e0a9-e50e24dcca99"
WRITE = "69400003-b5a3-f393-e0a9-e50e24dcca99"

CRC_TABLE = [
    0, 94, 188, 226, 38, 120, 154, 196, 76, 18, 240, 174, 106, 52, 214, 136,
    152, 198, 36, 122, 190, 224, 2, 92, 212, 138, 104, 54, 242, 172, 78, 16,
    110, 48, 210, 140, 72, 22, 244, 170, 34, 124, 158, 192, 4, 90, 184, 230,
    246, 168, 74, 20, 208, 142, 108, 50, 186, 228, 6, 88, 156, 194, 32, 126,
    220, 130, 96, 62, 250, 164, 70, 24, 144, 206, 44, 114, 182, 232, 10, 84,
    68, 26, 248, 166, 98, 60, 222, 128, 8, 86, 180, 234, 46, 112, 146, 204,
    178, 236, 14, 80, 148, 202, 40, 118, 254, 160, 66, 28, 216, 134, 100, 58,
    42, 116, 150, 200, 12, 82, 176, 238, 102, 56, 218, 132, 64, 30, 252, 162,
    230, 184, 90, 4, 192, 158, 124, 34, 170, 244, 22, 72, 140, 210, 48, 110,
    126, 32, 194, 156, 88, 6, 228, 186, 50, 108, 142, 208, 20, 74, 168, 246,
    136, 214, 52, 106, 174, 240, 18, 76, 196, 154, 120, 38, 226, 188, 94, 0,
    16, 78, 172, 242, 54, 104, 138, 212, 92, 2, 224, 190, 122, 36, 198, 152,
    58, 100, 134, 216, 28, 66, 160, 254, 118, 40, 202, 148, 80, 14, 236, 178,
    162, 252, 30, 64, 132, 218, 56, 102, 238, 176, 82, 12, 200, 150, 116, 42,
    84, 10, 232, 182, 114, 44, 206, 144, 24, 70, 164, 250, 62, 96, 130, 220,
    204, 146, 112, 46, 234, 180, 86, 8, 128, 222, 60, 98, 166, 248, 26, 68,
]

HOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0

t0 = time.monotonic()
gone = asyncio.Event()


def stamp():
    return f"+{time.monotonic() - t0:6.2f}s"


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = CRC_TABLE[(crc ^ b) & 0xFF]
    return crc


def framed(body) -> bytes:
    body = bytes(body)
    return body + bytes([crc8(body)])


def fmt(data: bytes) -> str:
    hexs = " ".join(f"{b:02X}" for b in data)
    txt = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{hexs}  |{txt}|"


def on_notify(_s, data: bytearray):
    print(f"{stamp()}  <<< {fmt(bytes(data))}", flush=True)


async def find():
    for attempt in range(6):
        for d in await BleakScanner.discover(timeout=12.0):
            if d.name and d.name.upper().startswith("ICOMM"):
                return d
        print(f"  scan {attempt + 1}/6 empty", flush=True)
        await asyncio.sleep(3)
    return None


async def main():
    dev = await find()
    if dev is None:
        print("module not advertising")
        return

    global t0
    t0 = time.monotonic()
    async with BleakClient(dev, disconnected_callback=lambda _c: gone.set()) as client:
        print(f"{stamp()}  connected to {dev.name} ({dev.address}), mtu={client.mtu_size}",
              flush=True)

        for svc in client.services:
            print(f"  service {svc.uuid}", flush=True)
            for ch in svc.characteristics:
                print(f"    char {ch.uuid}  handle={ch.handle}  {','.join(ch.properties)}",
                      flush=True)
                for d in ch.descriptors:
                    print(f"      desc {d.uuid} handle={d.handle}", flush=True)

        await client.start_notify(NOTIFY, on_notify)
        print(f"{stamp()}  notifications enabled on {NOTIFY}", flush=True)

        name = dev.name or ""
        pin = name[15:len(name) - 4] if len(name) > 21 else name[-6:]
        await client.write_gatt_char(WRITE, pin.encode(), response=True)
        print(f"{stamp()}  >>> pin {pin!r}", flush=True)
        await asyncio.sleep(3.0)

        # The app sleeps 300ms before every protocol frame.
        for label, body in (("list pair keys F2", [0xBD, 0xF2, 0x05, 0x01]),
                            ("random number F4", [0xBD, 0xF4, 0x04])):
            if gone.is_set():
                print(f"{stamp()}  link dropped", flush=True)
                return
            await asyncio.sleep(0.3)
            frame = framed(body)
            await client.write_gatt_char(WRITE, frame, response=True)
            print(f"{stamp()}  >>> {label}: {fmt(frame)}", flush=True)
            await asyncio.sleep(5.0)

        print(f"{stamp()}  holding {HOLD}s to see if the module ever hangs up", flush=True)
        try:
            await asyncio.wait_for(gone.wait(), timeout=HOLD)
            print(f"{stamp()}  peer disconnected", flush=True)
        except asyncio.TimeoutError:
            print(f"{stamp()}  still connected -- no idle hangup while we hold the link",
                  flush=True)


asyncio.run(main())
