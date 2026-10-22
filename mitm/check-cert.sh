#!/usr/bin/env bash
# Gate check for an iCOMM interception certificate.
#
# The module's TLS stack offers four suites and all of them authenticate with an
# RSA key. A leaf certificate with an ECDSA key therefore cannot be served to it
# at all: OpenSSL answers NO_SHARED_CIPHER and the device never sees a cert.
# This script says whether a given cert/key pair can be negotiated with that
# exact cipher offer, and what the module still has to accept about the chain.
#
#   ./check-cert.sh aylanetworks.com.crt aylanetworks.com.key [chain.pem]
#
# Exit 0 = negotiable with the module's offer. Exit 1 = it cannot be used.

set -euo pipefail

# The ClientHello captured from the module (icomm/README.md, Phase 3).
SUITES="DHE-RSA-AES256-SHA:AES128-SHA:AES256-SHA"

crt=${1:?usage: check-cert.sh <leaf.crt> <leaf.key> [chain.pem]}
key=${2:?usage: check-cert.sh <leaf.crt> <leaf.key> [chain.pem]}
chain=${3:-}

fail=0
note() { printf '  %s\n' "$*"; }
good() { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; fail=1; }
warn() { printf '  warn  %s\n' "$*"; }

echo "cert: $crt"
echo "key:  $key"

[ -r "$crt" ] || { echo "cannot read $crt" >&2; exit 1; }
[ -r "$key" ] || { echo "cannot read $key" >&2; exit 1; }

# --- leaf ---------------------------------------------------------------
subj=$(openssl x509 -in "$crt" -noout -subject | sed 's/^subject=*//')
issuer=$(openssl x509 -in "$crt" -noout -issuer | sed 's/^issuer=*//')
sigalg=$(openssl x509 -in "$crt" -noout -text | awk '/Signature Algorithm/{print $3; exit}')
keyalg=$(openssl x509 -in "$crt" -noout -text | awk '/Public Key Algorithm/{print $NF; exit}')
keybits=$(openssl x509 -in "$crt" -noout -text | awk '/Public-Key:/{gsub(/[()]/,"",$2); print $2; exit}')
san=$(openssl x509 -in "$crt" -noout -ext subjectAltName 2>/dev/null | tail -n +2 | tr -d ' ')

note "subject:   $subj"
note "issuer:    $issuer"
note "sig alg:   $sigalg"
note "key:       $keyalg ${keybits:+($keybits bit)}"
note "SAN:       ${san:-<none>}"

case "$keyalg" in
  rsaEncryption|rsa) good "RSA leaf key — one of the module's suites can use it" ;;
  id-ecPublicKey)
    bad "ECDSA leaf key — the module offers no ECDSA suite (no ECDHE at all)"
    note "the module's four suites: DHE-RSA-AES256-SHA, RSA-AES128-SHA,"
    note "RSA-AES256-SHA, renegotiation_info. All four need an RSA server key."
    note "re-issue the leaf with an RSA-2048 key, same SAN." ;;
  *) warn "unexpected key algorithm: $keyalg" ;;
esac

case "$sigalg" in
  *ecdsa*) warn "leaf is ECDSA-signed; the module must be able to verify ECDSA"
           note "signatures up the chain. A stack with no ECC cannot, even if the" 
           note "leaf key itself were RSA." ;;
  sha256WithRSAEncryption|sha384WithRSAEncryption|sha512WithRSAEncryption) good "RSA-signed leaf" ;;
esac

case "$san" in
  *aylanetworks.com*) good "SAN covers aylanetworks.com" ;;
  *) bad "SAN does not cover aylanetworks.com — the module is contacted at"
     note "iCOMMNextGenHP-71538015-device.aylanetworks.com" ;;
esac

if openssl x509 -in "$crt" -noout -checkend 0 >/dev/null 2>&1; then
  good "not expired: $(openssl x509 -in "$crt" -noout -enddate | cut -d= -f2)"
else
  bad "expired: $(openssl x509 -in "$crt" -noout -enddate | cut -d= -f2)"
fi

crtpub=$(openssl x509 -in "$crt" -noout -pubkey | openssl sha256)
keypub=$(openssl pkey -in "$key" -pubout 2>/dev/null | openssl sha256)
if [ "$crtpub" = "$keypub" ]; then good "private key matches the certificate"
else bad "private key does NOT match the certificate"; fi

