#!/usr/bin/env python3
"""Try candidate pairing codes, each followed by the app's first real frame.

The HCI trace says everything below the app layer is already correct: MTU 151,
CCCD 0100 written on the notify characteristic, every Write Request answered
with a Write Response. The module simply never sends a Handle Value
Notification, then drops the link at ~15s. That points at the app layer, and
the only app-layer secret is the pairing code.

The app derives it from the advertised name as name.slice(15) for a 21-char
name, which gives "949045" -- but the same code path also has a "000000"
default and a longer-name branch, so the derivation is worth testing against
alternatives rather than assuming.

Each candidate is written, then 300ms later (the app's own inter-frame delay)
the pair-key list request goes out. A notification after any one of them
identifies the right code by timestamp.
"""
import asyncio
import sys
import time
from bleak import BleakClient, BleakScanner

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

t0 = time.monotonic()
gone = asyncio.Event()
got = []


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
    got.append((time.monotonic() - t0, bytes(data)))


def candidates(name: str):
    body = name.split("-", 1)[1] if "-" in name else name
    return [
        ("app-derived", name[-6:]),
        ("full serial", body),
        ("full name", name),
        ("default", "000000"),
        ("serial tail 9", body[-9:]),
        ("app-derived + nul", name[-6:] + "\x00"),
    ]


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
    name = dev.name or ""
    todo = candidates(name)
    if len(sys.argv) > 1:
        todo = todo[int(sys.argv[1]):]

    while todo:
        global t0
        t0 = time.monotonic()
        gone.clear()
        async with BleakClient(dev, disconnected_callback=lambda _c: gone.set()) as client:
            await client.start_notify(NOTIFY, on_notify)
            print(f"{stamp()}  connected, subscribed", flush=True)

            while todo and not gone.is_set():
                label, pin = todo[0]
                try:
                    await client.write_gatt_char(WRITE, pin.encode(), response=True)
                except Exception as e:
                    print(f"{stamp()}  {label} write failed: {e}", flush=True)
                    break
                print(f"{stamp()}  >>> pin [{label}] {pin!r}", flush=True)
                await asyncio.sleep(0.3)
                if gone.is_set():
                    break
                frame = framed([0xBD, 0xF2, 0x05, 0x01])
                try:
                    await client.write_gatt_char(WRITE, frame, response=True)
                except Exception as e:
                    print(f"{stamp()}  F2 after {label} failed: {e}", flush=True)
                    break
                print(f"{stamp()}  >>> F2: {fmt(frame)}", flush=True)
                await asyncio.sleep(1.7)
                todo.pop(0)

            print(f"{stamp()}  link {'dropped' if gone.is_set() else 'still up'}; "
                  f"{len(todo)} candidate(s) left", flush=True)
        if todo:
            await asyncio.sleep(3)
            dev = await find()
            if dev is None:
                print("module stopped advertising")
                return

    print(f"\n{len(got)} notification(s) total")


asyncio.run(main())
