#!/usr/bin/env python3
"""Make a write through A.O. Smith's own GraphQL API, as the app does.

    /tmp/appwrite-venv/bin/python mitm/appwrite.py --list
    /tmp/appwrite-venv/bin/python mitm/appwrite.py --mode electric
    /tmp/appwrite-venv/bin/python mitm/appwrite.py --mode heat_pump
    /tmp/appwrite-venv/bin/python mitm/appwrite.py --setpoint 140

Why this exists: the only uncaptured command frame is the mode write. Home
Assistant's copy of this call hangs on A.O. Smith's retry ladder (six attempts,
20 s each with 10 s between) whenever their API is flaky, and the mobile app
cannot stay logged in alongside HA. This script makes the same GraphQL mutation
the app makes — updateMode / updateSetpoint against https://r2.wh8.co/graphql —
with credentials typed into a getpass prompt: they never appear on the command
line, in a file, or anywhere this repo can see them.

Run it while mitm/session.sh is armed in passthrough mode and every command
Ayla delivers to the module lands in the flow log, byte for byte.

Needs: python3 -m venv /tmp/appwrite-venv && /tmp/appwrite-venv/bin/pip install py-aosmith
"""

import argparse
import asyncio
import getpass
import sys

from py_aosmith import AOSmithAPIClient
from py_aosmith.models import OperationMode

# This tank's DSN, from the module's own regtoken.json — used to pick the right
# device from the account without guessing.
DSN = "AC000W019949045"


async def run(args: argparse.Namespace) -> int:
    email = args.email or input("A.O. Smith app email: ")
    password = getpass.getpass("A.O. Smith app password: ")

    async with AOSmithAPIClient(email, password) as client:
        devices = await client.get_devices()
        ours = next((d for d in devices if d.dsn == DSN), None)
        if ours is None:
            print(f"no device with DSN {DSN} on this account:")
            for d in devices:
                print(f"  {d.name}  dsn={d.dsn}  junction={d.junction_id}")
            return 1

        print(f"  device: {ours.name}  dsn={ours.dsn}")
        print(f"  online: {ours.status.is_online}  mode: {ours.status.mode}"
              f"  setpoint: {ours.status.temperature_setpoint} F")

        if args.list:
            for m in ours.supported_modes:
                print(f"  supported: {m.mode.value} (app name {m.original_name})")
            return 0

        if args.setpoint:
            print(f"  writing setpoint {args.setpoint} F ...")
            await client.update_setpoint(ours.junction_id, args.setpoint)
            print(f"  updateSetpoint accepted — watch the relay for the frame")

        if args.mode:
            mode = OperationMode(args.mode)
            print(f"  writing mode {mode.value} ...")
            await client.update_mode(ours.junction_id, mode)
            print(f"  updateMode accepted — watch the relay for the frame")

        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="write through A.O. Smith's GraphQL API")
    ap.add_argument("--email", help="app account email (prompted if omitted)")
    ap.add_argument("--list", action="store_true", help="list devices and modes, write nothing")
    ap.add_argument("--mode", choices=[m.value for m in OperationMode],
                    help="operation mode to write")
    ap.add_argument("--setpoint", type=int, help="setpoint in F to write")
    args = ap.parse_args()
    if not (args.list or args.mode or args.setpoint):
        ap.print_help()
        return 2
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
