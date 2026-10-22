#!/usr/bin/env python3
"""Silent UDP 55055 listener for the iCOMM session-capture experiment (Pi, root).

Binds UDP :55055 and RECEIVES ONLY. Never replies -- so the kernel generates
no ICMP port-unreachable and the module keeps sending its fresh-session
handshake attempts at us, which we log (hex + ascii) and tcpdump also sees.
Bounded by outer `timeout`.
"""
import datetime
import socket

LOG = "/tmp/handshake_rx.log"

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 55055))
    print("silent55055 bound 0.0.0.0:55055, receive-only", flush=True)
    with open(LOG, "ab", buffering=0) as f:
        while True:
            data, addr = s.recvfrom(4096)
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            line = f"{ts} {addr[0]}:{addr[1]} len={len(data)} hex={data.hex()}\n"
            f.write(line.encode())
            print(line.strip()[:160], flush=True)

main()
