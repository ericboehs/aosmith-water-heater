#!/usr/bin/env python3
"""Is the module indicating rather than notifying?

69400002 declares Notify only, so BlueZ writes CCCD 0100 and will drop an
incoming Handle Value Indication. Firmware that indicates anyway would look
exactly like our silence. Force 0200 and 0300 on the descriptor and retry F2;
also retry with write-without-response, which we only ever tried before the
PIN was authenticating.
"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner
sys.path.insert(0, "/home/ericboehs/ble")
from icomm_ble import crc8, fmt

W6 = "69400003-b5a3-f393-e0a9-e50e24dcca99"
N6 = "69400002-b5a3-f393-e0a9-e50e24dcca99"
CCCD = 0x0013
F2 = bytes([0xBD, 0xF2, 0x05, 0x01])
FRAME = F2 + bytes([crc8(F2)])

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
    t0 = time.monotonic()
    def log(m):
        print(f"[{time.monotonic()-t0:6.2f}s] {m}", flush=True)
    async with BleakClient(dev) as c:
        t0 = time.monotonic()
        await c.write_gatt_char(W6, (dev.name or "")[-6:].encode(), response=True)
        log("pin written")
        await asyncio.sleep(0.6)
        box = []
        await c.start_notify(N6, lambda _s, d: log(f"  <<< NOTIFY {fmt(bytes(d))}") or box.append(d))
        log("subscribed (BlueZ wrote 0100)")

        for value, label in ((b"\x02\x00", "indicate"), (b"\x03\x00", "notify+indicate")):
            try:
                await c.write_gatt_descriptor(CCCD, value)
                log(f"CCCD forced to {value.hex()} ({label})")
            except Exception as e:
                log(f"CCCD {label} write refused: {e}")
                continue
            box.clear()
            await c.write_gatt_char(W6, FRAME, response=True)
            log(f"  F2 sent under {label}")
            await asyncio.sleep(4)
            if not box:
                log(f"  nothing under {label}")

        log("retrying F2 write-WITHOUT-response, authenticated")
        box.clear()
        try:
            await c.write_gatt_char(W6, FRAME, response=False)
            await asyncio.sleep(4)
            if not box:
                log("  nothing")
        except Exception as e:
            log(f"  failed: {e}")

asyncio.run(main())
