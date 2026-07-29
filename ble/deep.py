#!/usr/bin/env python3
"""Five things we never tried on 6940, in one authenticated connection.

1. Negotiate the MTU. Every run so far sat at 23 while the app asks for 500.
2. Speak ASCII at 6940. It already parses ASCII -- that is what the PIN is --
   so the console grammar may live on this pipe too.
3. Write without response. We have only ever used write-with-response.
4. Wait much longer after the PIN before the first frame.
5. Sweep the frame's start byte. BD is the app's, but this firmware's may not be.

Read-only: the sweep uses F2 (list keys) and A0 (read), never 40 or 07.
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
    print(f"found {name}", flush=True)

    t0 = time.monotonic()
    def log(m):
        print(f"[{time.monotonic()-t0:6.2f}s] {m}", flush=True)

    async with BleakClient(dev) as c:
        # (1) force the MTU exchange the app performs
        try:
            await c._acquire_mtu()
        except Exception as e:
            log(f"mtu acquire failed: {e}")
        log(f"connected, mtu={c.mtu_size}")

        ch = c.services.get_characteristic(W)
        log(f"6940 write props: {ch.properties}")

        await c.write_gatt_char(W, pin.encode(), response=True)
        await asyncio.sleep(0.6)

        q = asyncio.Queue()
        await c.start_notify(N, lambda _s, d: q.put_nowait(bytes(d)))
        log("subscribed")

        async def shot(label, payload, resp=True, wait=2.5):
            while not q.empty():
                q.get_nowait()
            try:
                await c.write_gatt_char(W, payload, response=resp)
            except Exception as e:
                log(f"{label}: write failed: {e}")
                return None
            try:
                r = await asyncio.wait_for(q.get(), timeout=wait)
                log(f"{label}: -> {fmt(payload)}   <<< {fmt(r)}  {r!r}")
                return r
            except asyncio.TimeoutError:
                return None

        f2 = bytes([0xBD, 0xF2, 0x05, 0x01])
        f2 = f2 + bytes([crc8(f2)])

        log("--- 1: baseline F2 at negotiated MTU")
        await shot("F2/resp", f2)

        log("--- 2: ASCII on 6940")
        for tok in (b"AT?", b"AT?\r\n", b"?", b"AT\r\n", b"ATZ", b"help\n"):
            await shot(f"ascii {tok!r}", tok, wait=1.5)

        log("--- 3: write WITHOUT response")
        if "write-without-response" in ch.properties:
            await shot("F2/noresp", f2, resp=False)
        else:
            log("not supported by the characteristic")

        log("--- 4: long settle after the PIN")
        await asyncio.sleep(12)
        await shot("F2 after +12s", f2, wait=4.0)

        log("--- 5: start-byte sweep (256 probes)")
        hits = 0
        for b0 in range(256):
            body = bytes([b0, 0xF2, 0x05, 0x01])
            r = await shot(f"start {b0:02X}", body + bytes([crc8(body)]), wait=0.35)
            if r:
                hits += 1
        log(f"sweep done, {hits} responses")

asyncio.run(main())