# --- chain --------------------------------------------------------------
if [ -n "$chain" ]; then
  n=$(grep -c 'BEGIN CERTIFICATE' "$chain" 2>/dev/null || echo 0)
  good "chain file has $n certificate(s)"
  # Walk the chain by key identifier, not by subject string: a cross-signed copy
  # of an intermediate has a different issuer but the same key, so AKI->SKI is
  # the only link that stays meaningful when a CA is served two ways.
  if python3 - "$crt" "$chain" <<'PY'
import re, subprocess, sys

def field(pem, name):
    out = subprocess.run(["openssl", "x509", "-noout", "-text"],
                         input=pem, capture_output=True).stdout.decode()
    m = re.search(rf"X509v3 {name}:\s*\n\s*([0-9A-F:]+)", out)
    if m:
        return m.group(1)
    m = re.search(rf"{name}:\s*\n\s*([0-9A-F:]+)", out)
    return m.group(1) if m else None

def subject(pem):
    return subprocess.run(["openssl", "x509", "-noout", "-subject"],
                          input=pem, capture_output=True).stdout.decode() \
        .strip().removeprefix("subject=").replace("subject=", "")

def issuer(pem):
    return subprocess.run(["openssl", "x509", "-noout", "-issuer"],
                          input=pem, capture_output=True).stdout.decode() \
        .strip().replace("issuer=", "")

leaf = open(sys.argv[1], "rb").read()
chain = [c.encode() for c in re.findall(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
    open(sys.argv[2]).read(), re.S)]

want = field(leaf, "Authority Key Identifier")
ok = True
prev = leaf
for i, c in enumerate(chain, 1):
    ski = field(c, "Subject Key Identifier")
    if ski and want and ski.upper() == want.upper():
        print(f"  ok    link {i}: {subject(c).split('CN=')[-1]} signs the certificate below it")
    else:
        print(f"  FAIL  link {i}: {subject(c).split('CN=')[-1]} does not sign "
              f"{subject(prev).split('CN=')[-1]} (SKI {ski} vs AKI {want})")
        ok = False
    want = field(c, "Authority Key Identifier")
    prev = c

print(f"  note  the module must trust the issuer at the top: {issuer(prev)}")
sys.exit(0 if ok else 1)
PY
  then :; else fail=1; fi
else
  warn "no chain file given — the module must be able to build a path to a root it"
  note "trusts, and yanking a working intermediate out of the real handshake is the"
  note "usual way to get one. Without it the handshake stops at the leaf."
fi

# --- can the module's own offer even complete a handshake? ---------------
port=$(( (RANDOM % 20000) + 20000 ))
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

sopts=(-accept "127.0.0.1:$port" -naccept 1 -quiet -tls1_2 -cipher "$SUITES" -cert "$crt" -key "$key")
if [ -n "$chain" ]; then
  cat "$crt" "$chain" > "$tmp/leafchain.pem"
  sopts+=(-cert_chain "$chain")
fi

if openssl s_server "${sopts[@]}" >"$tmp/srv.log" 2>&1 &
then
  sleep 0.7
  out=$(printf 'GET / HTTP/1.0\r\n\r\n' |
    openssl s_client -connect "127.0.0.1:$port" -tls1_2 -noservername \
      -cipher "$SUITES" 2>&1 || true)
  wait $! 2>/dev/null || true
  if grep -q '^ *Cipher *: *\(DHE-RSA-AES256-SHA\|AES128-SHA\|AES256-SHA\)' <<<"$out"; then
    good "handshake with the module's exact offer completed:"
    grep '^ *Cipher *:' <<<"$out" | sed 's/^ */    /'
  else
    bad "handshake with the module's exact offer failed"
    grep -iE 'no shared cipher|handshake failure|alert' "$tmp/srv.log" 2>/dev/null |
      sed 's/^/    /' || true
    sed 's/^/    /' <<<"$(grep -iE 'error|alert' <<<"$out" | head -3)"
    if grep -qi 'no shared cipher' "$tmp/srv.log"; then
      note "NO_SHARED_CIPHER: the server key type does not match any suite the"
      note "module offers. This is the ECDSA-leaf case."
    fi
  fi
else
  warn "could not start openssl s_server; skipping the negotiation test"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "VERDICT: negotiable with the module's offer."
  echo "Still unknown, and only the module can answer it: whether its trust store"
  echo "contains this chain's root, and whether it can verify the chain's"
  echo "signature algorithms. Serve it and watch the pcap."
  exit 0
fi
echo "VERDICT: cannot be used against this module as-is." >&2
exit 1
