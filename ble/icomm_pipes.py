#!/usr/bin/env python3
"""Cross the pairing code and the first frame over both write pipes.

The module exposes two Nordic-UART-style services. The app only ever touches
69400001 (pin + frames), but the app was built for the whole iCOMM family and
this module also carries 7f510004, which the app never mentions. If this
hardware expects auth on one pipe and data on the other, or serves the whole
protocol on the pipe the app ignores, only crossing them will show it.

Every combination sends the pairing code, then the pair-key list request, and
listens on both notify pipes. Reads only; nothing here writes tank config.
"""
import asyncio
import time
from bleak import BleakClient, BleakScanner

A_NOTIFY = "69400002-b5a3-f393-e0a9-e50e24dcca99"
A_WRITE = "69400003-b5a3-f393-e0a9-e50e24dcca99"
B_NOTIFY = "7f510005-b5a3-f393-e0a9-e50e24dcca9e"
B_WRITE = "7f510006-b5a3-f393-e0a9-e50e24dcca9e"

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


def fmt(data: bytes) -> str:
    hexs = " ".join(f"{b:02X}" for b in data)
    txt = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{hexs}  |{txt}|"


def cb(label):
    def inner(_s, data: bytearray):
        print(f"{stamp()}  <<< [{label}] {fmt(bytes(data))}", flush=True)
        got.append((label, bytes(data)))
    return inner


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
    pin = (name[15:len(name) - 4] if len(name) > 21 else name[-6:]).encode()

    body = bytes([0xBD, 0xF2, 0x05, 0x01])
    with_crc = body + bytes([crc8(body)])

    combos = [
        ("pin A -> F2 B (no crc)", A_WRITE, B_WRITE, body),
        ("pin A -> F2 B (crc)", A_WRITE, B_WRITE, with_crc),
        ("pin B -> F2 B (crc)", B_WRITE, B_WRITE, with_crc),
        ("pin B -> F2 A (crc)", B_WRITE, A_WRITE, with_crc),
    ]

    while combos:
        global t0
        t0 = time.monotonic()
        gone.clear()
        async with BleakClient(dev, disconnected_callback=lambda _c: gone.set()) as client:
            await client.start_notify(A_NOTIFY, cb("A"))
            await client.start_notify(B_NOTIFY, cb("B"))
            print(f"{stamp()}  connected, both pipes subscribed", flush=True)

            while combos and not gone.is_set():
                label, pin_pipe, frame_pipe, frame = combos[0]
                try:
                    await client.write_gatt_char(pin_pipe, pin, response=True)
                    await asyncio.sleep(0.3)
                    await client.write_gatt_char(frame_pipe, frame, response=True)
                except Exception as e:
                    print(f"{stamp()}  {label} failed: {type(e).__name__}: {e}", flush=True)
                    break
                print(f"{stamp()}  >>> {label}: {fmt(frame)}", flush=True)
                await asyncio.sleep(2.2)
                combos.pop(0)

            print(f"{stamp()}  link {'dropped' if gone.is_set() else 'up'}; "
                  f"{len(combos)} left", flush=True)
        if combos:
            await asyncio.sleep(3)
            dev = await find()
            if dev is None:
                print("module stopped advertising")
                return

    print(f"\n{len(got)} notification(s)")


asyncio.run(main())
