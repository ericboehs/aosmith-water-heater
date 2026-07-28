#!/usr/bin/env python3
"""Find out what makes the module answer a protocol frame.

The framing, CRC and register map are known-good from the app bundle, and the
module ACKs our writes at the ATT layer, but it never notifies. This walks the
remaining unknowns one connection at a time: which pipe replies, whether the
PIN matters, and whether write-with/without-response matters.

Reads only (command A0). Nothing here changes tank configuration.
"""
import asyncio
import sys
import time
from bleak import BleakClient, BleakScanner

SVC_A_WRITE = "69400003-b5a3-f393-e0a9-e50e24dcca99"
SVC_A_NOTIFY = "69400002-b5a3-f393-e0a9-e50e24dcca99"
SVC_B_WRITE = "7f510006-b5a3-f393-e0a9-e50e24dcca9e"
SVC_B_NOTIFY = "7f510005-b5a3-f393-e0a9-e50e24dcca9e"

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
PAIR_FIRST = "--pair" in sys.argv


def stamp():
    return f"+{time.monotonic() - t0:6.2f}s"


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = CRC_TABLE[(crc ^ b) & 0xFF]
    return crc


def framed(body) -> bytes:
    """Append the CRC. Byte 3 of body is the total frame length incl. CRC."""
    body = bytes(body)
    return body + bytes([crc8(body)])


def read_frame(block: int, offset: int, count: int = 1) -> bytes:
    return framed([0xBD, 0xA0, 0x07, block, offset, count])


def fmt(data: bytes) -> str:
    hexs = " ".join(f"{b:02X}" for b in data)
    txt = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{hexs}  |{txt}|"


def cb(label):
    def inner(_s, data: bytearray):
        print(f"{stamp()}  <<< {label}: {fmt(bytes(data))}", flush=True)
    return inner


async def find():
    for _ in range(6):
        for d in await BleakScanner.discover(timeout=12.0):
            if d.name and d.name.upper().startswith("ICOMM"):
                return d
        await asyncio.sleep(3)
    return None


async def run(plan_name, steps, send_pin):
    dev = await find()
    if dev is None:
        print("module not advertising")
        return False

    global t0
    t0 = time.monotonic()
    gone.clear()
    print(f"\n===== {plan_name} =====", flush=True)
    async with BleakClient(dev, disconnected_callback=lambda _c: gone.set()) as client:
        for label, uuid in (("A", SVC_A_NOTIFY), ("B", SVC_B_NOTIFY)):
            await client.start_notify(uuid, cb(label))
        print(f"{stamp()}  connected, both pipes subscribed", flush=True)

        # The app derives the PIN from the *connected* device name, which comes
        # from GAP 2A00 and may differ from the advertised name.
        try:
            gap = await client.read_gatt_char("00002a00-0000-1000-8000-00805f9b34fb")
            print(f"{stamp()}  GAP name: {gap.decode(errors='replace')!r} "
                  f"(advertised {dev.name!r})", flush=True)
        except Exception as e:
            print(f"{stamp()}  GAP name read failed: {e}", flush=True)

        if PAIR_FIRST:
            try:
                ok = await client.pair()
                print(f"{stamp()}  pair() -> {ok}", flush=True)
            except Exception as e:
                print(f"{stamp()}  pair() failed: {type(e).__name__}: {e}", flush=True)

        if send_pin:
            name = dev.name or ""
            pin = name[15:len(name) - 4] if len(name) > 21 else name[-6:]
            await client.write_gatt_char(SVC_A_WRITE, pin.encode(), response=True)
            print(f"{stamp()}  >>> pin {pin}", flush=True)
            await asyncio.sleep(1.5)

        for desc, uuid, payload, with_resp in steps:
            if gone.is_set():
                print(f"{stamp()}  link dropped, aborting plan", flush=True)
                break
            try:
                await client.write_gatt_char(uuid, payload, response=with_resp)
                print(f"{stamp()}  >>> {desc}: {fmt(payload)}", flush=True)
            except Exception as e:
                print(f"{stamp()}  >>> {desc} FAILED: {type(e).__name__}: {e}", flush=True)
            try:
                await asyncio.wait_for(gone.wait(), timeout=2.5)
            except asyncio.TimeoutError:
                pass

        if not gone.is_set():
            print(f"{stamp()}  still connected at end of plan", flush=True)
    return True


async def main():
    which = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "1"

    probe = read_frame(26, 37)
    plans = {
        # Does the reply come back on pipe B, or only with a raw (uncrc'd)
        # frame, or without the PIN? One variable per step.
        "1": ("pin, then pipe A variants", [
            ("A write-resp  26/37", SVC_A_WRITE, probe, True),
            ("A write-nores 26/37", SVC_A_WRITE, probe, False),
            ("A write-resp  27/0", SVC_A_WRITE, read_frame(27, 0), True),
            ("A write-resp  13/0", SVC_A_WRITE, read_frame(13, 0), True),
        ], True),
        "2": ("no pin, pipe B writer", [
            ("B write-resp  26/37", SVC_B_WRITE, probe, True),
            ("B write-nores 26/37", SVC_B_WRITE, probe, False),
            ("A write-resp  26/37", SVC_A_WRITE, probe, True),
        ], False),
        "3": ("pin only, then wait quietly", [], True),
        # The app runs createSession -> "BD F2 05 01" before issuing any
        # command. Byte 3 is the total frame length including CRC, which is
        # consistent across every frame builder in the bundle.
        "4": ("pin, session F2, then read", [
            ("session BD F2 05 01", SVC_A_WRITE, framed([0xBD, 0xF2, 0x05, 0x01]), True),
            ("read 26/37", SVC_A_WRITE, probe, True),
            ("read 26/1", SVC_A_WRITE, read_frame(26, 1), True),
        ], True),
        "5": ("session F2 with no pin", [
            ("session BD F2 05 01", SVC_A_WRITE, framed([0xBD, 0xF2, 0x05, 0x01]), True),
            ("read 26/37", SVC_A_WRITE, probe, True),
            ("read 26/0", SVC_A_WRITE, read_frame(26, 0), True),
        ], False),
    }
    name, steps, pin = plans[which]
    await run(name, steps, pin)


asyncio.run(main())
