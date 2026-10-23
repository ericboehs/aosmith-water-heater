#!/usr/bin/env bash
# Block (or clear) the iCOMM module's encrypted operations channel.
#
# The module is told that a command is waiting over UDP 55055-55057 to
# ans-field.aylanetworks.com, not over the HTTPS ingress. Measured consequence:
# with that channel working it polls commands.xml only when a command actually
# exists, so a local stand-in for the cloud cannot push a write until it can also
# prompt a poll.
#
# Blocking it answers the question that matters for a local-only setup: does the
# module fall back to polling on a timer, or does it go quiet?
#
#   ./mitm/ops-block.sh arm        # add the DROP rules
#   ./mitm/ops-block.sh clear      # remove them (always run this)
#   ./mitm/ops-block.sh status
#
# Rules are tagged `icomm-ops` so they are greppable and cannot be confused with
# the DNAT/SNAT rules the session scripts arm (tagged `icomm-mitm`).

set -uo pipefail

UDM=${UDM:-root@10.0.1.1}
MODULE_IP=${MODULE_IP:-10.0.1.187}
PORTS=${PORTS:-55055:55057}

ssh_udm() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$UDM" "$@"; }

case "${1:-status}" in
  arm)
    ssh_udm "iptables -C FORWARD -s $MODULE_IP -p udp --dport $PORTS -m comment --comment icomm-ops -j DROP 2>/dev/null || \
             iptables -I FORWARD 1 -s $MODULE_IP -p udp --dport $PORTS -m comment --comment icomm-ops -j DROP"
    echo "armed: $(ssh_udm "iptables -S FORWARD | grep -c icomm-ops") rule(s) dropping UDP $PORTS for $MODULE_IP"
    ;;
  clear)
    ssh_udm 'while iptables -D FORWARD -s '"$MODULE_IP"' -p udp --dport '"$PORTS"' -m comment --comment icomm-ops -j DROP 2>/dev/null; do :; done'
    echo "cleared: $(ssh_udm "iptables -S FORWARD | grep -c icomm-ops") rule(s) left"
    ;;
  status)
    echo "icomm-ops rules: $(ssh_udm "iptables -S FORWARD | grep -c icomm-ops")"
    ;;
  *)
    echo "usage: $0 arm|clear|status" >&2; exit 2 ;;
esac
