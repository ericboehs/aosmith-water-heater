#!/usr/bin/env bash
# A long observation session on the module's TLS channel.
#
#   ./mitm/session.sh [minutes] [setpoint ...]
#
# window-watch.sh answers "will it dial, and what does the rejection look like?" —
# it arms the redirect and stops at the first dial. This answers "what does the
# cloud channel carry over time?", and, with INJECT set, "does the module accept a
# frame we built?"
#
# What is known now, and why this script looks the way it does:
#
#   * The module is event-driven, not chatty. A gateway capture showed it dialing on
#     a ~60 s cadence while it had state to report, but a six-minute window over a
#     flat tank with nothing queued produced **zero requests**. Without a cloud write
#     a session may capture nothing at all, so a trigger is what guarantees traffic;
#     it is not merely a convenience.
#   * A write is only delivered in reply to a commands.xml poll, and polls are far
#     rarer than dials (1 in 9 min, 0 in 6). An injected command can therefore wait
#     minutes, and `each` ladders step one poll at a time -- size the window to the
#     ladder, not the other way round.
#   * Any RSA-2048/SHA-256 leaf is accepted (see README), so the proxy is a plain
#     reverse proxy on a port of our choosing.
#   * The ops channel can be sniffed only on the gateway's br0; switch0 and eth9 see
#     nothing of this module. OPS=1 captures it for the length of the session.
#
# Usage:
#
#   ./mitm/session.sh 12 122 121 120         # three cloud writes, one flow log
#   INJECT="148 150" ./mitm/session.sh 12    # serve our own frames on each poll
#   OPS=1 RESTORE=150 ./mitm/session.sh 10   # also capture the encrypted ops channel
#   RULES=mitm/rules-set125.json ./mitm/session.sh 5 121   # hand-written rule file
#
# INJECT builds a rule ladder with mitm/frame.py — one frame per poll, last one
# repeating — so a sequence of setpoints needs no hand-written JSON. It takes
# precedence over RULES. The setpoint is written back to $RESTORE (default 150 °F,
# the ceiling for this unit) on the way out, whatever happens.

set -uo pipefail
cd "$(dirname "$0")/.."

