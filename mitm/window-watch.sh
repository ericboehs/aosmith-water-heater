#!/usr/bin/env bash
# Watch for the module's next TLS dial and capture it end to end.
#
#   ./mitm/window-watch.sh [minutes] [outdir]
#
# The module dials on its own schedule — it can be idle for many minutes, and
# bursts of 11 dials in 3 minutes only happen while it is failing and retrying.
# So instead of arming a short window and hoping, this arms the gateway redirect,
# captures continuously, and stops as soon as a dial passes through the DNAT
# rule, keeping the exchange that follows.
#
# Cleans up after itself whatever happens: gateway rules removed, tcpdump
# stopped, proxy left as the caller started it.

set -euo pipefail
cd "$(dirname "$0")/.."

MINUTES=${1:-40}
OUT=${2:-/tmp/window}
UDM=${UDM:-root@10.0.1.1}
MODULE_IP=${MODULE_IP:-10.0.1.187}
PROXY_HOST=${PROXY_HOST:-10.0.1.195}
PROXY_PORT=${PROXY_PORT:-8444}
PCAP=$OUT.pcap
LOG=$OUT.log

mkdir -p "$(dirname "$OUT")"; : >"$LOG"
say() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }

remote() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$UDM" "$@"; }

cleanup() {
  say "cleanup"
  sudo pkill -INT -f "tcpdump -i en33 .*port $PROXY_PORT" 2>/dev/null || true
  for pid in $(pgrep -f udm-redirect.sh); do kill -9 "$pid" 2>/dev/null || true; done
  remote "iptables -t nat -D PREROUTING -s $MODULE_IP -p tcp --dport 443 -m comment --comment icomm-mitm -j DNAT --to-destination $PROXY_HOST:$PROXY_PORT 2>/dev/null
          iptables -t nat -D POSTROUTING -s $MODULE_IP -d $PROXY_HOST -p tcp --dport $PROXY_PORT -m comment --comment icomm-mitm -j SNAT --to-source 10.0.1.1 2>/dev/null" >/dev/null 2>&1 || true
  sleep 1
  say "gateway rules left: $(remote "iptables -t nat -S | grep -c icomm-mitm" 2>/dev/null | tr -d '\r')"
}
trap cleanup EXIT INT TERM

say "pcap        $PCAP"
say "watching    $MODULE_IP tcp/443 for ${MINUTES}m"

sudo tcpdump -i en33 -n -s0 -w "$PCAP" "tcp port $PROXY_PORT" 2>/dev/null &
sleep 2

PROXY_HOST=$PROXY_HOST PROXY_PORT=$PROXY_PORT SECONDS_TO_RUN=$((MINUTES * 60)) \
  ./mitm/udm-redirect.sh >"$OUT.udm" 2>&1 &
sleep 5
say "armed: $(remote "iptables -t nat -S | grep -c icomm-mitm" | tr -d '\r') rules"

deadline=$(( $(date +%s) + MINUTES * 60 ))
seen=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  n=$(remote "iptables -t nat -L PREROUTING -v -n | awk '/icomm-mitm/{print \$1}'" 2>/dev/null | tr -d '\r')
  if [ -n "$n" ] && [ "$n" != 0 ] && [ "$n" -gt "$seen" ]; then
    say "dial: $n packets through DNAT"
    seen=$n
    sleep 60          # let the TLS exchange, its failure and any retries land
    break
  fi
  sleep 10
done

if [ "$seen" -eq 0 ]; then say "no dial within ${MINUTES}m"; else say "captured"; fi
sudo pkill -INT -f "tcpdump -i en33 .*port $PROXY_PORT" 2>/dev/null || true
sleep 2
say "pcap bytes  $(stat -f%z "$PCAP" 2>/dev/null || echo 0)"
