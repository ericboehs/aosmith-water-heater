#!/usr/bin/env python3
"""Read tank registers over BLE using the iCOMM app's own protocol.

Recovered by decompiling the app's Hermes bytecode bundle. The framing is:

    request   BD A0 07 <block> <offset> 01 <crc>
    response  BD A0 <len> <block> <offset> <lo> <hi> <crc>

with the 16-bit value being response[6] << 8 | response[5]. Frames go out as
raw bytes on the write pipe and come back as a notification on the listen
pipe. CRC is a table-driven CRC-8 (poly 0x5E, init 0, no final xor) over every
preceding byte of the frame.

Two details cost us earlier attempts: the app uses write-*with-response*, and
the module hangs up ~14s after connecting unless it hears valid protocol, so
reads are batched into one connection.

This script only issues reads (command A0). It writes no configuration.
"""
import asyncio
import os
from bleak import BleakClient, BleakScanner

# The module uses a BLE random static address. Set ICOMM_ADDR to yours, or
# leave it unset and let the scripts match on the advertised iCOMM name.
ADDR = os.environ.get("ICOMM_ADDR", "")
WRITE_UUID = "69400003-b5a3-f393-e0a9-e50e24dcca99"
NOTIFY_UUID = "69400002-b5a3-f393-e0a9-e50e24dcca99"

# CRC-8 table lifted verbatim from the app bundle; equivalent to poly 0x5E,
# init 0, non-reflected, no final xor (brute-forced against all 256 polys).
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

# Block 26 is the comms module's own map. Offsets are from the app's
# BLOCK_26_PARAMETERS table; the notes are the app's names, not measurements.
BLOCK_26 = {
    0: "MODE",
    1: "SETPOINT",
    2: "UNITS",
    3: "REAL_TIME_LW",
    4: "REAL_TIME_HW",
    5: "BLUETOOTH_CONNECTION_STATUS",
    6: "WIFI_CONNECTION_STATUS",
    13: "OADR_OVERRIDE_STATUS",
    32: "WIFI_RSSI_BARS",
    35: "SET_WATER_SENSOR",
    36: "WATER_SENSOR_STATUS",
    37: "WATER_AVAILABLE",
    38: "MODULE_FAULT_FLAGS",
}

BLOCK_13 = {
    0: "ONLINE",
    2: "REMOTE_SETPOINT",
    3: "REMOTE_MODE",
    4: "MAX_SETPOINT",
    9: "REMOTE_ENABLE",
    11: "FAULT_FLAGS",
}

MODE_BASE10 = {1: "SMART", 2: "STANDARD", 4: "VACATION",
               8: "EFFICIENCY(HeatPump)", 16: "HYBRID", 32: "ELECTRIC"}
LEAK_BITS = {0: "NO_WATER_DETECTED", 1: "WATER_DETECTED",
             2: "NO_SENSOR", 3: "DISCONNECTED"}


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = CRC_TABLE[(crc ^ b) & 0xFF]
    return crc


def build_read(block: int, offset: int) -> bytes:
    body = bytes([0xBD, 0xA0, 0x07, block, offset, 0x01])
    return body + bytes([crc8(body)])


def fmt(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


class Session:
    def __init__(self, client):
        self.client = client
        self.inbox = asyncio.Queue()

    def on_notify(self, _sender, data: bytearray):
        self.inbox.put_nowait(bytes(data))

    async def read_register(self, block: int, offset: int, timeout=3.0):
        """Returns (value, raw_frame) or (None, reason)."""
        while not self.inbox.empty():
            self.inbox.get_nowait()
        frame = build_read(block, offset)
        await self.client.write_gatt_char(WRITE_UUID, frame, response=True)
        try:
            resp = await asyncio.wait_for(self.inbox.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None, "timeout"
        if len(resp) < 8:
            return None, f"short ({fmt(resp)})"
        if crc8(resp[:-1]) != resp[-1]:
            return None, f"bad crc ({fmt(resp)})"
        value = (resp[6] << 8) | resp[5]
        return value, fmt(resp)


def describe(block, offset, value):
    if block == 26 and offset == 0:
        return MODE_BASE10.get(value, "?")
    if block == 26 and offset == 36:
        return LEAK_BITS.get(value, "?")
    if block == 26 and offset in (1,):
        return f"{value} raw; /256*1.8+32 = {round(value / 256 * 1.8 + 32)}F"
    if block == 26 and offset == 37:
        return f"{value}% if a straight percentage"
    return ""


async def main():
    # The module uses a BLE random static address that can change on power
    # cycle, so match on the advertised iCOMM name and fall back to the
    # last-known address only if the name scan turns up nothing.
    print("scanning for an iCOMM module ...", flush=True)
    dev = None
    for attempt in range(5):
        found = await BleakScanner.discover(timeout=15.0)
        for d in found:
            if d.name and d.name.upper().startswith("ICOMM"):
                dev = d
                break
        if dev is None:
            dev = next((d for d in found if d.address == ADDR), None)
        if dev:
            break
        print(f"  scan {attempt + 1}/5 empty, retrying", flush=True)
        await asyncio.sleep(3)
    if dev is None:
        print("not found")
        return

    print(f"found {dev.name}; connecting", flush=True)
    async with BleakClient(dev) as client:
        sess = Session(client)
        await client.start_notify(NOTIFY_UUID, sess.on_notify)
        print(f"connected, notifications on, mtu={client.mtu_size}", flush=True)

        # The app derives a 6-digit PIN from the advertised name and writes it
        # as ASCII to the writer pipe before anything else. Without it the
        # module ignores every frame and hangs up at ~14s.
        name = dev.name or ""
        pin = name[15:len(name) - 4] if len(name) > 21 else name[-6:]
        print(f"sending pin {pin!r}", flush=True)
        await client.write_gatt_char(WRITE_UUID, pin.encode(), response=True)
        await asyncio.sleep(1.0)
        while not sess.inbox.empty():
            print(f"  pin reply: {fmt(sess.inbox.get_nowait())}", flush=True)
        print(flush=True)

        for block, table in ((26, BLOCK_26), (13, BLOCK_13)):
            print(f"--- block {block} ---", flush=True)
            for offset, name in table.items():
                value, raw = await sess.read_register(block, offset)
                if value is None:
                    print(f"  {offset:3d} {name:30s} -- {raw}", flush=True)
                else:
                    note = describe(block, offset, value)
                    print(f"  {offset:3d} {name:30s} = {value:5d}  0x{value:04X}"
                          f"  {note}", flush=True)
                await asyncio.sleep(0.05)
            print(flush=True)


asyncio.run(main())
