#!/usr/bin/env bash
# Pi side: put this Pi between the iCOMM module and its gateway, then hand the
# module's TCP/443 to the interception proxy. Run on the tank Pi as root.
#
#   PROXY_HOST=10.0.1.195 PROXY_PORT=8443 sudo ./mitm/pi-redirect.sh
#
# Everything is applied in dedicated iptables chains and undone by the EXIT trap,
# including the ARP restore, so Ctrl-C leaves the network as it was found. Bounded
# by REDIRECT_SECS as a second line of defence.
#
# Note: this matches on **destination port 443**, not on a resolved Ayla IP. The
# ingress pool behind *-usfield.aylanetworks.com rotates mid-session (Phase 2), so
# a DNAT bound to one address silently stops matching. The module has no other
# TCP/443 peer.

set -euo pipefail
cd "$(dirname "$0")/.."   # icomm/arpspoof.py is addressed relative to the repo root

IFACE=${IFACE:-wlan0}
MODULE_IP=${MODULE_IP:-10.0.1.187}
PROXY_HOST=${PROXY_HOST:?set PROXY_HOST to the machine running mitm/run.sh}
PROXY_PORT=${PROXY_PORT:-8443}
REDIRECT_SECS=${REDIRECT_SECS:-900}

# nft-backed iptables validates a target against the hook it will run from, so
# DNAT and SNAT cannot share a user chain: each gets one, jumped from its own
# built-in chain. Mixing them fails with "RULE_INSERT failed (Invalid argument)".
PRE_CHAIN=ICOMM_MITM_PRE     # nat PREROUTING: DNAT the module's 443 to the proxy
POST_CHAIN=ICOMM_MITM_POST   # nat POSTROUTING: SNAT the hairpin back to this Pi
FWD_CHAIN=ICOMM_MITM_FWD
SELF_IP=$(ip -4 -o addr show dev "$IFACE" | awk '{print $4}' | cut -d/ -f1 | head -1)
SPOOF_PID=

[ -r icomm/arpspoof.py ] || { echo "run from a repo checkout: icomm/arpspoof.py missing" >&2; exit 1; }
if [ "$(id -u)" -ne 0 ]; then
  echo "run as root" >&2
  exit 1
fi
[ -n "$SELF_IP" ] || { echo "no IPv4 address on $IFACE" >&2; exit 1; }

cleanup() {
  echo "--- restoring" >&2
  iptables -t nat -D PREROUTING -j "$PRE_CHAIN" 2>/dev/null || true
  iptables -t nat -D POSTROUTING -j "$POST_CHAIN" 2>/dev/null || true
  for c in "$PRE_CHAIN" "$POST_CHAIN"; do
    iptables -t nat -F "$c" 2>/dev/null || true
    iptables -t nat -X "$c" 2>/dev/null || true
  done
  iptables -D FORWARD -j "$FWD_CHAIN" 2>/dev/null || true
  iptables -F "$FWD_CHAIN" 2>/dev/null || true
  iptables -X "$FWD_CHAIN" 2>/dev/null || true
  if [ -n "$SPOOF_PID" ] && kill -0 "$SPOOF_PID" 2>/dev/null; then
    # SIGTERM, not KILL: arpspoof.py's atexit restore re-sends the true MACs.
    kill -TERM "$SPOOF_PID" 2>/dev/null || true
    wait "$SPOOF_PID" 2>/dev/null || true
  fi
  echo "--- restored. verify: curl -s http://$MODULE_IP/regtoken.json" >&2
}
trap cleanup EXIT INT TERM

sysctl -qw net.ipv4.ip_forward=1
sysctl -qw "net.ipv4.conf.${IFACE}.send_redirects=0"
sysctl -qw net.ipv4.conf.all.send_redirects=0

iptables -t nat -N "$PRE_CHAIN"
iptables -t nat -F "$PRE_CHAIN"
iptables -t nat -A "$PRE_CHAIN" -s "$MODULE_IP" -p tcp --dport 443 \
  -j DNAT --to-destination "${PROXY_HOST}:${PROXY_PORT}"
iptables -t nat -I PREROUTING 1 -j "$PRE_CHAIN"

# Hairpin: when the proxy is on this same subnet it would answer the module
# directly, bypassing the return path, so the reverse translation never happens.
iptables -t nat -N "$POST_CHAIN"
iptables -t nat -F "$POST_CHAIN"
iptables -t nat -A "$POST_CHAIN" -s "$MODULE_IP" -d "$PROXY_HOST" -p tcp \
  --dport "$PROXY_PORT" -j SNAT --to-source "$SELF_IP"
iptables -t nat -I POSTROUTING 1 -j "$POST_CHAIN"

iptables -N "$FWD_CHAIN"
iptables -F "$FWD_CHAIN"
iptables -A "$FWD_CHAIN" -s "$MODULE_IP" -d "$PROXY_HOST" -p tcp --dport "$PROXY_PORT" -j ACCEPT
iptables -A "$FWD_CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A "$FWD_CHAIN" -j RETURN
iptables -I FORWARD 1 -j "$FWD_CHAIN"

echo "--- this Pi: $SELF_IP on $IFACE; module $MODULE_IP -> ${PROXY_HOST}:${PROXY_PORT}" >&2
echo "--- DNAT rules hit only on new connections (conntrack), so the module may" >&2
echo "    take up to ~60s to re-dial. Watch for a fresh SYN:" >&2
echo "    tcpdump -ni $IFACE -c 5 'host $MODULE_IP and tcp port 443'" >&2

python3 icomm/arpspoof.py "$IFACE" &
SPOOF_PID=$!

# Bounded run. sleep is interruptible so Ctrl-C reaches the trap.
deadline=$((SECONDS + REDIRECT_SECS))
while [ $SECONDS -lt $deadline ] && kill -0 "$SPOOF_PID" 2>/dev/null; do
  sleep 5
done
echo "--- redirect window over" >&2
