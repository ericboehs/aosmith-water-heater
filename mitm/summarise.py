#!/usr/bin/env python3
"""Summarise a session's flow log: what the module did, and what it reported.

    ./mitm/summarise.py mitm/flows.jsonl          # timeline + counts + telemetry
    ./mitm/summarise.py FLOWS --timeline          # dials and polls only

Written against what the module actually sends, which is three requests and a
reply: a telemetry POST to `superBlockDyn1`, a `commands.xml` poll, an empty
acknowledgement POST, then more telemetry. The numbers below are the offsets
decode in `icomm/README.md`; temperatures are 1/256 °C throughout, shown in °F.
"""

import argparse
import json
import pathlib
import re
import sys

TEMP = 1.8 / 256.0


def fahrenheit(raw: bytes) -> float:
    return int.from_bytes(raw, "big") * TEMP + 32


def blocks(body: str | None) -> list[bytes] | None:
    m = re.search(r"<value>([0-9A-Fa-f,]{8,})</value>", body or "")
    if not m:
        return None
    return [bytes.fromhex(x) for x in m.group(1).split(",")]


def readings(b: list[bytes]) -> dict:
    out = {}
    try:
        out["setpoint"] = fahrenheit(b[3][0:2])
        out["water_top"] = fahrenheit(b[1][0:2])
        out["water_bottom"] = fahrenheit(b[1][4:6])
        out["max_setpoint"] = fahrenheit(b[1][86:88])
        out["volts"] = b[1][99]
        out["mode"] = f"{b[3][30]}/{b[3][31]}"
        out["watts"] = int.from_bytes(b[4][12:14], "big")
        out["air"] = fahrenheit(b[4][30:32])
        out["rssi"] = int.from_bytes(b[4][40:42], "big", signed=True)
    except (IndexError, ValueError):
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="summarise a module session's flow log")
    ap.add_argument("flows", nargs="?", default="mitm/flows.jsonl")
    ap.add_argument("--timeline", action="store_true", help="dials and polls only")
    args = ap.parse_args()

    path = pathlib.Path(args.flows)
    rows = sorted((json.loads(l) for l in path.read_text().splitlines() if l.strip()),
                  key=lambda r: r.get("t", 0))
    if not rows:
        print(f"  {path}: empty")
        return 1
    t0 = rows[0]["t"]

    dials = polls = telemetry = 0
    print(f"  session: {rows[-1]['t'] - t0:.0f}s, {len(rows)} requests\n")
    print("   time    request                          note")
    for r in rows:
        when = f"+{r['t'] - t0:5.0f}s"
        if "commands.xml" in r["path"]:
            polls += 1
            body = r.get("response_body") or ""
            if "setBlockParameter" in body:
                m = re.search(r"<value>([^<]*)</value>", body)
                note = f"served command {m.group(1) if m else ''}"
            elif body.strip():
                note = "served empty <commands>"
            else:
                note = f"held {r.get('duration_ms', 0)/1000:.1f}s, empty"
            print(f"  {when}  POLL    {r['path'][:44]:44} {note}")
        elif "superBlock" in r["path"]:
            telemetry += 1
            print(f"  {when}  TELEM   {'':44} ")
        elif "setBlockParameter" in r["path"]:
            print(f"  {when}  ack     {r['path'][:44]:44} {r['status']}")
        else:
            print(f"  {when}  {r['method']:6}  {r['path'][:60]:60} {r['status']}")

    if not args.timeline:
        print("\n   time    setpoint   water top/bottom     mode    watts  volts  air    rssi")
        for r in rows:
            if "superBlock" not in r["path"]:
                continue
            b = blocks(r.get("request_body"))
            if not b:
                continue
            d = readings(b)
            print(f"  +{r['t'] - t0:5.0f}s  {d.get('setpoint', 0):7.1f} F  "
                  f"{d.get('water_top', 0):7.1f} / {d.get('water_bottom', 0):7.1f} F  "
                  f"{d.get('mode', '?'):>6}  {d.get('watts', 0):5d}  {d.get('volts', 0):4d}  "
                  f"{d.get('air', 0):5.1f} F  {d.get('rssi', 0):4d} dBm")

    print(f"\n  {telemetry} telemetry, {polls} polls, {len(rows)} requests over "
          f"{(rows[-1]['t'] - t0) / 60:.1f} min")
    if polls:
        avg = (rows[-1]["t"] - t0) / polls
        print(f"  one poll per {avg / 60:.1f} min on average — writes are gated on these")
    return 0


if __name__ == "__main__":
    sys.exit(main())
