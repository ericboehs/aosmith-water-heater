"""mitmproxy addon: readable log of the iCOMM module's TLS channel.

Every flow is appended to a JSONL file (one object per request/response, header
order preserved, bodies escaped), and printed one line per exchange. Headers are
logged but never redacted -- this is the module's cloud traffic, and the point of
the exercise is to see what it carries. Keep the JSONL out of git.

Optional fault injection, for development rather than observation:

    --set ayla_rules=/path/rules.json

    [{"match": {"method": "POST", "path_regex": "/notifications"},
      "action": {"status": 500, "body": "{}", "headers": {"X-Test": "1"}}}]

The first matching rule replaces the response, so the upstream is never called.
"""

import json
import re
import time
from pathlib import Path

from mitmproxy import ctx, http

# The four suites the module offers. If the negotiated one is not in here the
# device would have rejected the connection, so a mismatch is a real error.
MODULE_SUITES = {"DHE-RSA-AES256-SHA", "AES128-SHA", "AES256-SHA"}


class AylaMITM:
    def __init__(self) -> None:
        self.log_path: Path | None = None
        self.rules: list[dict] = []
        self.count = 0

    def load(self, loader) -> None:
        loader.add_option(
            "ayla_log",
            str,
            "mitm/flows.jsonl",
            "Where to append one JSON object per request/response.",
        )
        loader.add_option(
            "ayla_rules",
            str,
            "",
            "JSON file of request -> canned response rules.",
        )
        loader.add_option(
            "ayla_host",
            str,
            "proxy-ingress-usfield.aylanetworks.com",
            "Host the module would normally reach; logged beside each flow.",
        )

    def configure(self, updated) -> None:
        log = ctx.options.ayla_log
        self.log_path = Path(log).expanduser() if log else None
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text("")  # fresh session, no stale confusion
            ctx.log.info(f"flow log -> {self.log_path}")

        rules = ctx.options.ayla_rules
        if rules:
            self.rules = json.loads(Path(rules).expanduser().read_text())
            ctx.log.info(f"loaded {len(self.rules)} response rule(s)")
            for r in self.rules:
                r["_re"] = re.compile(r.get("match", {}).get("path_regex", ""))

    # -- tls ------------------------------------------------------------
    def tls_start_client(self, data) -> None:
        ctx.log.debug("client TLS offer accepted")

    def tls_established_client(self, data) -> None:
        cipher = getattr(data.conn, "cipher", None)
        name = cipher[0] if isinstance(cipher, tuple) else cipher
        if name and name not in MODULE_SUITES:
            ctx.log.warn(
                f"negotiated {cipher[0]}, which is not one of the module's four suites"
            )
        ctx.log.info(f"TLS established with module: {cipher}")

    # -- http -----------------------------------------------------------
    def request(self, flow: http.HTTPFlow) -> None:
        rule = self._match(flow.request)
        if rule is None:
            return
        action = rule.get("action", {})
        body = action.get("body", "")
        if not isinstance(body, str):
            body = json.dumps(body)
        flow.response = http.Response.make(
            action.get("status", 200),
            body.encode() if isinstance(body, str) else body,
            action.get("headers", {"Content-Type": "application/json"}),
        )
        ctx.log.info(f"answered from rule: {flow.request.method} {flow.request.path}")

    def response(self, flow: http.HTTPFlow) -> None:
        self.count += 1
        self._print(flow)
        self._log(flow)

    # -- helpers --------------------------------------------------------
    def _match(self, req: http.Request) -> dict | None:
        for rule in self.rules:
            m = rule.get("match", {})
            if m.get("method") and m["method"] != req.method:
                continue
            if m.get("path_regex") and not rule["_re"].search(req.path):
                continue
            return rule
        return None

    def _print(self, flow: http.HTTPFlow) -> None:
        req, res = flow.request, flow.response
        assert res is not None
        marked = "" if res.status_code < 400 else "  <-- error"
        ctx.log.info(
            f"#{self.count} {req.method} {req.path} -> {res.status_code} "
            f"({len(req.raw_content or b'')}B up, {len(res.raw_content or b'')}B down)"
            f"{marked}"
        )

    def _log(self, flow: http.HTTPFlow) -> None:
        if not self.log_path:
            return
        req, res = flow.request, flow.response
        assert res is not None
        record = {
            "t": round(time.time(), 3),
            "host": ctx.options.ayla_host,
            "method": req.method,
            "scheme": req.scheme,
            "path": req.path,
            "http_version": req.http_version,
            "request_headers": list(req.headers.items(multi=True)),
            "request_body": req.get_text(strict=False),
            "status": res.status_code,
            "response_headers": list(res.headers.items(multi=True)),
            "response_body": res.get_text(strict=False),
            "duration_ms": round((flow.response.timestamp_end or 0) - req.timestamp_start, 3) * 1000
            if flow.response.timestamp_end
            else None,
            "client_cipher": getattr(flow.client_conn, "cipher", None),
        }
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")


addons = [AylaMITM()]
