#!/usr/bin/env python3
"""Dump everything the module broadcasts, and watch it for change.

We have spent every session trying to make the module answer and never looked
at what it says unprompted. Some comms modules put live state straight into the
advertisement -- manufacturer data or service data -- which would hand us
telemetry with no session at all.

Also records whether anything else BLE is sitting near the tank.
"""
import asyncio, time
from bleak import BleakScanner

def fmt(d):
    return " ".join(f"{b:02X}" for b in d)

async def main():
    seen = {}
    others = {}
    t0 = time.monotonic()

    def cb(dev, adv):
        name = (dev.name or adv.local_name or "")
        rec = (adv.rssi, dict(adv.manufacturer_data), dict(adv.service_data),
               list(adv.service_uuids), adv.tx_power)
        if name.upper().startswith("ICOMM"):
            key = (tuple(sorted((k, bytes(v)) for k, v in rec[1].items())),
                   tuple(sorted((k, bytes(v)) for k, v in rec[2].items())),
                   tuple(rec[3]))
            if key not in seen:
                seen[key] = time.monotonic() - t0
                print(f"[{seen[key]:6.2f}s] {name} rssi={rec[0]} "
                      f"tx_power={rec[4]}", flush=True)
                for cid, val in rec[1].items():
                    print(f"    manufacturer 0x{cid:04X} ({cid}): "
                          f"{fmt(val)}  {bytes(val)!r}", flush=True)
                for uuid, val in rec[2].items():
                    print(f"    service data {uuid}: {fmt(val)}", flush=True)
                print(f"    service uuids: {rec[3] or 'none advertised'}",
                      flush=True)
        elif dev.address not in others:
            others[dev.address] = name or "(unnamed)"

    print("scanning 45s -- watching for advert payload changes ...", flush=True)
    async with BleakScanner(cb):
        await asyncio.sleep(45)

    print(f"\ndistinct iCOMM advert payloads: {len(seen)}", flush=True)
    print(f"other BLE devices in range: {len(others)}", flush=True)
    for addr, nm in list(others.items())[:15]:
        print(f"    {addr}  {nm}")

asyncio.run(main())
