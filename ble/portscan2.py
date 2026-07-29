#!/usr/bin/env python3
"""Gentle TCP scan. The module's stack stops accepting under any real load.

A 400-way concurrent sweep made even port 80 look closed, so this walks the
interesting ports two at a time with a pause between, and re-checks port 80
periodically to prove the target is still answering.
"""
import asyncio, os, urllib.request

HOST = os.environ.get("ICOMM_IP", "")
if not HOST:
    raise SystemExit("set ICOMM_IP to the module's LAN address")
PORTS = list(range(1, 1025)) + [
    1883, 1900, 3000, 4000, 5000, 5001, 5353, 6000, 7000, 8000, 8001, 8008,
    8009, 8080, 8081, 8083, 8086, 8443, 8880, 8883, 8888, 9000, 9090, 9999,
    10000, 10001, 18183, 41314, 49152, 49153, 55555,
]

async def probe(port):
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(HOST, port), 1.5)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False

def alive():
    try:
        with urllib.request.urlopen(f"http://{HOST}/regtoken.json", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False

async def main():
    print(f"target alive before scan: {alive()}", flush=True)
    found = []
    for i in range(0, len(PORTS), 2):
        batch = PORTS[i:i + 2]
        res = await asyncio.gather(*(probe(p) for p in batch))
        for p, ok in zip(batch, res):
            if ok:
                found.append(p)
                print(f"  open {p}", flush=True)
        await asyncio.sleep(0.06)
        if i and i % 200 == 0:
            print(f"  ... {i}/{len(PORTS)} (alive={alive()})", flush=True)
    print(f"\nopen TCP ports: {found}", flush=True)
    print(f"target alive after scan: {alive()}", flush=True)

asyncio.run(main())
