#!/usr/bin/env python3
"""Does the 6940 command dispatcher answer on the *other* service's pipe?

Everything so far assumed request on 69400003 -> reply on 69400002. Both
services descend from the same vendor UART base UUID, and 7F51 demonstrably
notifies (its console answers in ~60ms), so the reply may simply be routed
there. This subscribes to both notify characteristics at once and sends each
frame down each write characteristic, logging whichever pipe speaks.
"""
import asyncio, time
from bleak import BleakClient, BleakScanner

W6940 = "69400003-b5a3-f393-e0a9-e50e24dcca99"
N6940 = "69400002-b5a3-f393-e0a9-e50e24dcca99"
W7F51 = "7f510006-b5a3-f393-e0a9-e50e24dcca9e"
N7F51 = "7f510005-b5a3-f393-e0a9-e50e24dcca9e"

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

def frame(text):
    body = bytes([0xBD] + [int(t, 16) for t in text.split()])
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
    print(f"found {name} at {dev.address}")

    t0 = time.monotonic()
    def log(m):
        print(f"[{time.monotonic()-t0:6.2f}s] {m}", flush=True)

    async with BleakClient(dev) as c:
        heard = []
        def mk(tag):
            def cb(_s, data):
                heard.append((tag, bytes(data)))
                log(f"    <<< {tag}: {fmt(data)}  {bytes(data)!r}")
            return cb

        log("connected; writing PIN on 6940")
        await c.write_gatt_char(W6940, pin.encode(), response=True)
        await asyncio.sleep(0.6)

        await c.start_notify(N6940, mk("6940"))
        await c.start_notify(N7F51, mk("7f51"))
        log("subscribed to BOTH notify pipes")

        async def shot(label, char, payload, wait=3.0):
            n = len(heard)
            log(f"{label}: -> {fmt(payload)} on {char[:8]}")
            try:
                await c.write_gatt_char(char, payload, response=True)
            except Exception as e:
                log(f"    write failed: {e}")
                return
            await asyncio.sleep(wait)
            if len(heard) == n:
                log(f"    (silence)")

        # positive control: the console is known to answer
        await shot("console control", W7F51, b"AT?", 2.0)

        # the real question: F2 out one pipe, listening on both
        await shot("F2 via 6940", W6940, frame("F2 05 01"))
        await shot("F2 via 7f51", W7F51, frame("F2 05 01"))
        await shot("read via 7f51", W7F51, frame("A0 07 1A 00 01"))

        # does an authenticated console expose more than AT?
        for probe in (b"AT?", b"AT+HELP", b"AT+VER", b"AT+NAME", b"?"):
            await shot(f"console {probe!r}", W7F51, probe, 1.2)

        log(f"done; {len(heard)} notifications total")

asyncio.run(main())
