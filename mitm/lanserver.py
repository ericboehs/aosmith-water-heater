"""Experiment LAN server: speak Ayla Local Connect to the iCOMM module.

Key handling ported from deiger/AirCon (aircon/config.py, query_handlers.py),
which implements the same libada exchange the module's agent source shows.
Env: LANIP_KEY (the same key the relay handed the module), LANIP_KEY_ID.
Listens on :8888; the module registers us via POST /local_reg.json and then
connects out to /local_lan/*. Every plaintext update is logged.
A file /tmp/icomm-experiment/local_write.json with {"name","value","base_type"}
is delivered as a property write on the next commands poll, then removed.
Phase 28 reference implementation; see ARCHITECTURE.md item 12.
"""
import base64
import json
import logging
import math
import os
import random
import string
import time
from pathlib import Path

from aiohttp import web
from Crypto.Cipher import AES

LOG = logging.getLogger("lanserver")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
WORK = Path(os.environ.get("LAN_WORK_DIR", "/tmp/icomm-lan"))
OUT = WORK / "local-updates.jsonl"
WRITE_FILE = WORK / "local_write.json"
LANIP_KEY = os.environ["LANIP_KEY"].encode()
KEY_ID = int(os.environ.get("LANIP_KEY_ID", "1"))
state = {"random_1": "", "time_1": 0, "random_2": "", "time_2": 0, "seq_no": 0}
app_enc = dev_enc = None


def hmac_digest(key, msg):
    import hmac
    return hmac.digest(key, msg, "sha256")


def build_key(msg):
    return hmac_digest(LANIP_KEY, hmac_digest(LANIP_KEY, msg) + msg)


class Enc:
    def __init__(self, msg):
        self.sign_key = build_key(msg + b"0")
        self.crypto_key = build_key(msg + b"1")
        self.cipher = AES.new(build_key(msg + b"1") and self.crypto_key,
                              AES.MODE_CBC, build_key(msg + b"2")[:16])

    @staticmethod
    def pad(data):
        return data.ljust(math.ceil(len(data) / 16) * 16, bytes([0]))

    def encrypt_sign(self, data: dict) -> dict:
        text = json.dumps(data).encode()
        return {"enc": base64.b64encode(self.cipher.encrypt(self.pad(text))).decode(),
                "sign": base64.b64encode(hmac_digest(self.sign_key, text)).decode()}

    def decrypt_validate(self, data: dict) -> dict:
        text = self.cipher.decrypt(base64.b64decode(data["enc"])).rstrip(bytes([0]))
        sign = base64.b64encode(hmac_digest(self.sign_key, text)).decode()
        if sign != data["sign"]:
            raise ValueError(f"bad signature on {text[:80]!r}")
        return json.loads(text.decode())


def derive():
    global app_enc, dev_enc
    r1, r2 = state["random_1"].encode(), state["random_2"].encode()
    t1, t2 = str(state["time_1"]).encode(), str(state["time_2"]).encode()
    app_enc = Enc(r1 + r2 + t1 + t2)   # app -> module
    dev_enc = Enc(r2 + r1 + t2 + t1)   # module -> app


async def key_exchange(request):
    global state
    body = await request.json()
    key = body["key_exchange"]
    LOG.warning("key_exchange: %s", key)
    if key.get("key_id") != KEY_ID:
        LOG.error("key id mismatch: %s", key.get("key_id"))
        return web.Response(status=404)
    state["random_1"], state["time_1"] = key["random_1"], key["time_1"]
    state["random_2"] = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    state["time_2"] = time.monotonic_ns() % 2**40
    derive()
    LOG.warning("session keys derived")
    return web.json_response({"random_2": state["random_2"], "time_2": state["time_2"]})


async def commands(request):
    state["seq_no"] += 1
    data = {}
    if WRITE_FILE.exists():
        write = json.loads(WRITE_FILE.read_text())
        data = {"properties": [{"property": {
            "base_type": write.get("base_type", "string"),
            "name": write["name"], "value": write["value"],
            "id": "".join(random.choices(string.ascii_letters + string.digits, k=8))}}]}
        WRITE_FILE.unlink()
        LOG.warning("delivering LOCAL write: %s = %s", write["name"], write["value"])
    response = {"seq_no": state["seq_no"], "data": data}
    LOG.info("commands poll -> %s", "write" if data else "empty")
    return web.json_response(app_enc.encrypt_sign(response))


async def datapoint(request):
    raw = await request.text()
    try:
        update = dev_enc.decrypt_validate(json.loads(raw))
        LOG.warning("decrypted update: %s", update)
        with OUT.open("a") as f:
            f.write(json.dumps({"t": time.time(), "update": update}) + "\n")
    except Exception:
        LOG.exception("datapoint decrypt failed; raw: %s", raw[:200])
        return web.Response(status=400)
    return web.Response()


async def anything(request):
    body = await request.text()
    LOG.info("%s %s body=%s", request.method, request.path, body[:200])
    return web.json_response({"status": 200})


app = web.Application()
app.router.add_post("/local_lan/key_exchange.json", key_exchange)
app.router.add_route("*", "/local_lan/commands.json", commands)
app.router.add_route("*", "/local_lan/property/datapoint.json", datapoint)
app.router.add_route("*", "/local_lan/property/datapoint/ack.json", datapoint)
app.router.add_route("*", "/{tail:.*}", anything)
web.run_app(app, port=8888, print=None)
