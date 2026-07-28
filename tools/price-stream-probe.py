#!/usr/bin/env python3
"""Find out whether this tank actually acts on Price Stream, or just accepts it.

The audit established that the tank advertises Price Stream (capability bit 7)
and accepts the section 11.8.3.3 "no valid prices" message. Accepting is not
acting. This sends real forecasts and watches the operational state for 13
(Running Price Stream) or 14 (Idle Price Stream), which are the only
externally visible evidence that prices changed the tank's mind.

Run with the daemon stopped -- the port is flocked.
"""
import sys
import time

sys.path.insert(0, "/home/ericboehs/cta2045-mqtt")
import cta2045  # noqa: E402

PORT = "/dev/serial0"
CHEAP = 1000     # $0.01000/kWh
DEAR = 99000     # $0.99000/kWh
SETTLE = 45      # seconds to watch after each stream


def state(tank):
    result = tank.operational_state()
    return result if result else (None, "no response")


def watch(tank, label, seconds=SETTLE):
    print(f"  watching {seconds}s ...", flush=True)
    seen = {}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        code, name = state(tank)
        seen[code] = name
        time.sleep(5)
    print(f"  {label}: states seen {sorted(k for k in seen if k is not None)}"
          f" -- {', '.join(sorted(set(seen.values())))}", flush=True)
    return seen


def send(tank, label, pairs):
    print(f"\n=== {label} ===", flush=True)
    for when, price in pairs:
        print(f"  {time.strftime('%H:%M', time.localtime(when))}"
              f"  {price / 100000:.5f}/kWh", flush=True)
    code = tank.set_price_stream(pairs)
    print(f"  response: {code} "
          f"({cta2045.INTERMEDIATE_RESPONSES.get(code, '?')})", flush=True)
    return code


def main():
    tank = cta2045.Tank(PORT)
    try:
        print(f"max payload: {tank.max_payload()} bytes", flush=True)
        print(f"accepted pairs: {tank.accepted_pairs()}", flush=True)
        tank.heartbeat()

        code, name = state(tank)
        print(f"baseline state: {code} {name}", flush=True)
        print(f"hot water: {cta2045.hot_water_percent(tank.commodity())}%",
              flush=True)

        now = int(time.time())

        # Cheap now, expensive later: the tank should want to heat now.
        send(tank, "cheap now, dear in 3h",
             [(now - 3600, CHEAP), (now + 10800, DEAR)])
        watch(tank, "cheap-now")

        tank.heartbeat()

        # Expensive now, cheap later: the tank should want to wait.
        send(tank, "dear now, cheap in 3h",
             [(now - 3600, DEAR), (now + 10800, CHEAP)])
        watch(tank, "dear-now")

        print("\n=== restoring ===", flush=True)
        code = tank.invalidate_prices()
        print(f"  invalidate: {code} "
              f"({cta2045.INTERMEDIATE_RESPONSES.get(code, '?')})", flush=True)
        tank.heartbeat()
        code, name = state(tank)
        print(f"  final state: {code} {name}", flush=True)
        print(f"  hot water: {cta2045.hot_water_percent(tank.commodity())}%",
              flush=True)
    finally:
        tank.close()


main()
