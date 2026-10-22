#!/usr/bin/env python3
"""Stand in for the iCOMM module's TLS client, for testing the proxy.

Reproduces the four properties of the captured ClientHello that broke the naive
interception setup: TLS 1.2 only, **no SNI**, four legacy CBC suites, and no
client certificate. A proxy that works here works for the device; one that fails
here fails there too, and failing here costs a second instead of 24 seconds of
device silence plus a pcap.

    ./module-sim.py --port 8443 --path /notifications/get
    ./module-sim.py --port 8443 --trust chain-root.pem   # validate like the device

--trust turns on real chain validation against that CA bundle, which is the only
way to approximate the module's Phase 4/5 behaviour without the module.
"""

import argparse
import socket
import ssl
import sys

MODULE_SUITES = "DHE-RSA-AES256-SHA:AES128-SHA:AES256-SHA"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--path", default="/")
    ap.add_argument("--method", default="GET")
    ap.add_argument("--body", default="")
    ap.add_argument("--header", action="append", default=[])
    ap.add_argument("--trust", help="CA bundle to validate the server against")
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers(MODULE_SUITES)
    # The module sends no SNI, so it cannot be doing SNI-based hostname checks;
    # it validates the chain and the name inside the certificate.
    ctx.check_hostname = False
    if args.trust:
        ctx.load_verify_locations(args.trust)
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:
        ctx.verify_mode = ssl.CERT_NONE

    sock = socket.create_connection((args.host, args.port), timeout=args.timeout)
    try:
        tls = ctx.wrap_socket(sock, server_hostname=None)  # no SNI, like the module
    except ssl.SSLError as e:
        print(f"TLS FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    cipher, proto, bits = tls.cipher()
    print(f"TLS {proto}  {cipher}  {bits} bit")
    if cipher not in MODULE_SUITES.split(":"):
        print(f"WARNING: {cipher} is not one of the module's suites")

    der = tls.getpeercert(binary_form=True)
    if der:
        import subprocess
        info = subprocess.run(
            ["openssl", "x509", "-inform", "DER", "-noout", "-subject", "-issuer"],
            input=der, capture_output=True, check=False,
        ).stdout.decode(errors="replace")
        print(info.strip())

    lines = [f"{args.method} {args.path} HTTP/1.1", "Connection: close"]
    if args.body:
        lines.append(f"Content-Length: {len(args.body.encode())}")
    lines += args.header
    req = "\r\n".join(lines) + "\r\n\r\n" + args.body
    tls.sendall(req.encode())

    data = b""
    while chunk := tls.recv(65536):
        data += chunk
    print("--- response")
    print(data.decode(errors="replace"))
    tls.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
