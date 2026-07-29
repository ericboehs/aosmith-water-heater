#!/usr/bin/env python3
"""Ask the module to open an Ayla LAN-mode session with us.

/regtoken.json identifies this as an Ayla module doing Same-LAN registration.
In Ayla's LAN protocol the client POSTs its own address to /local_reg.json and
the *device* then connects back and starts a key exchange. So the interesting
traffic is inbound: we stand up a logging HTTP server first, then knock.

Even if we cannot complete the exchange -- the LAN key normally comes from the
Ayla cloud for a registered DSN -- the callback alone tells us LAN mode is
enabled in this firmware and hands us whatever the device volunteers about
itself.

Read-only: local_reg.json registers a notification target. It changes no tank
configuration.
"""
import json, os, socket, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

MODULE = os.environ.get("ICOMM_IP", "")
if not MODULE:
    raise SystemExit("set ICOMM_IP to the module's LAN address")
PORT = 8888

def my_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((MODULE, 80))
    ip = s.getsockname()[0]
    s.close()
    return ip

HITS = []

class Handler(BaseHTTPRequestHandler):
    def _log(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        HITS.append((self.command, self.path, dict(self.headers), body))
        print(f"\n  <<< {self.command} {self.path}", flush=True)
        for k, v in self.headers.items():
            print(f"      {k}: {v}", flush=True)
        if body:
            print(f"      body: {body[:600]!r}", flush=True)
        return body

    def do_POST(self):
        self._log()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":200}')

    def do_GET(self):
        self._log()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":200}')

    def do_PUT(self):
        self.do_POST()

    def log_message(self, *a):
        pass

def post(path, payload, timeout=8):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"http://{MODULE}/{path}", data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()[:400]
    except Exception as e:
        return None, repr(e)

def main():
    ip = my_ip()
    print(f"listening on {ip}:{PORT}", flush=True)
    srv = HTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    body = {"local_reg": {"ip": ip, "port": PORT,
                          "uri": "/local_lan", "notify": 1}}
    print(f"POST /local_reg.json {json.dumps(body)}", flush=True)
    code, resp = post("local_reg.json", body)
    print(f"  -> {code} {resp}", flush=True)

    print("waiting 30s for the module to call back ...", flush=True)
    time.sleep(30)
    print(f"\ninbound requests: {len(HITS)}", flush=True)
    if not HITS:
        print("nothing called back", flush=True)

main()
