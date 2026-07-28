#!/usr/bin/env python3
"""Find the 7f51 console's command grammar by treating its error as an oracle.

It replies ERROR:invalidCMD to anything it does not recognise, so any other
reply -- a different error, a value, silence -- means the token matched. Probe
prefixes and single characters first to learn the shape before guessing words.

Deliberately excludes anything that could write, reset, erase or format.
"""
import asyncio, sys, time
from bleak import BleakClient, BleakScanner

W6 = "69400003-b5a3-f393-e0a9-e50e24dcca99"
W7 = "7f510006-b5a3-f393-e0a9-e50e24dcca9e"
N7 = "7f510005-b5a3-f393-e0a9-e50e24dcca9e"
BORING = "ERROR:invalidCMD"

PROBES = (
    [""]
    + [chr(c) for c in range(ord("a"), ord("z") + 1)]
    + [chr(c) for c in range(ord("A"), ord("Z") + 1)]
    + [str(d) for d in range(10)]
    + ["AT", "AT+", "AT?", "at+", "$", "#", "!", "*", "+++", "--", "-",
       "/", "\\", ".", ":", ";", "@", "%", "^", "&", "~", "|",
       "get", "getVersion", "getInfo", "getStatus", "getDsn", "getMac",
       "getName", "getState", "getConfig", "getMode", "getAll",
       "cmd", "cmdList", "listCmd", "showCmd", "helpCmd", "invalidCMD",
       "CMD", "cmd?", "ver?", "id?", "test", "ping", "echo", "hello"]
)

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
    hits, quiet = [], []
    async with BleakClient(dev) as c:
        await c.write_gatt_char(W6, (dev.name or "")[-6:].encode(), response=True)
        await asyncio.sleep(0.6)
        box = []
        await c.start_notify(N7, lambda _s, d: box.append(bytes(d)))
        print("authenticated; probing", len(PROBES), "tokens", flush=True)
        for p in PROBES:
            box.clear()
            try:
                await c.write_gatt_char(W7, (p + "\n").encode(), response=True)
            except Exception as e:
                print(f"  {p!r} write failed: {e}", flush=True)
                continue
            await asyncio.sleep(0.35)
            txt = b"".join(box).decode("ascii", "replace").strip()
            if not box:
                quiet.append(p)
            elif txt != BORING:
                hits.append((p, txt))
                print(f"  HIT {p!r} -> {txt!r}", flush=True)
    print(f"\n{len(hits)} distinct replies, {len(quiet)} silent")
    if quiet:
        print("silent:", quiet)

asyncio.run(main())
