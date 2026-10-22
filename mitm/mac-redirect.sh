#!/usr/bin/env bash
# Proxy side of a gateway-side redirect: catch the module's packets here.
#
# When the gateway routes the module's traffic at us by next hop (UniFi Policy
# Engine, "Route" action), the packets still carry the *Ayla* destination IP --
# routing does not rewrite the header. This host is therefore asked to accept
# connections addressed to somebody else, which is what pf's rdr is for.
#
#   SOURCE=10.0.1.187 ./mitm/mac-redirect.sh    # window open until Ctrl-C
#   SOURCE=10.0.1.168 ./mitm/mac-redirect.sh    # to test from the Pi instead
#
# Undone on exit. Only affects tcp/443 from SOURCE arriving on IFACE.

set -euo pipefail
IFACE=${IFACE:-en33}
SOURCE=${SOURCE:-10.0.1.187}
PROXY_PORT=${PROXY_PORT:-8444}
ANCHOR=com.apple/icomm-mitm   # com.apple/* is already anchored in the stock pf.conf

[ "$(id -u)" -eq 0 ] || exec sudo -n "$0" "$@"

cleanup() {
  echo "--- removing pf rules" >&2
  pfctl -a "$ANCHOR" -F all 2>/dev/null || true
}
trap cleanup EXIT INT TERM

pfctl -a "$ANCHOR" -F all 2>/dev/null || true
pfctl -a "$ANCHOR" -f - <<RULES
rdr pass on $IFACE inet proto tcp from $SOURCE to any port 443 -> 127.0.0.1 port $PROXY_PORT
RULES
pfctl -e >/dev/null 2>&1 || true

echo "--- redirecting tcp/443 from $SOURCE on $IFACE -> 127.0.0.1:$PROXY_PORT" >&2
echo "--- active rules:" >&2
pfctl -a "$ANCHOR" -s rules 2>/dev/null | sed 's/^/    /' >&2

while :; do sleep 5; done
