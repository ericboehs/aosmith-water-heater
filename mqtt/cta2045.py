"""CTA-2045 UCM link and application layer for an A.O. Smith HPWH.

Talks to the tank's EcoPort over RS-485 at 19200 8N1, in place of EPRI's
libcea2045. The framing errors that originally motivated writing this were
later root-caused to two processes reading the same tty rather than to that
library, so this exists for being self-contained and debuggable -- no build
step, no C++ toolchain, and a trace hook on every byte -- not because
libcea2045 was shown to be at fault.

Three things about this bus are easy to get wrong and cost real debugging time:

* It is half duplex. Transmitting while the tank is mid-response collides with
  it and destroys the rest of the frame, so a response is collected in silence
  and only acked once a complete frame is in hand.
* The port must be held exclusively. A second process reading the same tty
  silently steals bytes -- the driver reports them received while your own
  read() returns nothing -- so the port is flocked and a stale reader is a
  startup error rather than a mystery.
* Reported power is an estimate, not a measurement, and the tank says so: the
  high bit of the commodity byte is the measured/estimated flag and this tank
  clears it. Values are nameplate constants -- exactly 356 W on the compressor
  across 130 consecutive samples, and 4500 W with an element on, matching the
  two 4500 W elements on the model page. Treat it as a categorical "what is
  running", never as metering.

Wire format: msgType1, msgType2, three reserved zero bits, a 13-bit big-endian
payload length, `length` payload bytes, then a 2-byte checksum. Link acks and
naks are the exception: bare 2-byte messages with no length and no checksum.
"""

import fcntl
import os
import select
import termios
import time

MSG_TYPE_BASIC = (0x08, 0x01)
MSG_TYPE_INTERMEDIATE = (0x08, 0x02)
MSG_TYPE_DATALINK = (0x08, 0x03)

LINK_ACK = 0x06
LINK_NAK = 0x15

# Basic message op codes.
OP_SHED = 0x01
OP_END_SHED = 0x02
OP_APP_ACK = 0x03
OP_APP_NAK = 0x04
OP_POWER_LEVEL = 0x06
OP_PRESENT_RELATIVE_PRICE = 0x07
OP_NEXT_RELATIVE_PRICE = 0x08
OP_CRITICAL_PEAK = 0x0A
OP_GRID_EMERGENCY = 0x0B
OP_COMM_STATUS = 0x0E
OP_CUSTOMER_OVERRIDE = 0x11
OP_OPER_STATE_REQUEST = 0x12
OP_OPER_STATE_RESPONSE = 0x13
OP_LOAD_UP = 0x17

# Intermediate message op codes.
OP_INFO = 0x01
OP_GET_SET = 0x03
OP_COMMODITY = 0x06

SUB_TEMP_OFFSET = 0x02
SUB_TEMP_SETPOINT = 0x03
SUB_PRESENT_TEMPERATURE = 0x04

REPLY_BIT = 0x80

NAK_REASONS = {
    0x00: "no reason",
    0x01: "invalid byte",
    0x02: "invalid length",
    0x03: "checksum error",
    0x05: "message timeout",
    0x06: "unsupported message type",
    0x07: "request not supported",
}

OPERATIONAL_STATES = {
    0: "Idle Normal",
    1: "Running Normal",
    2: "Running Curtailed",
    3: "Running Heightened",
    4: "Idle Curtailed",
    5: "SGD Error",
    6: "Idle Heightened",
    7: "Cycling On",
    8: "Cycling Off",
    9: "Variable Following",
    10: "Variable Not Following",
    11: "Idle Opted Out",
    12: "Running Opted Out",
    # Revision B additions. Never seen from this tank, but it advertises Price
    # Stream support in its capability bitmap, so they are reachable.
    13: "Running Price Stream",
    14: "Idle Price Stream",
}

# States in which the tank is putting heat into the water.
HEATING_STATES = {1, 2, 3, 7, 9, 10, 12, 13}

# Commodity codes. 0x0a/0x0b are Advanced Load Up total and present energy take
# capacity -- distinct from 0x06/0x07, though this tank reports the same values
# for both pairs.
COMMODITY_ELECTRICITY = 0x00
COMMODITY_STORAGE_CAPACITY = 0x06
COMMODITY_STORAGE_DEFICIT = 0x07

# How long this tank tolerates silence before declaring SGD Error, measured:
# Running Curtailed still came back 810.2 s after the last comm-status, and the
# first SGD Error frame arrived at 811.7 s.
#
# The error masks an active event rather than cancelling it -- one heartbeat
# cleared the error and the tank reported Running Curtailed again with the
# 1800 s shed still counting down. Nothing reads this constant; the cadence
# that matters is CTA2045_HEARTBEAT_INTERVAL (300 s), well inside this window
# and inside the 1-5 minute range CTA-2045-B asks for.
SGD_ERROR_TIMEOUT = 811.0

