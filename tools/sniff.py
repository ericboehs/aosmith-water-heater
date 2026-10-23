#!/usr/bin/env python3
"""Listen-only RS-485 sniffer for the CTA-2045 EcoPort bus.

Opens the port, never transmits, and prints every byte chunk and every
well-formed frame it sees. Unlike probe.py this does not reuse Link._take_frame
(which only accepts 0x08xx application frames): it frames *any* message type,
so vendor-proprietary traffic (00xx-05xx, f0xx-ffff) is caught too.

Usage:
    tools/sniff.py [port] [seconds] [--trace]

Run it while triggering cloud actions (setpoint/mode change) to see whether
the iCOMM module shares this bus. Stop the cta2045-mqtt daemon first -- the
port is flocked and a stale reader is a loud startup error, not a mystery.
"""

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.join(_HERE, "..", "mqtt"), os.path.join(_HERE, "..")):
    if os.path.exists(os.path.join(_candidate, "cta2045.py")):
        sys.path.insert(0, _candidate)
        break

import cta2045
from cta2045 import (SerialPort, checksum, LINK_ACK, LINK_NAK, MAX_FRAME_LENGTH,
                     NAK_REASONS, INTERMEDIATE_RESPONSES, OPERATIONAL_STATES)

# Message-type names for the ranges CTA-2045-B Table 6-2 assigns.
def msg_type_name(mt):
    first, second = mt[0], mt[1]
    if first == 0x08:
        return {0x01: "Basic", 0x02: "Intermediate", 0x03: "Data-Link",
                0x04: "Commissioning/Network"}.get(second, f"08xx future")
    if 0x00 <= first <= 0x05:
        return "vendor proprietary"
    if first == 0x06:
        return "reserved (link-ack ambiguity)"
    if first == 0x07:
        return "future"
    if 0x09 <= first <= 0x14:
        return "named pass-through / future"
    if first == 0x15:
        return "reserved (link-nak ambiguity)"
    if 0x16 <= first <= 0xEF:
        return "future"
    return "vendor proprietary"


def take_frame(buf):
    """Pull one frame off the front of the buffer, any message type.

    Returns raw bytes, or None when more bytes are required. Garbage is
    dropped a byte at a time until the stream lines up again.
    """
    while True:
        if len(buf) < 2:
            return None

        first = buf[0]

        if first in (LINK_ACK, LINK_NAK):
            raw = bytes(buf[:2])
            del buf[:2]
            return raw

        if len(buf) < 4:
            return None

        # Top 3 bits of byte 2 are reserved and must be zero.
        if buf[2] & 0xE0:
            del buf[:1]
            continue

        length = ((buf[2] & 0x1F) << 8) | buf[3]
        if length > MAX_FRAME_LENGTH:
            del buf[:1]
            continue

        total = 4 + length + 2
        if len(buf) < total:
            return None

        raw = bytes(buf[:total])
        if checksum(raw[:-2]) != raw[-2:]:
            del buf[:1]
            continue

        del buf[:total]
        return raw


def describe(raw):
    """Human-readable one-liner for a raw frame."""
    if len(raw) == 2 and raw[0] in (LINK_ACK, LINK_NAK):
        if raw[0] == LINK_ACK:
            return "link ACK"
        return f"link NAK ({NAK_REASONS.get(raw[1], raw[1])})"

    mt = (raw[0], raw[1])
    length = ((raw[2] & 0x1F) << 8) | raw[3]
    payload = raw[4:-2]
    op1 = payload[0] if payload else None
    op2 = payload[1] if len(payload) > 1 else None

    parts = [f"type={mt[0]:02x}{mt[1]:02x} ({msg_type_name(mt)})",
             f"len={length}", f"op={op1:02x} {op2:02x}" if op1 is not None else "op=-"]

    # Decode the things we already understand, when they apply.
    if mt == (0x08, 0x01) and op1 == cta2045.OP_OPER_STATE_RESPONSE and len(payload) >= 2:
        code = payload[1]
        parts.append(f"state={OPERATIONAL_STATES.get(code, code)}")
    if mt == (0x08, 0x02) and op1 is not None and len(payload) >= 3 and payload[1] & 0x80:
        code = payload[2]
        parts.append(f"resp={INTERMEDIATE_RESPONSES.get(code, code)}")

    parts.append(f"payload={payload.hex(' ')}")
    return "  ".join(parts)


def main():
    port_path = "/dev/serial0"
    duration = None
    trace = "--trace" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        port_path = args[0]
    if len(args) > 1:
        duration = float(args[1])

    started = time.monotonic()

    def stamp():
        return f"+{time.monotonic() - started:8.2f}s"

    port = SerialPort(port_path)
    buffer = bytearray()

    print(f"{stamp()}  listening on {port_path} (listen-only, no TX)"
          + (f", {duration}s" if duration else ", until killed"),
          flush=True)

    deadline = None if duration is None else time.monotonic() + duration
    while True:
        remaining = 1.0 if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            break

        chunk = port.read(remaining)
        if chunk:
            if trace:
                print(f"{stamp()}  rx        {chunk.hex(' ')}", flush=True)
            buffer.extend(chunk)

        # Drain every complete frame now in the buffer.
        while True:
            raw = take_frame(buffer)
            if raw is None:
                break
            print(f"{stamp()}  <<< {raw.hex(' ')}", flush=True)
            print(f"{stamp()}      {describe(raw)}", flush=True)

    port.close()
    print(f"{stamp()}  done", flush=True)


if __name__ == "__main__":
    main()
