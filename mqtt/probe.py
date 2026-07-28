#!/usr/bin/env python3
"""Send a handful of CTA-2045 requests and print every frame that comes back.

Diagnostic tool: no MQTT, no daemon, just the wire. Pass --trace to see the
raw byte chunks as well as the decoded frames.
"""

import sys
import time

import cta2045
from cta2045 import Link, SerialPort


def main():
    port_path = "/dev/serial0"
    trace = "--trace" in sys.argv
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            port_path = arg

    started = time.monotonic()

    def stamp():
        return f"+{time.monotonic() - started:6.2f}s"

    def on_trace(direction, data):
        if trace:
            print(f"{stamp()}  {direction:8} {data.hex(' ')}")

    port = SerialPort(port_path)
    link = Link(port, on_trace=on_trace)
    link.drain(0.5)

    requests = [
        ("device info",       cta2045.intermediate(cta2045.OP_INFO, cta2045.OP_INFO)),
        ("comm status found", cta2045.basic(cta2045.OP_COMM_STATUS, 0x01)),
        ("operational state", cta2045.basic(cta2045.OP_OPER_STATE_REQUEST, 0x00)),
        ("commodity",         cta2045.intermediate(cta2045.OP_COMMODITY, 0x00)),
        ("setpoint",          cta2045.intermediate(cta2045.OP_GET_SET,
                                                   cta2045.SUB_TEMP_SETPOINT)),
        ("present temp",      cta2045.intermediate(cta2045.OP_GET_SET,
                                                   cta2045.SUB_PRESENT_TEMPERATURE)),
    ]

    for label, request in requests:
        print(f"\n{stamp()}  === {label} ===")
        print(f"{stamp()}  >>> {request.hex(' ')}")
        link.send(request)

        deadline = time.monotonic() + 3.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            frame = link.read_frame(remaining)
            if frame is None:
                break
            if frame.is_link:
                kind = "ACK" if frame.raw[0] == cta2045.LINK_ACK else "NAK"
                detail = "" if kind == "ACK" else \
                    f" ({cta2045.NAK_REASONS.get(frame.raw[1], frame.raw[1])})"
                print(f"{stamp()}  <<< link {kind}{detail}")
                continue
            print(f"{stamp()}  <<< {frame.raw.hex(' ')}")
            print(f"{stamp()}      type={frame.msg_type[0]:02x}{frame.msg_type[1]:02x} "
                  f"op={frame.op_code1:02x} {frame.op_code2:02x} "
                  f"payload={frame.payload.hex(' ')}")

        time.sleep(0.5)

    port.close()


if __name__ == "__main__":
    main()