# The length field is 13 bits, so the wire cannot express more than this.
MAX_FRAME_LENGTH = 0x1FFF


def checksum(data):
    """The CTA-2045 two-byte checksum: Fletcher over mod-255 sums, seeded 0xAA."""
    sum1, sum2 = 0xAA, 0x00
    for byte in data:
        sum1 = (sum1 + byte) % 0xFF
        sum2 = (sum2 + sum1) % 0xFF
    first = 255 - ((sum1 + sum2) % 255)
    second = 255 - ((sum1 + first) % 255)
    return bytes([first, second])


def build(msg_type, payload):
    if len(payload) > MAX_FRAME_LENGTH:
        raise ValueError(f"payload {len(payload)} exceeds the 13-bit length field")
    # Byte 2 carries three reserved zero bits and the high 5 bits of the length.
    header = bytes([msg_type[0], msg_type[1],
                    (len(payload) >> 8) & 0x1F, len(payload) & 0xFF])
    body = header + bytes(payload)
    return body + checksum(body)


def basic(op_code1, op_code2=0x00):
    return build(MSG_TYPE_BASIC, [op_code1, op_code2])


def intermediate(op_code1, op_code2=0x00, payload=b""):
    return build(MSG_TYPE_INTERMEDIATE, bytes([op_code1, op_code2]) + bytes(payload))


def encode_duration(seconds):
    """Durations encode as seconds = value squared times two, for 0x01-0xfe.

    The two reserved codes are deliberately unreachable from here: 0x00 means
    the duration is unknown, and 0xff means longer than the byte can express.
    Neither is a duration this daemon can time out on.
    """
    value = int(round((max(seconds, 2) / 2) ** 0.5))
    return max(1, min(254, value))


def decode_duration(value):
    """Seconds, or None for the two codes that are not durations.

    0x00 is "unknown" and 0xff is "longer than representable"; the quadratic
    encoding only covers 0x01-0xfe (2 s to 129,032 s).
    """
    if value in (0x00, 0xFF):
        return None
    return value * value * 2


class ProtocolError(Exception):
    pass


class PortBusy(ProtocolError):
    """Another process holds the serial port."""


class Frame:
    """One decoded message off the wire."""

    def __init__(self, raw):
        self.raw = raw
        self.msg_type = (raw[0], raw[1])
        self.is_link = len(raw) == 2
        self.payload = b"" if self.is_link else raw[4:-2]

    @property
    def is_ack(self):
        return self.is_link and self.raw[0] == LINK_ACK

    @property
    def is_nak(self):
        return self.is_link and self.raw[0] == LINK_NAK

    @property
    def nak_reason(self):
        return NAK_REASONS.get(self.raw[1], f"unknown {self.raw[1]}")

    @property
    def op_code1(self):
        return self.payload[0] if self.payload else None

    @property
    def op_code2(self):
        return self.payload[1] if len(self.payload) > 1 else None

    def __repr__(self):
        return f"<Frame {self.raw.hex(' ')}>"