MINUTES=${1:-12}; shift || true
SETPOINTS=("$@")
[ ${#SETPOINTS[@]} -gt 0 ] || SETPOINTS=()
RESTORE=${RESTORE:-150}

OUT=${OUT:-/tmp/session}
MODULE_IP=${MODULE_IP:-10.0.1.187}
PROXY_HOST=${PROXY_HOST:-10.0.1.195}
PROXY_PORT=${PROXY_PORT:-8444}
HA=${HA:-http://10.0.1.113:8123}
UDM=${UDM:-root@10.0.1.1}
MITM_BIN=${MITM_BIN:-$HOME/.local/venvs/mitmproxy-12.3/bin/mitmdump}
FLOWS=${FLOWS:-mitm/flows.jsonl}
OPS=${OPS:-0}
INJECT=${INJECT:-}
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

# A ladder rule from frames we encode ourselves. frame.py owns the framing, so a
# change to it cannot silently disagree with the rules built here.
build_ladder() {
  python3 - "$@" <<'PY'
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location("frame", "mitm/frame.py")
frame = importlib.util.module_from_spec(spec); spec.loader.exec_module(frame)
steps = []
for f in sys.argv[1:]:
    wire = frame.encode_setpoint(float(f)).hex(" ").upper()
    steps.append({"status": 200, "headers": {"Content-Type": "application/xml; charset=utf-8"},
                  "body": ("<commands>\n  <properties>\n    <property>\n"
                           "      <base-type>string</base-type>\n      <mime-type></mime-type>\n"
                           f"      <name>setBlockParameter</name>\n      <value>{wire}</value>\n"
                           "    </property>\n  </properties>\n  <schedules/>\n  <cmds/>\n</commands>\n")})
    print(f"  ladder step: {f} F -> {wire}")
pathlib.Path("/tmp/session-ladder.json").write_text(json.dumps(
    [{"match": {"path_regex": "commands\\.xml"}, "each": steps}], indent=2) + "\n")
PY
}

cleanup() {
  say "cleanup"
  pkill -f "mitmdump --mode reverse" 2>/dev/null
  # tcpdump flushes on SIGINT, not on SIGKILL: give it a moment, or the pcap keeps
  # only its 24-byte header and the window looks empty when it was not.
  sudo pkill -INT -f "tcpdump -i en33 .*$PROXY_PORT" 2>/dev/null
  sleep 2
  # udm-redirect.sh cleans up from an EXIT trap but ignores SIGTERM while blocked
  # on ssh, so it is killed outright and its rules are deleted explicitly. The
  # "Killed: 9" this prints is expected, not a failure.
  for pid in $(pgrep -f udm-redirect.sh); do kill -9 "$pid" 2>/dev/null; done
  remote "iptables -t nat -D PREROUTING -s $MODULE_IP -p tcp --dport 443 -m comment --comment icomm-mitm -j DNAT --to-destination $PROXY_HOST:$PROXY_PORT 2>/dev/null
          iptables -t nat -D POSTROUTING -s $MODULE_IP -d $PROXY_HOST -p tcp --dport $PROXY_PORT -m comment --comment icomm-mitm -j SNAT --to-source 10.0.1.1 2>/dev/null" >/dev/null 2>&1
  if [ "$OPS" = "1" ]; then
    remote "pkill -INT -f 'tcpdump -i br0' " >/dev/null 2>&1
    sleep 2
    scp -q -o BatchMode=yes "$UDM":/tmp/session-ops.pcap "$OUT-ops.pcap" 2>/dev/null
    remote "rm -f /tmp/session-ops.pcap" >/dev/null 2>&1
    [ -f "$OUT-ops.pcap" ] && say "ops capture: $(stat -f%z "$OUT-ops.pcap") bytes -> $OUT-ops.pcap"
  fi
  [ -n "${HA_TOKEN_SET:-}" ] && say "restoring setpoint $RESTORE F (http $(ha "$RESTORE"))"
  left=$(remote "iptables -t nat -S | grep -c icomm-mitm" 2>/dev/null | tr -d '\r')
  say "gateway rules left: ${left:-unknown}"
  [ "${left:-1}" = "0" ] || say "WARNING: rules still armed — remove them by hand"
  say "flows: $(wc -l <"$FLOWS" 2>/dev/null | tr -d ' ')   pcap: $(stat -f%z "$OUT.pcap" 2>/dev/null) bytes"
  [ -f mitm/summarise.py ] && ./mitm/summarise.py "$FLOWS" 2>/dev/null | tail -4
}
trap cleanup EXIT INT TERM

[ -n "${HOME_ASSISTANT_TOKEN:-}" ] && HA_TOKEN_SET=1
START=$(date +%s)
if [ -n "$INJECT" ]; then
  say "building ladder from: $INJECT"
  # shellcheck disable=SC2086
  build_ladder $INJECT
  RULES=/tmp/session-ladder.json
fi

if [ ${#SETPOINTS[@]} -gt 0 ]; then
  say "session: ${MINUTES}m, cloud writes: ${SETPOINTS[*]}"
else
  say "session: ${MINUTES}m, no cloud writes (the module dials on its own ~60 s cadence)"
fi

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

if [ "$OPS" = "1" ]; then
  # br0 is the interface that sees this module's VLAN; switch0 and eth9 do not.
  remote "nohup timeout $((MINUTES * 60 + 60)) tcpdump -i br0 -n -s 0 -w /tmp/session-ops.pcap \
          'udp and host $MODULE_IP and not port 53' >/tmp/session-ops.log 2>&1 &" >/dev/null 2>&1
  say "ops capture armed on $UDM:br0"
fi

PROXY_HOST=$PROXY_HOST PROXY_PORT=$PROXY_PORT SECONDS_TO_RUN=$((MINUTES * 60)) ./mitm/udm-redirect.sh >"$OUT-udm.log" 2>&1 &
sleep 6
armed=$(remote "iptables -t nat -S | grep -c icomm-mitm" | tr -d '\r')
say "armed: $armed rules"
[ "$armed" = "2" ] || { say "redirect did not arm — see $OUT-udm.log"; exit 1; }

# DNAT only steers *new* connections. The module holds an established one to real
# Ayla -- measured: six minutes armed with a cloud write and the module never
# reconnected, because it never needed to. Dropping its conntrack entries makes the
# next packet look like a new flow, get DNAT'd mid-stream, and break the connection,
# which is what makes it dial again. Two passes, because the module may have more
# than one flow open.
for _ in 1 2; do
  n=$(remote "conntrack -D -s $MODULE_IP -p tcp --dport 443 2>/dev/null | wc -l" | tr -d '\r')
  say "conntrack flushed: ${n:-0} entry(ies)"
  sleep 3
done

# Cloud writes, spaced so each has time to reach the module and be acknowledged in
# the telemetry. They no longer exist to provoke a dial — the module dials on its
# own — but they are how a real command gets into the channel.
if [ ${#SETPOINTS[@]} -gt 0 ]; then
  gap=$(( (MINUTES * 60 - 120) / ${#SETPOINTS[@]} ))
  [ "$gap" -lt 30 ] && gap=30
  for sp in "${SETPOINTS[@]}"; do
    say "cloud write: setpoint $sp F (http $(ha "$sp"))"
    sleep "$gap"
  done
fi

# Hold the window open to its deadline. cloud writes may have consumed part of it,
# but a session is always exactly MINUTES long so captures are comparable -- and so
# a dial (about one a minute) is guaranteed to land inside it.
deadline=$(( START + MINUTES * 60 ))
while [ "$(date +%s)" -lt "$deadline" ]; do sleep 5; done
say "done"
