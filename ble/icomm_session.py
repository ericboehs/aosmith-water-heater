#!/usr/bin/env python3
"""Open an authenticated iCOMM BLE session, in the app's exact order.

Every earlier script in this directory subscribed to the notify pipe as soon as
the link came up and then sent the PIN. A second pass over the app's Hermes
bundle showed that is backwards. The app:

    connect -> request MTU 500 -> discover -> write the 6 ASCII PIN bytes
    (with response) -> sleep ~600ms -> *then* subscribe -> write BD F2 05 01 D8

so the CCCD 0100 lands *after* the PIN write response, not before. If the
firmware only binds its notification handle once a PIN has authenticated, a
CCCD written too early is silently dropped, which is exactly the symptom we
have: every ATT write accepted, not one notification, remote hangup at ~15s.

Pass --subscribe-first to run the old ordering instead. That is the control
half of the experiment; run both back to back with btmon capturing.

The other correction from that pass: the session frames *do* carry CRC. The
"skipCrc argument shift" we thought we had found is not in the decompile --
_sendReceive passes three arguments, so skipCrc defaults false. The first
request on the wire is literally BD F2 05 01 D8.

Handshake, once notifications work:

    F2 05 01                list stored pair keys
    F0 16 <18 bytes>        store our pair key (18 ASCII chars)
    F3 05 01                delete the first stored key, only when full
    F4 04                   ask for a random number
    F1 19 01 <20 bytes>     HMAC-SHA1(message=pairkey, key=random) digest

Note the HMAC argument order: the app calls CryptoJS.HmacSHA1(pairKey, random)
and CryptoJS's signature is (message, key), so the two random bytes are the
*key* and the pair key string is the message.

This script performs the handshake and stops. It writes no tank configuration.
"""
import argparse
import asyncio
import hashlib
import hmac
import os
import sys
import time

from bleak import BleakClient, BleakScanner

WRITE_UUID = "69400003-b5a3-f393-e0a9-e50e24dcca99"
NOTIFY_UUID = "69400002-b5a3-f393-e0a9-e50e24dcca99"

# The module uses a BLE random static address, so we match on the advertised
# name; ICOMM_ADDR is only a fallback for when the name scan comes up empty.
ADDR = os.environ.get("ICOMM_ADDR", "")

# The tank's DSN. The app hashes it into the 18-character asset ID it stores as
# a pair key. Keep the real value out of the repo.
DSN = os.environ.get("ICOMM_DSN", "")

# crc8 carries the table brute-forced out of the bundle; keep one copy of it.
from icomm_ble import crc8, fmt  # noqa: E402

ACK_TABLE = {
    0: "unknown",
    1: "pairing timeout",
    2: "pair key list full",
    3: "key not found",
    4: "session key timer expired",
    5: "random number not valid",
    6: "crc incorrect",
    7: "success",
}


def frame(text: str) -> bytes:
    """Build a wire frame from the app's own space-separated hex notation.

    The app prefixes BD, pads each token to two digits, and appends CRC-8.
    """
    body = bytes([0xBD] + [int(tok, 16) for tok in text.split()])
    return body + bytes([crc8(body)])


def ack_of(resp: bytes):
    """Decode the ACK bitmask in the response's penultimate byte.

    The app maps the *bit position* of the lowest set bit through its ACKTable,
    so 0x80 is success and 0x08 is "key not found".
    """
    if len(resp) < 2:
        return None, "short"
    mask = resp[-2]
    if mask == 0:
        return 0, ACK_TABLE[0]
    bit = (mask & -mask).bit_length() - 1
    return bit, ACK_TABLE.get(bit, f"bit {bit}")


def pin_for(name: str) -> str:
    """The app's PIN derivation, verbatim: a slice of the advertised name."""
    return name[15:len(name) - 4] if len(name) > 21 else name[-6:]


def asset_id(dsn: str) -> str:
    """assetID = hex(SHA256(dsn))[0:18], per the setup screen."""
    return hashlib.sha256(dsn.encode()).hexdigest()[:18]


class Session:
    """One request, one notification, one subscription -- like the app.

    The app builds a fresh monitor for every request and tears it down after a
    single callback, so on Android the CCCD flips 0100 / 0000 around each
    exchange. We mirror that unless --persist-notify says otherwise.
    """

    def __init__(self, client, persist=False, log=print):
        self.client = client
        self.persist = persist
        self.log = log
        self.inbox = asyncio.Queue()
        self.subscribed = False

    def _on_notify(self, _sender, data: bytearray):
        self.inbox.put_nowait(bytes(data))

    async def subscribe(self):
        if not self.subscribed:
            await self.client.start_notify(NOTIFY_UUID, self._on_notify)
            self.subscribed = True

    async def unsubscribe(self):
        if self.subscribed and not self.persist:
            await self.client.stop_notify(NOTIFY_UUID)
            self.subscribed = False

    async def request(self, text: str, timeout=6.0):
        """Send one app-notation frame and wait for its notification."""
        while not self.inbox.empty():
            self.inbox.get_nowait()
        payload = frame(text)
        await self.subscribe()
        self.log(f"  -> {fmt(payload)}")
        started = time.monotonic()
        await self.client.write_gatt_char(WRITE_UUID, payload, response=True)
        try:
            resp = await asyncio.wait_for(self.inbox.get(), timeout=timeout)
        except asyncio.TimeoutError:
            self.log(f"  <- (nothing in {timeout:.0f}s)")
            await self.unsubscribe()
            return None
        elapsed = (time.monotonic() - started) * 1000
        bit, meaning = ack_of(resp)
        good = crc8(resp[:-1]) == resp[-1]
        self.log(f"  <- {fmt(resp)}  [{elapsed:.0f}ms, ack={bit} {meaning},"
                 f" crc {'ok' if good else 'BAD'}]")
        await self.unsubscribe()
        return resp


