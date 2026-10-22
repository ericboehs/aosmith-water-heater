#!/usr/bin/env bash
# A long observation session on the module's TLS channel.
#
#   ./mitm/session.sh [minutes] [setpoint ...]
#
# window-watch.sh answers "will it dial, and what does the rejection look like?" —
# it arms the redirect and stops at the first dial. This answers a different
# question: "what does the cloud channel carry over time?" It holds the redirect
# open for the whole session, presses the cloud at intervals so the module
# connects, and leaves one flow log covering every exchange.
#
# Each trigger is a setpoint write through Home Assistant, which is the reliable
# way to make the module dial (it can otherwise sit idle for many minutes):
#
#   ./mitm/session.sh 12 122 121 120        # three writes, three connections
#
# The final setpoint is written back to 120 °F on the way out, and the redirect is
# removed by the EXIT trap whatever happens. Note that while this is armed the
# module's cloud channel goes through the proxy, so the certificate served has to
# be one it accepts — see README: any RSA/SHA-256 leaf works.

set -uo pipefail
cd "$(dirname "$0")/.."

MINUTES=${1:-12}; shift || true
SETPOINTS=("$@")
[ ${#SETPOINTS[@]} -gt 0 ] || SETPOINTS=(122 121 120)
RESTORE=${RESTORE:-120}

OUT=${OUT:-/tmp/session}
MODULE_IP=${MODULE_IP:-10.0.1.187}
PROXY_HOST=${PROXY_HOST:-10.0.1.195}
PROXY_PORT=${PROXY_PORT:-8444}
HA=${HA:-http://10.0.1.113:8123}
UDM=${UDM:-root@10.0.1.1}
MITM_BIN=${MITM_BIN:-$HOME/.local/venvs/mitmproxy-12.3/bin/mitmdump}
FLOWS=${FLOWS:-mitm/flows.jsonl}
# Rules are for fault injection; by default the file exists but is empty, so the
# addon loads and logs without altering any response.
RULES=${RULES:-mitm/rules-none.json}

say() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
remote() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$UDM" "$@"; }
ha() {
  curl -s -m 25 -o /dev/null -w "%{http_code}" -X POST \
    -H "Authorization: Bearer ${HOME_ASSISTANT_TOKEN:?HOME_ASSISTANT_TOKEN is not set}" \
    -H "Content-Type: application/json" \
    -d "{\"entity_id\":\"water_heater.garage\",\"temperature\":$1}" \
    "$HA/api/services/water_heater/set_temperature"
}

cleanup() {
  say "cleanup"
  pkill -f "mitmdump --mode reverse" 2>/dev/null
  sudo pkill -INT -f "tcpdump -i en33 .*$PROXY_PORT" 2>/dev/null
  # udm-redirect.sh cleans up from an EXIT trap but ignores SIGTERM while blocked
  # on ssh, so it is killed outright and its rules are deleted explicitly. The
  # "Killed: 9" this prints is expected, not a failure.
  for pid in $(pgrep -f udm-redirect.sh); do kill -9 "$pid" 2>/dev/null; done
  remote "iptables -t nat -D PREROUTING -s $MODULE_IP -p tcp --dport 443 -m comment --comment icomm-mitm -j DNAT --to-destination $PROXY_HOST:$PROXY_PORT 2>/dev/null
          iptables -t nat -D POSTROUTING -s $MODULE_IP -d $PROXY_HOST -p tcp --dport $PROXY_PORT -m comment --comment icomm-mitm -j SNAT --to-source 10.0.1.1 2>/dev/null" >/dev/null 2>&1
  say "restoring setpoint $RESTORE F (http $(ha "$RESTORE"))"
  left=$(remote "iptables -t nat -S | grep -c icomm-mitm" 2>/dev/null | tr -d '\r')
  say "gateway rules left: ${left:-unknown}"
  [ "${left:-1}" = "0" ] || say "WARNING: rules still armed — remove them by hand"
  say "flows: $(wc -l <"$FLOWS" 2>/dev/null | tr -d ' ')   pcap: $(stat -f%z "$OUT.pcap" 2>/dev/null) bytes"
}
trap cleanup EXIT INT TERM

say "session: ${MINUTES}m, ${#SETPOINTS[@]} trigger(s): ${SETPOINTS[*]}"
"$MITM_BIN" --mode reverse:https://proxy-ingress-usfield.aylanetworks.com@$PROXY_PORT \
  --set confdir=mitm/confdir --set flow_detail=1 \
  --set ciphers_client='DHE-RSA-AES256-SHA:AES128-SHA:AES256-SHA' \
  --set tls_version_client_max=TLS1_2 \
  --set ssl_verify_upstream_trusted_ca=mitm/upstream-entrust-g2.pem \
  -s mitm/ayla-mitm.py --set "ayla_log=$FLOWS" --set "ayla_rules=$RULES" \
  >"$OUT-proxy.log" 2>&1 &
sleep 4

sudo tcpdump -i en33 -n -s0 -w "$OUT.pcap" "tcp port $PROXY_PORT" 2>/dev/null &
sleep 2
PROXY_HOST=$PROXY_HOST PROXY_PORT=$PROXY_PORT SECONDS_TO_RUN=$((MINUTES * 60)) ./mitm/udm-redirect.sh >"$OUT-udm.log" 2>&1 &
sleep 6
armed=$(remote "iptables -t nat -S | grep -c icomm-mitm" | tr -d '\r')
say "armed: $armed rules"
[ "$armed" = "2" ] || { say "redirect did not arm — see $OUT-udm.log"; exit 1; }

# One trigger per setpoint, spaced so each gets its own connection and the
# telemetry that follows it lands in the log.
gap=$(( (MINUTES * 60 - 120) / ${#SETPOINTS[@]} ))
[ "$gap" -lt 30 ] && gap=30
for sp in "${SETPOINTS[@]}"; do
  say "trigger: setpoint $sp F (http $(ha "$sp"))"
  sleep "$gap"
done
sleep 60
say "done"
