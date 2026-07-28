#!/usr/bin/env python3
"""Poke the tank by hand: read status, or start and stop demand-response events.

Goes through the same locking SerialPort the daemon uses, so running this while
the daemon is up fails loudly instead of silently stealing its bytes. That
failure mode -- two readers on one tty, the driver reporting bytes received
while your own read() returns nothing -- is exactly what makes this bus look
haunted, so it is worth the lock.

    tank-cli.py status
    tank-cli.py watch [interval] [count]
    tank-cli.py loadup|shed|critical|emergency [minutes]
    tank-cli.py normal
"""

import os
import sys
import time

# Works from the repo (tools/ beside mqtt/) and from the flat layout the Pi
# gets deployed into (tools/ beside the module itself).
_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.join(_HERE, "..", "mqtt"), os.path.join(_HERE, "..")):
    if os.path.exists(os.path.join(_candidate, "cta2045.py")):
        sys.path.insert(0, _candidate)
        break

import cta2045
from cta2045 import Tank

EVENTS = {
    "loadup": "load_up",
    "shed": "shed",
    "critical": "critical_peak",
    "emergency": "grid_emergency",
}


def show(tank):
    state = tank.operational_state()
    records = tank.commodity()
    capacity = records.get(cta2045.COMMODITY_STORAGE_CAPACITY, (0, 0))[1]
    deficit = records.get(cta2045.COMMODITY_STORAGE_DEFICIT, (0, 0))[1]
    percent = cta2045.hot_water_percent(records)
    nominal = records.get(cta2045.COMMODITY_ELECTRICITY, (0, 0))[0]
    print(f"{time.strftime('%H:%M:%S')}  "
          f"{(state[1] if state else '?'):<19} "
          f"{nominal:5d} W nominal   "
          f"deficit {deficit:5d}/{capacity or 0} Wh   "
          f"hot water {percent if percent is not None else '?'}%",
          flush=True)


def main():
    args = sys.argv[1:] or ["status"]
    action = args[0]

    try:
        tank = Tank(os.environ.get("CTA2045_PORT", "/dev/serial0"))
    except cta2045.PortBusy as error:
        print(f"{error}\nStop the daemon first: sudo systemctl stop cta2045-mqtt")
        return 1

    try:
        # Any command is pointless if the tank has decided its uplink is dead.
        tank.heartbeat()

        if action == "status":
            print(tank.device_info())
            print(f"setpoint: {tank.setpoint()} F (read only on this tank)")
            show(tank)

        elif action == "watch":
            interval = float(args[1]) if len(args) > 1 else 30.0
            count = int(args[2]) if len(args) > 2 else 10
            last_beat = time.monotonic()
            for _ in range(count):
                if time.monotonic() - last_beat > 300:
                    tank.heartbeat()
                    last_beat = time.monotonic()
                show(tank)
                time.sleep(interval)

        elif action == "normal":
            print("end shed:", "accepted" if tank.end_shed() else "refused")
            time.sleep(10)
            show(tank)

        elif action in EVENTS:
            minutes = float(args[1]) if len(args) > 1 else 30.0
            accepted = getattr(tank, EVENTS[action])(minutes * 60)
            print(f"{action} {minutes:g} min:",
                  "accepted" if accepted else "refused")
            time.sleep(10)
            show(tank)

        else:
            print(__doc__)
            return 2
    finally:
        tank.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
