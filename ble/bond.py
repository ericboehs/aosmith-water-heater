#!/usr/bin/env python3
"""Bond with the module before talking to it.

Every attempt so far used a plain unencrypted link. But the button on the tank
is labelled Bluetooth *pairing*, and a peripheral that requires an encrypted
link before it will notify behaves exactly like this one: writes accepted at
ATT (the write characteristic carries no security requirement), CCCD accepted
by the local stack, and not one notification ever emitted. The NotPermitted we
got from a hand-written CCCD points the same direction.

So: bond first, then run the ordinary authenticated session.
"""
import asyncio, time
from bleak import BleakClient, BleakScanner

W = "69400003-b5a3-f393-e0a9-e50e24dcca99"
N = "69400002-b5a3-f393-e0a9-e50e24dcca99"

TABLE = [0,94,188,226,38,120,154,196,76,18,240,174,106,52,214,136,
152,198,36,122,190,224,2,92,212,138,104,54,242,172,78,16,
110,48,210,140,72,22,244,170,34,124,158,192,4,90,184,230,
246,168,74,20,208,142,108,50,186,228,6,88,156,194,32,126,
220,130,96,62,250,164,70,24,144,206,44,114,182,232,10,84,
68,26,248,166,98,60,222,128,8,86,180,234,46,112,146,204,
178,236,14,80,148,202,40,118,254,160,66,28,216,134,100,58,
42,116,150,200,12,82,176,238,102,56,218,132,64,30,252,162,
230,184,90,4,192,158,124,34,170,244,22,72,140,210,48,110,
126,32,194,156,88,6,228,186,50,108,142,208,20,74,168,246,
136,214,52,106,174,240,18,76,196,154,120,38,226,188,94,0,
16,78,172,242,54,104,138,212,92,2,224,190,122,36,198,152,
58,100,134,216,28,66,160,254,118,40,202,148,80,14,236,178,
162,252,30,64,132,218,56,102,238,176,82,12,200,150,116,42,
84,10,232,182,114,44,206,144,24,70,164,250,62,96,130,220,
204,146,112,46,234,180,86,8,128,222,60,98,166,248,26,68]

def crc8(d):
    c = 0
    for b in d:
        c = TABLE[(c ^ b) & 0xFF]
    return c

def frame(*vals):
    body = bytes([0xBD]) + bytes(vals)
    return body + bytes([crc8(body)])

def fmt(d):
    return " ".join(f"{b:02X}" for b in d)

async def main():
    dev = None
    for _ in range(4):
        for d in await BleakScanner.discover(timeout=12.0):
            if d.name and d.name.upper().startswith("ICOMM"):
                dev = d
                break
        if dev:
            break
    if not dev:
        print("not advertising")
        return
    name = dev.name
    pin = name[15:len(name)-4] if len(name) > 21 else name[-6:]
    print(f"found {name} at {dev.address}", flush=True)

    t0 = time.monotonic()
    def log(m):
        print(f"[{time.monotonic()-t0:6.2f}s] {m}", flush=True)

    async with BleakClient(dev) as c:
        log("connected; requesting bond")
        try:
            ok = await c.pair()
            log(f"pair() -> {ok}")
        except Exception as e:
            log(f"pair() raised: {type(e).__name__}: {e}")

        props = c._backend._properties
        log(f"paired={props.get('Paired')} bonded={props.get('Bonded')} "
            f"trusted={props.get('Trusted')}")

        await c.write_gatt_char(W, pin.encode(), response=True)
        await asyncio.sleep(0.6)
        q = asyncio.Queue()
        await c.start_notify(N, lambda _s, d: q.put_nowait(bytes(d)))
        log("pin sent, subscribed")

        for label, payload in (("F2", frame(0xF2, 0x05, 0x01)),
                               ("F4", frame(0xF4, 0x04)),
                               ("A0 read blk26 off0",
                                frame(0xA0, 0x07, 0x1A, 0x00, 0x01))):
            while not q.empty():
                q.get_nowait()
            log(f"{label}: -> {fmt(payload)}")
            await c.write_gatt_char(W, payload, response=True)
            try:
                r = await asyncio.wait_for(q.get(), timeout=4.0)
                log(f"    <<< {fmt(r)}")
            except asyncio.TimeoutError:
                log("    (silence)")

asyncio.run(main())
