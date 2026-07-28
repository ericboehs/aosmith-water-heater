#!/usr/bin/env python3
"""Now that the PIN authenticates, find where the answer comes back.

The link survives well past the 15s watchdog once the PIN is accepted, so we
can afford to try every frame on every pipe in a single connection instead of
burning a pairing window per experiment. Read-only frames plus the two
harmless session queries; nothing here writes tank configuration.
"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner
sys.path.insert(0, "/home/ericboehs/ble")
from icomm_ble import crc8, fmt

PIPES = {
    "6940": ("69400003-b5a3-f393-e0a9-e50e24dcca99",
             "69400002-b5a3-f393-e0a9-e50e24dcca99"),
    "7f51": ("7f510006-b5a3-f393-e0a9-e50e24dcca9e",
             "7f510005-b5a3-f393-e0a9-e50e24dcca9e"),
}

FRAMES = [
    ("F2 list pair keys",  [0xBD, 0xF2, 0x05, 0x01]),
    ("F4 random number",   [0xBD, 0xF4, 0x04]),
    ("A0 read blk26 off0", [0xBD, 0xA0, 0x07, 26, 0, 0x01]),
    ("A0 read blk13 off0", [0xBD, 0xA0, 0x07, 13, 0, 0x01]),
]

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
    gone = asyncio.Event()
    async with BleakClient(dev, disconnected_callback=lambda _: gone.set()) as c:
        t0 = time.monotonic()
        pin = (dev.name or "")[-6:]
        # The PIN goes on the 6940 writer only; that is the pipe the app uses
        # and the one we have now proven satisfies the auth watchdog.
        await c.write_gatt_char(PIPES["6940"][0], pin.encode(), response=True)
        log(f"pin {pin} written")
        await asyncio.sleep(0.6)
        for tag, (_w, n) in PIPES.items():
            await c.start_notify(n, lambda _s, d, t=tag: log(f"  <<< {t} NOTIFY {fmt(bytes(d))}"))
        log("subscribed to both notify pipes")
        for wtag, (w, _n) in PIPES.items():
            for label, body in FRAMES:
                if gone.is_set():
                    log("link dropped, stopping")
                    return
                payload = bytes(body) + bytes([crc8(bytes(body))])
                log(f"{wtag} >>> {label}: {fmt(payload)}")
                try:
                    await c.write_gatt_char(w, payload, response=True)
                except Exception as e:
                    log(f"    write failed: {e}")
                    continue
                await asyncio.sleep(3.0)
        log("sweep done; idling 20s for anything late")
        try:
            await asyncio.wait_for(gone.wait(), timeout=20)
            log("module terminated the link")
        except asyncio.TimeoutError:
            log("still connected")

asyncio.run(main())
