#!/usr/bin/env python3
"""Full TCP sweep of the module, plus a fresh look at its LAN-mode endpoints.

We concluded LAN mode is unavailable from a single guessed path list on port
80. That is not a scan. This checks all 65535 TCP ports before we call it.

⚠️ Its 400-way concurrency floods the module's embedded stack and reports
*every* port closed, including port 80, which answers fine on its own. The
result is a false negative -- use portscan2.py instead. Kept as the record of
how that conclusion was nearly drawn.
"""
import asyncio, json, os, socket, urllib.request

HOST = os.environ.get("ICOMM_IP", "")
if not HOST:
    raise SystemExit("set ICOMM_IP to the module's LAN address")

def my_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((HOST, 80))
    ip = s.getsockname()[0]
    s.close()
    return ip

async def probe(port, sem):
    async with sem:
        try:
            fut = asyncio.open_connection(HOST, port)
            r, w = await asyncio.wait_for(fut, timeout=2.0)
            w.close()
            try:
                await w.wait_closed()
            except Exception:
                pass
            return port
        except Exception:
            return None

async def main():
    sem = asyncio.Semaphore(400)
    print(f"scanning {HOST} 1-65535 ...", flush=True)
    results = await asyncio.gather(*(probe(p, sem) for p in range(1, 65536)))
    open_ports = [p for p in results if p]
    print(f"open TCP ports: {open_ports}\n", flush=True)

    print("=== regtoken.json ===", flush=True)
    try:
        with urllib.request.urlopen(f"http://{HOST}/regtoken.json", timeout=5) as r:
            print(r.read().decode(), flush=True)
    except Exception as e:
        print(repr(e), flush=True)

    # LAN-mode registration, every verb and spelling Ayla modules have used
    body = json.dumps({"local_reg": {"ip": my_ip(), "port": 8888,
                                     "uri": "/local_lan", "notify": 1}}).encode()
    print("\n=== local registration attempts ===", flush=True)
    for path in ("local_reg.json", "client/local_reg.json", "local_lan.json",
                 "lan.json", "localreg.json"):
        for verb in ("POST", "PUT"):
            req = urllib.request.Request(f"http://{HOST}/{path}", data=body,
                                         headers={"Content-Type": "application/json"},
                                         method=verb)
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    print(f"  {verb} /{path} -> {r.status} {r.read()[:200]}", flush=True)
            except Exception as e:
                code = getattr(e, "code", None)
                if code and code != 404:
                    print(f"  {verb} /{path} -> {code}", flush=True)
    print("  (only non-404 responses shown)", flush=True)

asyncio.run(main())
