#!/usr/bin/env python3
"""Replace the water heater card on the Climate view with local CTA-2045 cards.

Storage-mode Lovelace keeps its config in memory, so editing .storage on disk
does nothing until a restart. The websocket API is the only way to change it in
place. Run with --dry-run to write the proposed config out without saving.

    HOME_ASSISTANT_TOKEN=... patch_dashboard.py [--dry-run]

HA_URL and HA_DASHBOARD select the instance and the dashboard's url_path; the
defaults are a stock install's ("lovelace" is the built-in Overview).
"""

import asyncio
import json
import os
import sys
import time

import websockets

from cards import NEW_CARDS

URL = os.environ.get("HA_URL", "ws://homeassistant.local:8123/api/websocket")
TOKEN = os.environ.get("HOME_ASSISTANT_TOKEN")
DASHBOARD = os.environ.get("HA_DASHBOARD", "lovelace")
HERE = os.path.dirname(os.path.abspath(__file__))

VIEW_TITLE = "Climate"
# Cards are located by content rather than index so a reordered dashboard fails
# loudly instead of clobbering the wrong card. The first title is the original
# cloud-only card, the second is what this script leaves behind -- matching both
# means the script can be re-run to adjust the cards it already installed.
CARD_TITLES = ("\U0001f6c1 Water Heater Control", "\U0001f6c1 Water Heater")


async def rpc(socket, counter, message):
    message = dict(message, id=next(counter))
    await socket.send(json.dumps(message))
    while True:
        reply = json.loads(await socket.recv())
        if reply.get("id") == message["id"] and reply.get("type") == "result":
            if not reply.get("success"):
                raise SystemExit(f"call failed: {reply.get('error')}")
            return reply.get("result")


def patch(config):
    views = config["views"]
    matches = [v for v in views if v.get("title") == VIEW_TITLE]
    if len(matches) != 1:
        raise SystemExit(f"expected one {VIEW_TITLE!r} view, found {len(matches)}")
    view = matches[0]

    for section in view.get("sections", []):
        cards = section.get("cards", [])
        # Every card this script owns mentions the local hot water sensor; on a
        # re-run they form one contiguous run to swap out wholesale.
        ours = [i for i, card in enumerate(cards)
                if card.get("title") in CARD_TITLES
                or "sensor.water_heater_hot_water" in json.dumps(card)]
        if not ours:
            continue
        first, last = ours[0], ours[-1]
        if ours != list(range(first, last + 1)):
            raise SystemExit(f"water heater cards are not contiguous: {ours}")
        section["cards"] = cards[:first] + NEW_CARDS + cards[last + 1:]
        return config, (first, len(ours))
    raise SystemExit(f"no water heater card found in the {VIEW_TITLE} view")


async def main():
    if not TOKEN:
        raise SystemExit("HOME_ASSISTANT_TOKEN is not set")
    dry_run = "--dry-run" in sys.argv
    counter = iter(range(1, 10_000))

    async with websockets.connect(URL, max_size=16 * 1024 * 1024) as socket:
        json.loads(await socket.recv())                      # auth_required
        await socket.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        if json.loads(await socket.recv()).get("type") != "auth_ok":
            raise SystemExit("authentication rejected")

        config = await rpc(socket, counter,
                           {"type": "lovelace/config", "url_path": DASHBOARD})

        # Timestamped so a re-run cannot bury the pre-change config.
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(HERE, f"dashboard-backup-{stamp}.json")
        with open(backup, "w") as handle:
            json.dump(config, handle, indent=2)
        print(f"backed up current config to {backup}")

        config, (index, replaced) = patch(config)
        with open(os.path.join(HERE, "dashboard-proposed.json"), "w") as handle:
            json.dump(config, handle, indent=2)
        print(f"replaced {replaced} card(s) at index {index} "
              f"with {len(NEW_CARDS)} cards")

        if dry_run:
            print("dry run; not saving")
            return

        await rpc(socket, counter, {"type": "lovelace/config/save",
                                    "url_path": DASHBOARD, "config": config})
        print("saved")


if __name__ == "__main__":
    asyncio.run(main())
