#!/usr/bin/env python3
"""Characterise how this tank responds to Price Stream.

The first probe showed the tank does respond, but ran the phases back to back
so state bled between them. This resets to a flat neutral price and waits for
the tank to settle before each phase, so each result stands alone.

The discriminating phase is "flat dear": a forecast that is uniformly
expensive. If the tank curtails on that, it is judging price against some
internal absolute threshold. If it does nothing, it is judging each price
relative to the rest of the forecast -- which is what actually matters when
deciding whether to feed it real cents-per-kWh or a normalised signal.

Run with the daemon stopped.
"""
import sys
import time

sys.path.insert(0, "/home/ericboehs/cta2045-mqtt")
import cta2045  # noqa: E402

PORT = "/dev/serial0"
NEUTRAL = 12000  # $0.12000/kWh
CHEAP = 1000
DEAR = 99000
SETTLE = 60
OBSERVE = 60


def poll(tank, seconds, label):
    seen = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = tank.operational_state()
        if result:
            seen.append(result[1])
        time.sleep(4)
    counts = {}
    for name in seen:
        counts[name] = counts.get(name, 0) + 1
    summary = ", ".join(f"{n}x{c}" for n, c in sorted(counts.items()))
    print(f"  {label}: {summary}", flush=True)
    return counts


def reset(tank):
    """Flat neutral price plus End Shed, then wait for the tank to settle."""
    now = int(time.time())
    tank.set_price_stream([(now - 3600, NEUTRAL), (now + 43200, NEUTRAL)])
    tank.end_shed()
    tank.heartbeat()
    time.sleep(SETTLE)


def phase(tank, label, pairs):
    reset(tank)
    print(f"\n=== {label} ===", flush=True)
    code = tank.set_price_stream(pairs)
    print(f"  sent: {code} ({cta2045.INTERMEDIATE_RESPONSES.get(code)})",
          flush=True)
    return poll(tank, OBSERVE, "states")


def main():
    tank = cta2045.Tank(PORT)
    try:
        tank.heartbeat()
        now = int(time.time())
        print(f"start: {tank.operational_state()}  "
              f"{cta2045.hot_water_percent(tank.commodity())}% hot", flush=True)

        phase(tank, "cheap now, dear later",
              [(now - 3600, CHEAP), (now + 10800, DEAR)])
        phase(tank, "dear now, cheap later",
              [(now - 3600, DEAR), (now + 10800, CHEAP)])
        phase(tank, "flat dear (absolute-vs-relative test)",
              [(now - 3600, DEAR), (now + 10800, DEAR)])

        print("\n=== restoring ===", flush=True)
        reset(tank)
        print(f"  final: {tank.operational_state()}  "
              f"{cta2045.hot_water_percent(tank.commodity())}% hot", flush=True)
    finally:
        tank.close()


main()
