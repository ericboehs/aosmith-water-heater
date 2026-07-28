#!/usr/bin/env python3
"""Passive BLE scan. Prints every advertiser with name, RSSI and service UUIDs,
so we can spot the tank's iCOMM module if it's in range."""
import asyncio
from bleak import BleakScanner

SECS = 20


async def main():
    seen = {}

    def cb(dev, adv):
        seen[dev.address] = (adv.local_name or dev.name, adv.rssi,
                             list(adv.service_uuids), adv.manufacturer_data)

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(SECS)
    await scanner.stop()

    print(f"{len(seen)} devices in {SECS}s\n")
    for addr, (name, rssi, uuids, mfg) in sorted(seen.items(), key=lambda x: -x[1][1]):
        mfgids = ", ".join(f"0x{k:04x}" for k in mfg) if mfg else "-"
        print(f"{rssi:>5} dBm  {addr}  {name or '(no name)'}")
        if uuids:
            print(f"              services: {', '.join(uuids)}")
        if mfg:
            print(f"              mfg ids: {mfgids}")


asyncio.run(main())
