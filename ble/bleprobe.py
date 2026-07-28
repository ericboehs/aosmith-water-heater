#!/usr/bin/env python3
"""Read the tank module's readable GAP attributes, then check whether it keeps
advertising after a connection has been made.

Everything here is read-only. Nothing is written to either serial pipe -- the
protocol on those is unknown and this device may use BLE for WiFi provisioning,
so a blind write could disturb its configuration.

The advertising question matters: if the module only advertises while in
pairing mode, BLE can't serve as an always-on transport without pressing
buttons on the tank, and CTA-2045 stays the only viable local control path.
"""
import asyncio
import os
from bleak import BleakClient, BleakScanner

# The module uses a BLE random static address. Set ICOMM_ADDR to yours, or
# leave it unset and let the scripts match on the advertised iCOMM name.
ADDR = os.environ.get("ICOMM_ADDR", "")


async def find_module(timeout=20.0):
    """The module by address if ICOMM_ADDR is set, else the first iCOMM-*."""
    if ADDR:
        return await BleakScanner.find_device_by_address(ADDR, timeout=timeout)
    found = await BleakScanner.discover(timeout=timeout)
    return next((d for d in found
                 if d.name and d.name.upper().startswith("ICOMM")), None)

GAP = [
    ("2a00 device name", "00002a00-0000-1000-8000-00805f9b34fb"),
    ("2a01 appearance", "00002a01-0000-1000-8000-00805f9b34fb"),
    ("2a04 preferred conn params", "00002a04-0000-1000-8000-00805f9b34fb"),
    ("2aa6 central addr resolution", "00002aa6-0000-1000-8000-00805f9b34fb"),
]


async def main():
    dev = await find_module(20.0)
    if dev is None:
        print("not advertising before connect attempt")
        return

    async with BleakClient(dev) as client:
        print("connected, reading GAP attributes\n")
        for label, uuid in GAP:
            try:
                v = await client.read_gatt_char(uuid)
                hexs = " ".join(f"{b:02x}" for b in v)
                txt = "".join(chr(b) if 32 <= b < 127 else "." for b in v)
                print(f"  {label:32s} {hexs:24s} |{txt}|")
            except Exception as e:
                print(f"  {label:32s} READ FAILED: {e}")

    print("\ndisconnected; re-scanning 25s to see if it still advertises ...")
    again = await find_module(25.0)
    if again:
        print(f"STILL ADVERTISING: {again.name} -- BLE is reachable without the button dance")
    else:
        print("NOT ADVERTISING -- needs the button dance each time; unusable as a daemon transport")


asyncio.run(main())
