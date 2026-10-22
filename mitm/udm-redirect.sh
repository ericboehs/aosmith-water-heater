#!/usr/bin/env bash
# UDM Pro side of the redirect: send the module's tcp/443 to the proxy host.
#
#   PROXY_HOST=10.0.1.195 PROXY_PORT=8444 ./mitm/udm-redirect.sh
#
# This is the in-path method that worked, after ARP poisoning stopped taking:
# the gateway rewrites the destination, so the module's packets arrive addressed
# to the proxy host itself and no layer-2 trickery is involved.
#
# DNAT alone is not enough. The proxy is on the same LAN, so it would answer the
# module directly and the gateway would never see the return packets to undo the
# translation; the module would then get a SYN-ACK from the wrong address. The
# SNAT rule makes the gateway the peer the proxy replies to.
#
# Everything is applied with a comment tag and removed by the EXIT trap; nothing
# else on the gateway is touched. While this is armed the module's cloud channel
# is broken by design — the module cannot complete TLS to the proxy unless the
# certificate is one it accepts (see README: it currently is not).

set -euo pipefail

UDM=${UDM:-root@10.0.1.1}
MODULE_IP=${MODULE_IP:-10.0.1.187}
PROXY_HOST=${PROXY_HOST:?set PROXY_HOST to the machine running mitm/run.sh}
PROXY_PORT=${PROXY_PORT:-8444}
TAG=icomm-mitm
SECONDS_TO_RUN=${SECONDS_TO_RUN:-900}

remote() { ssh -o BatchMode=yes "$UDM" "$@"; }

cleanup() {
  echo "--- removing gateway rules" >&2
  remote "iptables -t nat -D PREROUTING  -s $MODULE_IP -p tcp --dport 443 -m comment --comment $TAG -j DNAT --to-destination $PROXY_HOST:$PROXY_PORT 2>/dev/null || true
          iptables -t nat -D POSTROUTING -s $MODULE_IP -d $PROXY_HOST -p tcp --dport $PROXY_PORT -m comment --comment $TAG -j SNAT --to-source 10.0.1.1 2>/dev/null || true
          echo \"remaining $TAG rules: \$(iptables -t nat -S | grep -c $TAG)\"" >&2
}
trap cleanup EXIT INT TERM

remote "iptables -t nat -D PREROUTING  -s $MODULE_IP -p tcp --dport 443 -m comment --comment $TAG -j DNAT --to-destination $PROXY_HOST:$PROXY_PORT 2>/dev/null || true
        iptables -t nat -D POSTROUTING -s $MODULE_IP -d $PROXY_HOST -p tcp --dport $PROXY_PORT -m comment --comment $TAG -j SNAT --to-source 10.0.1.1 2>/dev/null || true
        iptables -t nat -I PREROUTING  1 -s $MODULE_IP -p tcp --dport 443 -m comment --comment $TAG -j DNAT --to-destination $PROXY_HOST:$PROXY_PORT
        iptables -t nat -I POSTROUTING 1 -s $MODULE_IP -d $PROXY_HOST -p tcp --dport $PROXY_PORT -m comment --comment $TAG -j SNAT --to-source 10.0.1.1
        iptables -t nat -S | grep $TAG"

cat >&2 <<EOF
--- armed on $UDM: $MODULE_IP tcp/443 -> $PROXY_HOST:$PROXY_PORT (SNAT via 10.0.1.1)
--- the module must open a NEW connection; its TLS channel is event-driven, so
    poke the cloud to make it dial (a property write through Home Assistant works):
      POST /api/services/water_heater/set_temperature  {"entity_id":"water_heater.garage","temperature":121}
--- window: ${SECONDS_TO_RUN}s, then the rules are removed
EOF

sleep "$SECONDS_TO_RUN"
echo "--- window over" >&2
