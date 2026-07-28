#!/usr/bin/env python3
"""Enumerate the console's AT-style query names.

AT? answers AT, so the grammar is <NAME>? and an unrecognised NAME still gives
ERROR:invalidCMD. Sweep candidate names in query form only -- no '=' writes.
"""
import asyncio, string, sys
from bleak import BleakClient, BleakScanner

W6 = "69400003-b5a3-f393-e0a9-e50e24dcca99"
W7 = "7f510006-b5a3-f393-e0a9-e50e24dcca9e"
N7 = "7f510005-b5a3-f393-e0a9-e50e24dcca9e"
BORING = "ERROR:invalidCMD"

NAMES = [
    "AT", "ATI", "ATE", "ATZ", "A", "T",
    "VER", "VERSION", "FW", "FWVER", "SW", "SWVER", "HW", "HWVER", "REV",
    "INFO", "ID", "SN", "DSN", "MODEL", "NAME", "MAC", "ADDR", "BDADDR",
    "STATUS", "STATE", "MODE", "CFG", "CONFIG", "SET", "GET", "ALL", "LIST",
    "HELP", "CMD", "CMDS", "MENU", "DEBUG", "LOG", "TEST", "DIAG",
    "WIFI", "SSID", "RSSI", "IP", "AP", "BLE", "BT", "PIN", "KEY", "PAIR",
    "UART", "BAUD", "TIME", "CLOCK", "RTC", "TEMP", "SETPOINT", "LEAK",
    "TANK", "HEATER", "WH", "REG", "BLOCK", "READ", "DUMP", "MEM",
    "BOOT", "APP", "OTA", "DFU", "UPGRADE", "PART", "SLOT",
    "AYLA", "CLOUD", "TOKEN", "PROV", "SERIAL", "MFG", "PROD",
]
NAMES += list(string.ascii_uppercase)
NAMES += [n.lower() for n in ("VER", "INFO", "STATUS", "HELP", "MAC", "DSN")]

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
    hits = []
    async with BleakClient(dev) as c:
        await c.write_gatt_char(W6, (dev.name or "")[-6:].encode(), response=True)
        await asyncio.sleep(0.6)
        box = []
        await c.start_notify(N7, lambda _s, d: box.append(bytes(d)))
        print(f"authenticated; probing {len(NAMES)} names", flush=True)
        for n in NAMES:
            box.clear()
            try:
                await c.write_gatt_char(W7, (n + "?\n").encode(), response=True)
            except Exception as e:
                print(f"  {n}? write failed: {e}", flush=True)
                continue
            await asyncio.sleep(0.3)
            txt = b"".join(box).decode("ascii", "replace").strip()
            if txt and txt != BORING:
                hits.append((n, txt))
                print(f"  HIT {n}? -> {txt!r}", flush=True)
            elif not box:
                print(f"  SILENT {n}?", flush=True)
    print(f"\n{len(hits)} recognised names")

asyncio.run(main())
