#!/usr/bin/env bash
# Run the iCOMM TLS interception proxy.
#
#   LEAF=aylanetworks.com.crt KEY=aylanetworks.com.key CHAIN=chain.pem ./run.sh
#
# Listens as a reverse proxy: the module speaks raw TLS to :8443 with no SNI, and
# mitmproxy terminates it with your certificate and opens its own TLS session to
# the real Ayla ingress. Reverse mode is used rather than transparent mode
# because the DNAT happens on the Pi, so the proxy host never sees the original
# destination IP -- and the module sends no SNI to reconstruct it from.
#
# Env:
#   LEAF, KEY, CHAIN   certificate files. CHAIN defaults to the AAA cross-sign,
#                      which chains to a 2002 root every store has; chain-emsign.pem
#                      is the alternative if the module lacks that root.
#   PORT               listen port on this host          (default 8443)
#   UPSTREAM_CA        CA bundle for the mitmproxy -> Ayla leg. Defaults to the
#                      Entrust G2 root Ayla's chain ends at: mitmproxy ships
#                      certifi, which has dropped Entrust, so the upstream leg
#                      fails verification with a perfectly valid server chain.
#   UPSTREAM           real Ayla ingress to forward to
#                      (default proxy-ingress-usfield.aylanetworks.com:443)
#   UI                 dump = mitmdump, web = mitmweb    (default dump)
#   RULES              optional JSON response-injection rules
#   SKIP_CHECK=1       start even if check-cert.sh says no (for capturing the
#                      rejection itself, which is how Phase 4/5 were done)
#   EXTRA              extra mitmproxy flags, word-split (e.g. "--set ssl_insecure=true")
#   MITM_BIN           mitmdump/mitmweb to run (default: whatever is on PATH)
#   EMUDHRA            1 or old: use mitmproxy >= 12.3's --emudhra, which signs
#                      leaves with the recovered EM OV TLS CA - G3C key. Needs no
#                      LEAF/KEY/CHAIN; "old" puts the AAA cross-sign first.

set -euo pipefail
cd "$(dirname "$0")/.."

LEAF=${LEAF:-aylanetworks.com.crt}
KEY=${KEY:-aylanetworks.com.key}
CHAIN=${CHAIN:-mitm/chain-aaa.pem}
PORT=${PORT:-8443}
# A bare host:port means https; give a scheme to override.
UPSTREAM=${UPSTREAM:-proxy-ingress-usfield.aylanetworks.com:443}
case "$UPSTREAM" in *://*) UPSTREAM_URL=$UPSTREAM ;; *) UPSTREAM_URL=https://$UPSTREAM ;; esac
UI=${UI:-dump}
RULES=${RULES:-}
MITM_BIN=${MITM_BIN:-mitmdump}
EMUDHRA=${EMUDHRA:-}
UPSTREAM_CA=${UPSTREAM_CA:-mitm/upstream-entrust-g2.pem}

# The module's own offer, so nothing can be negotiated that it could not do.
SUITES="DHE-RSA-AES256-SHA:AES128-SHA:AES256-SHA"

BUNDLE=
if [ -n "$EMUDHRA" ]; then
  { "$MITM_BIN" --help 2>&1 || true; } | grep -q -- "--emudhra" ||
    { echo "$MITM_BIN has no --emudhra (needs mitmproxy >= 12.3)" >&2; exit 1; }
else
  for f in "$LEAF" "$KEY" ${CHAIN:+"$CHAIN"}; do
    [ -r "$f" ] || { echo "missing $f" >&2; exit 1; }
  done
  if [ "${SKIP_CHECK:-0}" != "1" ]; then
    ./mitm/check-cert.sh "$LEAF" "$KEY" ${CHAIN:+"$CHAIN"}
  fi
  # mitmproxy wants one PEM with the leaf first, then its issuers, then the key.
  BUNDLE=mitm/bundle.pem
  umask 077
  cat "$LEAF" ${CHAIN:+"$CHAIN"} "$KEY" > "$BUNDLE"
  trap 'rm -f "$BUNDLE"' EXIT
fi

args=(
  --mode "reverse:${UPSTREAM_URL}@${PORT}"
  --set "ciphers_client=${SUITES}"
  --set tls_version_client_min=TLS1_2
  --set tls_version_client_max=TLS1_2
  --set flow_detail=2
  --set save_stream_file=mitm/flows.mitm
  -s mitm/ayla-mitm.py
)
[ -n "$BUNDLE" ] && args+=(--set "certs=*=${BUNDLE}")
[ -n "$EMUDHRA" ] && { [ "$EMUDHRA" = old ] && args+=(--emudhra-old) || args+=(--emudhra); }
[ -n "$RULES" ] && args+=(--set "ayla_rules=${RULES}")
[ -n "$UPSTREAM_CA" ] && [ -r "$UPSTREAM_CA" ] &&
  args+=(--set "ssl_verify_upstream_trusted_ca=${UPSTREAM_CA}")
# shellcheck disable=SC2206
[ -n "${EXTRA:-}" ] && args+=(${EXTRA})

cat <<EOF >&2
--- iCOMM mitm
  listen      :${PORT}  (reverse -> ${UPSTREAM})
  cert        :${EMUDHRA:+--emudhra${EMUDHRA:+ }${EMUDHRA}}${BUNDLE:+${LEAF} + ${CHAIN}}
  suites      :${SUITES}
  flows       :mitm/flows.jsonl (readable), mitm/flows.mitm (replayable)
  point the module here from the Pi:
      PROXY_HOST=<this host> PROXY_PORT=${PORT} sudo ./mitm/pi-redirect.sh
EOF

case "$UI" in
  web) exec "$(dirname "$MITM_BIN")/mitmweb" "${args[@]}" 2>/dev/null || exec "$MITM_BIN" "${args[@]}" ;;
  *)   exec "$MITM_BIN" "${args[@]}" ;;
esac
