#!/usr/bin/env python3
"""Does the PIN change anything the firmware does?

If the PIN is being processed at all, an authenticated link should behave
differently from an unauthenticated one -- most visibly in when the module
hangs up. Send PIN + F2, then sit still and time the disconnect.
"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner
sys.path.insert(0, "/home/ericboehs/ble")
from icomm_ble import crc8, fmt

W = "69400003-b5a3-f393-e0a9-e50e24dcca99"
N = "69400002-b5a3-f393-e0a9-e50e24dcca99"
send_pin = "--no-pin" not in sys.argv

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
        print(f"[{time.monotonic()-t0:5.2f}s] {m}", flush=True)
    gone = asyncio.Event()
    async with BleakClient(dev, disconnected_callback=lambda _: gone.set()) as c:
        t0 = time.monotonic()
        log("connected")
        if send_pin:
            pin = (dev.name or "")[-6:]
            await c.write_gatt_char(W, pin.encode(), response=True)
            log(f"pin {pin} accepted")
            await asyncio.sleep(0.6)
        else:
            log("skipping the pin entirely (control)")
        await c.start_notify(N, lambda _s, d: log(f"NOTIFY {fmt(bytes(d))}"))
        log("subscribed")
        body = bytes([0xBD, 0xF2, 0x05, 0x01])
        await c.write_gatt_char(W, body + bytes([crc8(body)]), response=True)
        log("F2 sent; now idling until the module hangs up")
        try:
            await asyncio.wait_for(gone.wait(), timeout=45)
            log("module terminated the link")
        except asyncio.TimeoutError:
            log("still connected at 45s -- no hangup")

asyncio.run(main())