async def find_module(timeout=15.0, attempts=4):
    for attempt in range(attempts):
        found = await BleakScanner.discover(timeout=timeout)
        for dev in found:
            if dev.name and dev.name.upper().startswith("ICOMM"):
                return dev
        if ADDR:
            hit = next((d for d in found if d.address == ADDR), None)
            if hit:
                return hit
        print(f"  scan {attempt + 1}/{attempts} found nothing named iCOMM",
              flush=True)
    return None


async def handshake(args):
    print("scanning (the module only advertises in pairing mode) ...",
          flush=True)
    dev = await find_module()
    if dev is None:
        print("no iCOMM module advertising -- is the light flashing blue?")
        return 1

    # The PIN comes from the *advertised* name, so record it exactly; which
    # branch of the derivation applies depends on its length.
    name = dev.name or ""
    pin = pin_for(name)
    print(f"found {name!r} at {dev.address}")
    print(f"  name length {len(name)} -> "
          f"{'slice(15, -4)' if len(name) > 21 else 'last 6'} branch")
    print(f"  pin {pin!r} = {fmt(pin.encode())}")

    t0 = time.monotonic()

    def log(msg):
        print(f"[{time.monotonic() - t0:5.2f}s] {msg}", flush=True)

    async with BleakClient(dev) as client:
        log(f"connected, mtu={client.mtu_size}")
        sess = Session(client, persist=args.persist_notify, log=log)

        if args.subscribe_first:
            # The control case: what every earlier script did.
            await sess.subscribe()
            log("subscribed BEFORE the pin (control ordering)")

        # The PIN is six raw ASCII bytes -- no BD frame, no length, no CRC,
        # no terminator -- written with response. The app waits for nothing
        # afterwards but its own sleeps.
        log(f"writing pin {pin!r}")
        await client.write_gatt_char(WRITE_UUID, pin.encode(), response=True)
        log("pin write response received")

        # Two explicit 300ms sleeps in the app, between the PIN and the first
        # session request. Reproduced rather than tuned.
        await asyncio.sleep(0.6)

        if not args.subscribe_first:
            await sess.subscribe()
            log("subscribed AFTER the pin (app ordering)")

        log("F2: list stored pair keys")
        resp = await sess.request("F2 05 01")
        if resp is None:
            log("no notification -- the module is still silent")
            return 2

        bit, meaning = ack_of(resp)
        log(f"first notification received: {meaning}")

        if not DSN:
            log("ICOMM_DSN unset, stopping before we store a pair key")
            return 0

        key = asset_id(DSN)
        log(f"pair key {key} ({len(key)} chars)")
        stored = " ".join(f"{b:02X}" for b in key.encode())

        if bit == 2:  # the module's slot list is full
            log("F3: delete the first stored key")
            await sess.request("F3 05 01")

        if bit != 7:
            log("F0: store our pair key")
            resp = await sess.request(f"F0 16 {stored}")
            if resp is None:
                return 2

        log("F4: request a random number")
        resp = await sess.request("F4 04")
        if resp is None:
            return 2

        # getRandomNumber() reverses the byte list and reads elements 3 and 2,
        # i.e. the two bytes before the ACK and CRC, big-endian on the wire.
        rand = bytes([resp[-4], resp[-3]])
        log(f"random {fmt(rand)}")

        digest = hmac.new(rand, key.encode(), hashlib.sha1).digest()
        challenge = " ".join(f"{b:02X}" for b in digest)
        log("F1: session challenge")
        resp = await sess.request(f"F1 19 01 {challenge}")
        if resp is None:
            return 2

        bit, meaning = ack_of(resp)
        log(f"session: {meaning}")
        return 0 if bit == 7 else 3


def main():
    parser = argparse.ArgumentParser(
        description="open an authenticated iCOMM BLE session")
    parser.add_argument("--subscribe-first", action="store_true",
                        help="subscribe before the PIN (the old, failing "
                             "ordering) as the control half of the A/B")
    parser.add_argument("--persist-notify", action="store_true",
                        help="keep one subscription instead of the app's "
                             "subscribe/unsubscribe per request")
    args = parser.parse_args()
    sys.exit(asyncio.run(handshake(args)))


if __name__ == "__main__":
    main()