class SerialPort:
    """A raw 19200 8N1 port, held exclusively. No pyserial dependency."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NDELAY)
        try:
            # Without this a forgotten process on the same tty drains our
            # bytes and every symptom looks like a protocol bug instead.
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(self.fd)
            raise PortBusy(f"{path} is held by another process") from error

        attrs = termios.tcgetattr(self.fd)
        attrs[0] = termios.IGNPAR                                  # iflag
        attrs[1] = 0                                               # oflag
        attrs[2] = (termios.B19200 | termios.CS8
                    | termios.CLOCAL | termios.CREAD)              # cflag
        attrs[3] = 0                                               # lflag
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIFLUSH)

    def write(self, data):
        os.write(self.fd, data)

    def read(self, timeout):
        """Whatever is available within timeout seconds, possibly empty."""
        ready, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
        if not ready:
            return b""
        try:
            return os.read(self.fd, 4096)
        except (BlockingIOError, OSError):
            return b""

    def close(self):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


class Link:
    """Framing, resynchronisation, and link-layer acking."""

    # Give the tank time to release the bus before acking; on half duplex an
    # ack sent too eagerly lands on top of whatever it is still sending.
    TURNAROUND = 0.02

    # The tank does not require us to ack its responses -- it answers happily
    # for hours without one -- and an ack is another chance to talk over it on
    # a half-duplex bus, so acking is off unless a caller asks for it.
    def __init__(self, port, on_trace=None, ack_responses=False):
        self.port = port
        self.buffer = bytearray()
        self.on_trace = on_trace
        self.ack_responses = ack_responses

    def _trace(self, direction, data):
        if self.on_trace:
            self.on_trace(direction, data)

    def send(self, frame):
        self._trace("tx", frame)
        self.port.write(frame)

    def _take_frame(self):
        """Pull one frame off the front of the buffer, resyncing as needed.

        Returns a Frame, or None when more bytes are required. Anything that
        cannot begin a valid frame is dropped a byte at a time until the stream
        lines up again.
        """
        while True:
            if len(self.buffer) < 2:
                return None

            first = self.buffer[0]

            if first in (LINK_ACK, LINK_NAK):
                raw = bytes(self.buffer[:2])
                del self.buffer[:2]
                return Frame(raw)

            if first != MSG_TYPE_BASIC[0]:
                del self.buffer[:1]
                continue

            if len(self.buffer) < 4:
                return None

            # Top 3 bits of byte 2 are reserved and must be zero; a frame that
            # sets them is a resync artifact, not a very long payload.
            if self.buffer[2] & 0xE0:
                del self.buffer[:1]
                continue

            length = ((self.buffer[2] & 0x1F) << 8) | self.buffer[3]
            if length > MAX_FRAME_LENGTH:
                del self.buffer[:1]
                continue

            total = 4 + length + 2
            if len(self.buffer) < total:
                return None

            raw = bytes(self.buffer[:total])
            if checksum(raw[:-2]) != raw[-2:]:
                del self.buffer[:1]
                continue

            del self.buffer[:total]
            return Frame(raw)

    def read_frame(self, timeout):
        """Next frame within timeout seconds, or None.

        Acks only complete application frames, and only after the tank has
        finished sending -- see the half-duplex note in the module docstring.
        """
        deadline = time.monotonic() + timeout
        while True:
            frame = self._take_frame()
            if frame is not None:
                if self.ack_responses and not frame.is_link:
                    time.sleep(self.TURNAROUND)
                    self.send(bytes([LINK_ACK, 0x00]))
                return frame

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None

            chunk = self.port.read(remaining)
            if chunk:
                self._trace("rx", chunk)
                self.buffer.extend(chunk)

    def request(self, frame, timeout=3.0, settle=0.4):
        """Send a request and return its application response frame.

        The link ack that precedes the response is consumed here; a link nak is
        returned so the caller can tell "refused" from "no answer at all".

        The settle at the end matters: the tank may retransmit a response it
        thinks went unheard, and any bytes still in flight when the next
        request goes out would be read as that request's answer. Swallowing
        them here is what keeps consecutive polls from drifting out of step.
        """
        self.buffer.clear()
        self.send(frame)

        deadline = time.monotonic() + timeout
        nak = None
        result = None
        while result is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            response = self.read_frame(remaining)
            if response is None:
                break
            if response.is_ack:
                continue
            if response.is_nak:
                nak = response
                continue
            result = response

        self.drain(settle)
        return result or nak

    def drain(self, duration=0.3):
        """Discard whatever is in flight, so a request starts from silence."""
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self.port.read(deadline - time.monotonic())
        self.buffer.clear()


def parse_operational_state(frame):
    """Return (code, name), or None when the frame is not a state response."""
    if frame is None or len(frame.payload) < 2:
        return None
    if frame.op_code1 != OP_OPER_STATE_RESPONSE:
        return None
    code = frame.payload[1]
    return code, OPERATIONAL_STATES.get(code, f"unknown {code}")


def parse_commodity(frame):
    """Return {code: (instantaneous_rate, cumulative_amount)}.

    The payload is the two op-code bytes, a response-code byte, then 13-byte
    records of commodity code plus two 6-byte big-endian quantities.

    The high bit of the commodity byte is a measured(1)/estimated(0) flag, not
    part of the code, so it is masked off here. This tank reports everything
    estimated, but keying on the raw byte would silently lose a record the day
    it does not.
    """
    if frame is None or len(frame.payload) < 3:
        return {}
    if frame.payload[2] != 0x00:
        return {}
    body = frame.payload[3:]
    records = {}
    for offset in range(0, len(body) - 12, 13):
        records[body[offset] & 0x7F] = (
            int.from_bytes(body[offset + 1:offset + 7], "big"),
            int.from_bytes(body[offset + 7:offset + 13], "big"),
        )
    return records


def hot_water_percent(records):
    """Percent of the tank's storage that is hot, from the commodity records.

    Code 7 is a deficit -- the energy needed to be full again -- so it rises as
    hot water is drawn. Capacity is read from the same poll as the deficit
    rather than cached: 4250 Wh is what this tank currently reports, but 7275 Wh
    was logged earlier, and whether that tracks the front-panel mode has never
    been tested against a deliberate mode change.
    """
    capacity = records.get(COMMODITY_STORAGE_CAPACITY, (0, 0))[1]
    deficit = records.get(COMMODITY_STORAGE_DEFICIT, (0, 0))[1]
    if not capacity:
        return None
    fraction = 1.0 - (deficit / capacity)
    return round(max(0.0, min(1.0, fraction)) * 100, 1)


def parse_setpoint(frame):
    """Return setpoint 1 in the tank's reported unit, or None.

    CTA-2045-B section 11.1.6 lays the response out as opcode 1, opcode 2,
    response code, a 2-byte device type, a units byte, then signed 16-bit
    setpoint 1 and an optional signed setpoint 2. Reading the trailing word
    instead -- which is what this used to do -- returns setpoint 2 whenever the
    device sends one, so the offsets are taken from the front.

    Observed here: 08 02 00 08 03 83 00 00 03 00 00 96 11 12, meaning success,
    device type 0003 (heat-pump water heater), units 00 (Fahrenheit), 150 F.

    Read only on this tank. A correctly framed write, setpoint 2 set to the
    specified "do not change" value 8000, still comes back Command Not
    Implemented, so the refusal is the tank's and not a framing mistake.
    """
    if frame is None or len(frame.payload) < 8:
        return None
    if frame.payload[2] != 0x00:
        return None
    return int.from_bytes(frame.payload[6:8], "big", signed=True)


def parse_device_info(frame):
    """Decode a device-info response into a dict.

    CTA-2045-B section 11.1.1.2 fixes the layout, and this tank's 48-byte
    payload matches it exactly:

        01 81 00 42 00 01 18 00 03 02 0e 00 00 01 80 00
        "HPS10-50H45DV"   NUL-padded to 16
        nine-digit serial NUL-padded to 16

    which is response 0, CTA revision "B", vendor 0x0118, device type 0x0003
    (heat-pump water heater), device revision 2.14, capability bitmap
    0x00000180 -- bits 7 and 8, Price Stream and Get SGD Efficiency Level.

    Anything that is not that shape falls back to scanning for printable ASCII
    runs, which is what this used to do unconditionally. The heuristic happens
    to produce the right model and serial for this frame, but it is not a
    decoder and it gives up the rest of the struct.
    """
    if frame is None:
        return {}
    payload = frame.payload
    if len(payload) >= 48 and payload[2] == 0x00:
        return {
            "cta_revision": chr(payload[3]),
            "vendor": int.from_bytes(payload[5:7], "big"),
            "device_type": int.from_bytes(payload[7:9], "big"),
            "device_revision": f"{payload[9]}.{payload[10]}",
            "capabilities": int.from_bytes(payload[11:15], "big"),
            "model": payload[16:32].split(b"\x00")[0].decode("ascii", "replace"),
            "serial": payload[32:48].split(b"\x00")[0].decode("ascii", "replace"),
        }
    runs, current = [], bytearray()
    for byte in frame.payload:
        if 32 <= byte < 127:
            current.append(byte)
        else:
            if len(current) >= 4:
                runs.append(current.decode("ascii"))
            current = bytearray()
    if len(current) >= 4:
        runs.append(current.decode("ascii"))
    info = {}
    if runs:
        info["model"] = runs[0]
    if len(runs) > 1:
        info["serial"] = runs[1]
    return info


class Tank:
    """Request/response helpers on top of a Link."""

    def __init__(self, port_path, on_trace=None):
        self.port = SerialPort(port_path)
        self.link = Link(self.port, on_trace=on_trace)
        self.link.drain()

    def close(self):
        self.port.close()

    def device_info(self):
        return parse_device_info(
            self.link.request(intermediate(OP_INFO, OP_INFO)))

    def operational_state(self):
        return parse_operational_state(
            self.link.request(basic(OP_OPER_STATE_REQUEST)))

    def commodity(self):
        return parse_commodity(self.link.request(intermediate(OP_COMMODITY)))

    def setpoint(self):
        return parse_setpoint(
            self.link.request(intermediate(OP_GET_SET, SUB_TEMP_SETPOINT)))

    def heartbeat(self):
        """Tell the tank its uplink is healthy, clearing/preventing SGD Error."""
        return self._command(basic(OP_COMM_STATUS, 0x01))

    def shed(self, seconds):
        return self._command(basic(OP_SHED, encode_duration(seconds)))

    def end_shed(self):
        return self._command(basic(OP_END_SHED, 0x00))

    def load_up(self, seconds):
        return self._command(basic(OP_LOAD_UP, encode_duration(seconds)))

    def critical_peak(self, seconds):
        return self._command(basic(OP_CRITICAL_PEAK, encode_duration(seconds)))

    def grid_emergency(self, seconds):
        return self._command(basic(OP_GRID_EMERGENCY, encode_duration(seconds)))

    def _command(self, frame):
        """True when the tank application-acked, rather than merely link-acked."""
        response = self.link.request(frame)
        return bool(response and not response.is_link
                    and response.op_code1 == OP_APP_ACK)
