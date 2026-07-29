#!/usr/bin/env python3
"""Sweep the 7F51 console's 3-character command space.

'AT?' answers 'AT'; 'AT' alone is invalidCMD and trailing junk after 'AT?' is
ignored. So the parser dispatches on exactly three characters. Two sweeps:
'AT' + every printable byte, and every letter pair + '?'. Read-only probes --
nothing here writes tank configuration.
"""
import asyncio, string, time
from bleak import BleakClient, BleakScanner

W = "7f510006-b5a3-f393-e0a9-e50e24dcca9e"
N = "7f510005-b5a3-f393-e0a9-e50e24dcca9e"
BAD = b"ERROR:invalidCMD\n"

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

    async with BleakClient(dev) as c:
        await c.write_gatt_char("69400003-b5a3-f393-e0a9-e50e24dcca99",
                                pin.encode(), response=True)
        await asyncio.sleep(0.6)
        q = asyncio.Queue()
        await c.start_notify(N, lambda _s, d: q.put_nowait(bytes(d)))

        async def probe(tok):
            while not q.empty():
                q.get_nowait()
            await c.write_gatt_char(W, tok, response=True)
            try:
                return await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                return None

        hits = []
        # sweep 1: AT + every printable byte
        for ch in range(0x20, 0x7F):
            tok = b"AT" + bytes([ch])
            r = await probe(tok)
            if r != BAD:
                hits.append((tok, r))
                print(f"  HIT {tok!r} -> {r!r}", flush=True)
        print(f"sweep 1 done ({len(hits)} hits)", flush=True)

        # sweep 2: every uppercase letter pair + '?'
        for a in string.ascii_uppercase:
            for b in string.ascii_uppercase:
                tok = (a + b + "?").encode()
                if tok == b"AT?":
                    continue
                r = await probe(tok)
                if r != BAD:
                    hits.append((tok, r))
                    print(f"  HIT {tok!r} -> {r!r}", flush=True)
        print(f"total hits: {len(hits)}", flush=True)
        for tok, r in hits:
            print(f"  {tok!r} -> {r!r}")

asyncio.run(main())
