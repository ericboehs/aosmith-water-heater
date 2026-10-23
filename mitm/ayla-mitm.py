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

A rule may also carry an `each` list instead of a single `action`: successive
matches serve successive entries, and the last one repeats. That is how a ladder
of setpoints gets served without restarting the proxy — useful for working out
which telemetry offsets follow a value and which only drift:

    [{"match": {"path_regex": "commands.xml"},
      "each": [{"body": "...148 F..."}, {"body": "...150 F..."}]}]

An `inject` rule is the one that keeps the cloud alive. Instead of answering the
request locally, it lets the request go upstream and rewrites the *response* only
when there is something to say. Ayla therefore still sees every poll, so the module
stays online in the cloud and normal cloud writes keep flowing; our frame rides along
when we have one:

    {"match": {"path_regex": "commands.xml"},
     "inject": {"file": "/tmp/ayla-cmd.txt"}}

If Ayla's own reply already carries a command, ours is left in the file for the next
poll — the real cloud wins, which is what you want when a write is in flight.

`echo_headers` copies named request headers back onto the response. Ayla echoes
`x-Ayla-auth-key` on every reply; whether the module needs that to keep polling is
an open question, and serving a reply that omits it is one of the differences
between a synthetic reply and a real one:

    {"action": {"body": "<commands/>", "echo_headers": ["x-Ayla-auth-key"]}}
"""

import asyncio
import json
import re
import time
from pathlib import Path

from mitmproxy import ctx, http

# The four suites the module offers. If the negotiated one is not in here the
# device would have rejected the connection, so a mismatch is a real error.
MODULE_SUITES = {"DHE-RSA-AES256-SHA", "AES128-SHA", "AES256-SHA"}

# Ayla's own reply to a poll with nothing queued, byte for byte (newline layout
# included) -- served while a hold times out, so the module sees what it normally
# sees rather than something novel.
EMPTY_COMMANDS = "<commands>\n  <properties/>\n  <schedules/>\n  <cmds/>\n</commands>\n"


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
                r["_step"] = 0

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
    async def request(self, flow: http.HTTPFlow) -> None:
        rule = self._match(flow.request)
        if rule is None:
            return
        if rule.get("hold"):
            await self._hold(flow, rule)
            return
        self._answer(flow, rule)

    async def _hold(self, flow: http.HTTPFlow, rule: dict) -> bool:
        """Keep the module's poll open until a command appears, or give up.

        The module only asks for commands when it believes one is waiting, so a
        cloud stand-in cannot push a write on its own. Holding the poll open
        turns the module's own request into the push channel: the reply is sent
        the moment the trigger file has content, which is as close to an instant
        write as this protocol allows.

        `hold` = {file, timeout, poll_ms, empty}: the file is consumed (truncated)
        when its contents are served, and an empty reply is sent on timeout so the
        module does not sit on a dead connection.
        """
        cfg = rule["hold"]
        trigger = Path(cfg["file"]).expanduser()
        empty = cfg.get("empty", EMPTY_COMMANDS)
        step = max(0.025, int(cfg.get("poll_ms", 200)) / 1000.0)
        timeout = float(cfg.get("timeout", 120))
        start = time.monotonic()
        deadline = start + timeout

        body = ""
        while time.monotonic() < deadline:
            if trigger.exists():
                body = trigger.read_text().strip()
                if body:
                    trigger.write_text("")  # consume, so it is served once
                    break
            await asyncio.sleep(step)  # yields to the event loop: other flows keep flowing
        held = time.monotonic() - start

        self._answer(flow, rule, body=body or empty)
        ctx.log.info(
            f"held poll {held:.1f}s, "
            + ("served a command" if body else f"served an empty reply (timeout {timeout:.0f}s)")
        )
        return True

    def _answer(self, flow: http.HTTPFlow, rule: dict, body: str | None = None) -> None:
        action = self._next_action(rule)
        if body is None:
            body = action.get("body", "")
            if not isinstance(body, str):
                body = json.dumps(body)
        headers = dict(action.get("headers", {"Content-Type": "application/json"}))
        for name in action.get("echo_headers", []):
            got = flow.request.headers.get(name)
            if got is not None:
                headers[name] = got
        flow.response = http.Response.make(
            action.get("status", 200),
            body.encode() if isinstance(body, str) else body,
            headers,
        )
        if action.get("close"):
            # Hang up instead of keeping the connection alive, to test whether a
            # dropped connection is what makes the module dial and poll again.
            flow.response.close_connection = True
        ctx.log.info(
            f"answered from rule: {flow.request.method} {flow.request.path}"
            + (f" (step {rule['_step'] + 1}/{len(rule['each'])})" if rule.get("_each") else "")
        )

    def _next_action(self, rule: dict) -> dict:
        """One action per match when the rule is a ladder, else always the same."""
        if not rule.get("each"):
            return rule.get("action", {})
        step = min(rule.get("_step", 0), len(rule["each"]) - 1)
        rule["_step"] = step + 1
        return rule["each"][step]

    def response(self, flow: http.HTTPFlow) -> None:
        self.count += 1
        # inject first, then log: the flow log must record what the module actually
        # received, not Ayla's pre-injection reply. Logging in the other order made
        # an injected poll look empty in the log and cost a morning of confusion.
        self._inject(flow)
        self._print(flow)
        self._log(flow)

    def _inject(self, flow: http.HTTPFlow) -> None:
        """Rewrite an upstream reply with a frame of ours, keeping the round trip.

        Runs after Ayla has answered, so the cloud still records the poll and the
        module stays online -- the difference between this and a local `action` rule,
        which answers without ever contacting upstream and makes the device vanish
        from Ayla for the length of the session.
        """
        rule = self._match(flow.request)
        if not rule or "inject" not in rule:
            return
        trigger = Path(rule["inject"]["file"]).expanduser()
        if not trigger.exists():
            return
        body = trigger.read_text().strip()
        if not body:
            return
        upstream = (flow.response.content or b"").decode(errors="replace")
        prefer_ours = rule["inject"].get("prefer") == "ours"
        if "setBlockParameter" in upstream and not prefer_ours:
            ctx.log.info("inject queued: Ayla already has a command, ours waits a poll")
            return
        if "setBlockParameter" in upstream:
            ctx.log.info("inject overrode Ayla's pending command (prefer=ours)")
        trigger.write_text("")  # consume, so it is served once
        flow.response.set_content(body.encode())
        ctx.log.info(f"injected {len(body)}B into the poll reply (upstream still contacted)")

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
            # The module arrives via the gateway's SNAT; anything else in the log
            # (127.0.0.1, ::1) is a local probe and not the appliance.
            "client": flow.client_conn.peername[0] if flow.client_conn.peername else None,
        }
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")


addons = [AylaMITM()]
