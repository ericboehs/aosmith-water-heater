#!/usr/bin/env python3
"""Decode the TLS handshake of a captured interception window.

    tcpdump -i en33 -n -s0 -w /tmp/win.pcap 'port 8444'
    ./mitm/tls-window.py /tmp/win.pcap

Reads a libpcap file (no scapy, no dpkt) and prints every TLS record with its
direction, record type, handshake message type and length. That is enough to
answer the question the proxy's own log cannot: which handshake messages the
module *received*, and whether it ever answered with an alert or a
ClientKeyExchange.

Record types:   20 change_cipher_spec  21 alert  22 handshake  23 application_data
Handshake types: 1 ClientHello  2 ServerHello  11 Certificate  12 ServerKeyExchange
                13 CertificateRequest  14 ServerHelloDone  16 ClientKeyExchange
                20 Finished  4 NewSessionTicket  0 hello_request
"""

import struct
import sys

HANDSHAKE = {
    0: "hello_request", 1: "ClientHello", 2: "ServerHello", 4: "NewSessionTicket",
    11: "Certificate", 12: "ServerKeyExchange", 13: "CertificateRequest",
    14: "ServerHelloDone", 15: "CertificateVerify", 16: "ClientKeyExchange",
    20: "Finished",
}
ALERTS = {0: "close_notify", 10: "unexpected_message", 20: "bad_record_mac",
          40: "handshake_failure", 42: "bad_certificate", 43: "unsupported_certificate",
          44: "certificate_revoked", 45: "certificate_expired", 46: "certificate_unknown",
          47: "illegal_parameter", 48: "unknown_ca", 49: "access_denied",
          51: "decrypt_error", 70: "protocol_version", 71: "insufficient_security",
          80: "internal_error", 86: "inappropriate_fallback", 90: "user_canceled",
          109: "missing_extension", 112: "unrecognized_name", 116: "certificate_required"}
RECORD = {20: "change_cipher_spec", 21: "alert", 22: "handshake", 23: "app_data"}


def packets(path):
    with open(path, "rb") as fh:
        magic = fh.read(4)
        if magic == b"\xd4\xc3\xb2\xa1":
            end = "<"
        elif magic == b"\xa1\xb2\xc3\xd4":
            end = ">"
        elif magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"):
            raise SystemExit("pcapng; re-capture with -w or use 'tcpdump -r … -w' to convert")
        else:
            raise SystemExit("not a libpcap file")
        fh.read(16)  # version, thiszone, sigfigs, snaplen
        network = struct.unpack(end + "I", fh.read(4))[0]
        while True:
            hdr = fh.read(16)
            if len(hdr) < 16:
                return
            sec, usec, incl, _orig = struct.unpack(end + "IIII", hdr)
            yield sec + usec / 1_000_000, fh.read(incl), network


def tcp(linktype, frame):
    """Return (src, sport, dst, dport, payload, flags) or None."""
    if linktype == 1:            # DLT_EN10MB, what tcpdump writes for en*
        if frame[12:14] != b"\x08\x00":
            return None
        o = 14
    elif linktype in (0, 108):   # DLT_NULL / DLT_LOOP, what tcpdump writes for lo0
        o = 4
    else:
        raise SystemExit(f"unsupported link type {linktype}; capture on an Ethernet or loopback interface")
    ip = frame[o:]
    if len(ip) < 20 or ip[0] >> 4 != 4 or ip[9] != 6:
        return None
    ihl = (ip[0] & 0x0F) * 4
    src, dst = ".".join(map(str, ip[12:16])), ".".join(map(str, ip[16:20]))
    t = ip[ihl:]
    sport, dport = struct.unpack(">HH", t[:4])
    off = (t[12] >> 4) * 4
    return src, sport, dst, dport, t[off:], t[13]


def main(path):
    """Reassemble each direction and print the TLS records in order.

    Records are not aligned to segments: a server flight of ServerHello +
    Certificate + ServerKeyExchange + ServerHelloDone is routinely split across
    TCP segments, so parsing per segment produces garbage for everything after
    the first boundary.
    """
    t0 = None
    streams = {}      # (src, sport) -> bytes buffered
    encrypted = set()
    for ts, frame, linktype in packets(path):
        got = tcp(linktype, frame)
        if not got:
            continue
        src, sport, dst, dport, data, flags = got
        t0 = t0 or ts
        when, who = f"{ts - t0:7.3f}", f"{src}:{sport} -> {dst}:{dport}"
        if flags & 0x02:
            print(f"{when}  {who}  SYN")
            continue
        if flags & 0x04:
            print(f"{when}  {who}  RST")
            continue
        if not data:
            continue
        buf = streams.get((src, sport), b"") + data
        i, parts = 0, []
        while i + 5 <= len(buf):
            rtype = buf[i]
            rlen = int.from_bytes(buf[i + 3:i + 5], "big")
            if rtype not in RECORD or i + 5 + rlen > len(buf):
                break                                  # incomplete: wait for more
            body = buf[i + 5:i + 5 + rlen]
            if (src, sport) in encrypted:
                parts.append(f"{rtype}/{rlen}B encrypted")
            else:
                name = RECORD[rtype]
                if rtype == 22 and len(body) >= 4:
                    name += "/" + HANDSHAKE.get(body[0], f"handshake{body[0]}")
                    if body[0] == 11 and len(body) >= 7:
                        name += f"({int.from_bytes(body[4:7], 'big')} B of certs)"
                elif rtype == 21 and len(body) >= 2:
                    name += f"/{ALERTS.get(body[1], body[1])} (level {body[0]})"
                parts.append(name)
            i += 5 + rlen
        streams[(src, sport)] = buf[i:]
        left = streams[(src, sport)]
        tail = f"  [+{len(left)}B buffered]" if left else ""
        print(f"{when}  {who}  {len(data):5d} B  " + (" | ".join(parts) if parts else f"{data[:8].hex(' ')} ...") + tail)
        if any(p.startswith("change_cipher_spec") for p in parts):
            encrypted.add((src, sport))                # everything after CCS is ciphertext


if __name__ == "__main__":
    main(sys.argv[1])
