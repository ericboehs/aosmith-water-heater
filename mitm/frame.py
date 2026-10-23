#!/usr/bin/env python3
"""Encode and decode the module's block-write frames.

    ./mitm/frame.py 125                     # -> BD 40 08 0B 06 33 AB E2
    ./mitm/frame.py --decode 'BD 40 08 0B 06 30 E4 5E'
    ./mitm/frame.py --decode 'BD 40 08 0B 06 30 E4 5D'   # bad CRC is reported

Frame layout, as carried in Ayla's `setBlockParameter` property:

    BD  <cmd=0x40>  <total_len>  <block>  <offset>  <value...>  <crc8>

`0xBD` is the prefix and `0x40` the block-write opcode recovered over BLE;
`total_len` counts the whole frame including the CRC, matching `BD F2 05 01 D8`
from the BLE side. The setpoint lives at block 0x0B / offset 0x06 as a
big-endian 1/256 °C value, and `crc8` is the table the BLE work already
brute-forced out of the app bundle.

Reproduces the three frames Ayla actually sent, byte for byte:

    122 F -> BD 40 08 0B 06 32 00 56
    121 F -> BD 40 08 0B 06 31 72 28
    120 F -> BD 40 08 0B 06 30 E4 5E

The CRC table is read out of `ble/icomm_ble.py` rather than copied, so there is
one definition. That module imports bleak at the top, which is only available on
the Pi, so the table is evaluated from its source instead of imported.
"""

import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
BLE_MODULE = HERE.parent / "ble" / "icomm_ble.py"

BLOCK_SETPOINT = 0x0B
OFFSET_SETPOINT = 0x06

# The cloud's MODE register, captured 23 October 2026 from Ayla's own deliveries:
# block 11 (the same block as the setpoint, offset 6) at OFFSET 15, two bytes --
# and they are exactly the b[3] 30/31 pair the telemetry reports as mode.
#   electric  -> 64 01  (100, 1)     BD 40 08 0B 0F 64 01 34
#   hybrid    -> 00 04  (0,   4)     BD 40 08 0B 0F 00 04 14
#   heat_pump -> 00 05  (0,   5)     BD 40 08 0B 0F 00 05 4A The BLE map's MODE at block 26 offset 0
# (ELECTRIC=32, HYBRID=16, EFFICIENCY=8) is a DIFFERENT register: over the cloud
# it is acknowledged and ignored -- measured twice.
MODE_BLOCK = 0x0B
MODE_OFFSET = 0x0F
MODES = {"electric": b"\x64\x01", "hybrid": b"\x00\x04", "heat_pump": b"\x00\x05"}


def _crc8():
    src = BLE_MODULE.read_text()
    table = re.search(r"^CRC_TABLE\s*=\s*(\[[^\]]*\])", src, re.S | re.M)
    if not table:
        raise SystemExit(f"no CRC_TABLE in {BLE_MODULE}")
    ns: dict = {}
    exec("CRC_TABLE = " + table.group(1), ns)  # noqa: S102 — a literal list, not code
    crc_table = ns["CRC_TABLE"]

    def crc8(data: bytes) -> int:
        crc = 0
        for b in data:
            crc = crc_table[(crc ^ b) & 0xFF]
        return crc

    return crc8


crc8 = _crc8()


def encode(block: int, offset: int, payload: bytes) -> bytes:
    """Any block write: BD 40 <len> <block> <offset> <payload...> <crc8>."""
    body = bytes([0xBD, 0x40, 0x00, block & 0xFF, offset & 0xFF]) + payload
    body = body[:2] + bytes([len(body) + 1]) + body[3:]   # total_len includes the CRC
    return body + bytes([crc8(body)])


def encode_setpoint(fahrenheit: float) -> bytes:
    q = round((fahrenheit - 32) / 1.8 * 256)
    if not 0 < q <= 0xFFFF:
        raise ValueError(f"{fahrenheit} F is out of range for 1/256 C in 16 bits")
    return encode(BLOCK_SETPOINT, OFFSET_SETPOINT, bytes([q >> 8, q & 0xFF]))


def encode_mode(mode: str, block: int = MODE_BLOCK, offset: int = MODE_OFFSET) -> bytes:
    if mode.lower() not in MODES:
        raise ValueError(f"unknown mode {mode!r}; try {sorted(MODES)}")
    return encode(block, offset, MODES[mode.lower()])


def decode(frame: bytes) -> dict:
    if len(frame) < 5 or frame[0] != 0xBD:
        raise ValueError("not a module frame (expected 0xBD first)")
    declared, actual = frame[2], len(frame)
    payload = frame[3:-1]
    out = {
        "cmd": f"0x{frame[1]:02X}",
        "declared_len": declared,
        "actual_len": actual,
        "length_ok": declared == actual,
        "block": payload[0] if payload else None,
        "offset": payload[1] if len(payload) > 1 else None,
        "crc_ok": crc8(frame[:-1]) == frame[-1],
        "crc": f"0x{frame[-1]:02X}",
    }
    data = payload[2:]
    if len(data) == 2:
        q = int.from_bytes(data, "big")
        out["raw"] = data.hex(" ")
        out["value_1_256_c"] = q
        out["fahrenheit"] = round(q / 256 * 1.8 + 32, 2)
        out["celsius"] = round(q / 256, 3)
    return out


def commands_xml(wire: str) -> str:
    """The poll reply body, matching Ayla's formatting rather than one long line."""
    return ("<commands>\n  <properties>\n    <property>\n      <base-type>string</base-type>\n"
            "      <mime-type></mime-type>\n      <name>setBlockParameter</name>\n"
            f"      <value>{wire}</value>\n    </property>\n  </properties>\n"
            "  <schedules/>\n  <cmds/>\n</commands>\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="encode/decode the module's block-write frames")
    ap.add_argument("setpoint", nargs="?", type=float, help="setpoint in F, to encode")
    ap.add_argument("--decode", metavar="HEX", help="frame to decode, e.g. 'BD 40 08 0B 06 30 E4 5E'")
    ap.add_argument("--mode", metavar="NAME", help=f"mode write frame: {', '.join(sorted(MODES))} (cloud register, block 11 offset 15)")
    ap.add_argument("--block", type=lambda v: int(v, 0), help="with --mode: block number (default 26)")
    ap.add_argument("--xml", action="store_true", help="emit the commands XML body for the inject file")
    ap.add_argument("--offset", type=lambda v: int(v, 0), help="with --mode: offset (default 0)")
    args = ap.parse_args()

    if args.decode:
        frame = bytes.fromhex(re.sub(r"[^0-9A-Fa-f]", "", args.decode))
        for k, v in decode(frame).items():
            print(f"  {k:14} {v}")
        return 0 if decode(frame)["crc_ok"] else 1

    if args.mode:
        kw = {}
        if args.block is not None: kw["block"] = args.block
        if args.offset is not None: kw["offset"] = args.offset
        frame = encode_mode(args.mode, **kw)
        if args.xml:
            print(commands_xml(frame.hex(" ").upper()), end="")
            return 0
        print(f"  mode {args.mode} = {MODES[args.mode.lower()].hex(' ').upper()}  ->  {frame.hex(' ').upper()}")
        print(f"  decoded: {decode(frame)}")
        return 0

    if args.setpoint is None:
        ap.print_help()
        return 2
    frame = encode_setpoint(args.setpoint)
    print(f"  {args.setpoint:g} F  ->  {frame.hex(' ').upper()}")
    print(f"  decoded: {decode(frame)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
