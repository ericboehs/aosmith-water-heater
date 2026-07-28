#!/usr/bin/env python3
"""Bridge an A.O. Smith CTA-2045 heat pump water heater to Home Assistant.

Polls the tank over RS-485 and publishes to MQTT with Home Assistant discovery,
so the tank appears as a device with no cloud account and no iCOMM dependency.

Two behaviours here exist because the tank demands them:

* A comm-status heartbeat runs every few minutes. Without it the tank decides
  its uplink is dead after about 14 minutes, reports SGD Error, and silently
  drops whatever demand-response event was active.
* Demand-response events are re-issued before they expire. Durations are
  bounded on purpose -- an indefinite event would outlive this process and
  leave the tank curtailed with nothing left to release it.

Configuration is entirely environment driven. MQTT_PASSWORD is read from the
environment and never written anywhere.
"""

import json
import os
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cta2045
from cta2045 import Tank

PORT_PATH = os.environ.get("CTA2045_PORT", "/dev/serial0")
POLL_INTERVAL = float(os.environ.get("CTA2045_POLL_INTERVAL", "30"))
HEARTBEAT_INTERVAL = float(os.environ.get("CTA2045_HEARTBEAT_INTERVAL", "300"))

# Re-issue well inside the event duration so a missed round trip does not let
# the event lapse unnoticed.
EVENT_DURATION = float(os.environ.get("CTA2045_EVENT_DURATION", "1800"))
EVENT_REISSUE = float(os.environ.get("CTA2045_EVENT_REISSUE", "600"))

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "homeassistant")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD")

TOPIC_BASE = os.environ.get("MQTT_TOPIC_BASE", "cta2045/waterheater")
DISCOVERY_PREFIX = os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
NODE_ID = os.environ.get("MQTT_NODE_ID", "aosmith_hpwh")

STATE_TOPIC = f"{TOPIC_BASE}/state"
AVAILABILITY_TOPIC = f"{TOPIC_BASE}/availability"
COMMAND_TOPIC = f"{TOPIC_BASE}/demand_response/set"

# Each mode maps to the Tank method that starts it. "Normal" ends whatever is
# running; the others are timed events this daemon keeps alive.
DR_MODES = {
    "Normal": None,
    "Shed": "shed",
    "Load Up": "load_up",
    "Critical Peak": "critical_peak",
    "Grid Emergency": "grid_emergency",
}

# Operational states that show an event is already running, mapped to the mode
# this daemon will adopt. The tank reports the *effect* of an event and not the
# command behind it -- shed, critical peak and grid emergency all read back as
# Curtailed -- so adoption picks the mildest command producing that effect.
ADOPTED_MODES = {
    2: "Shed",          # Running Curtailed
    4: "Shed",          # Idle Curtailed
    3: "Load Up",       # Running Heightened
    6: "Load Up",       # Idle Heightened
}


def log(message):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def sensor(key, name, unit=None, device_class=None, icon=None,
           state_class="measurement"):
    config: dict = {
        "name": name,
        "state_topic": STATE_TOPIC,
        "value_template": f"{{{{ value_json.{key} }}}}",
        "unique_id": f"{NODE_ID}_{key}",
        "availability_topic": AVAILABILITY_TOPIC,
    }
    if unit:
        config["unit_of_measurement"] = unit
        if state_class:
            config["state_class"] = state_class
    if device_class:
        config["device_class"] = device_class
    if icon:
        config["icon"] = icon
    return f"{DISCOVERY_PREFIX}/sensor/{NODE_ID}/{key}/config", config


def binary_sensor(key, name, device_class=None, icon=None):
    config: dict = {
        "name": name,
        "state_topic": STATE_TOPIC,
        "value_template": f"{{{{ value_json.{key} }}}}",
        "payload_on": "true",
        "payload_off": "false",
        "unique_id": f"{NODE_ID}_{key}",
        "availability_topic": AVAILABILITY_TOPIC,
    }
    if device_class:
        config["device_class"] = device_class
    if icon:
        config["icon"] = icon
    return f"{DISCOVERY_PREFIX}/binary_sensor/{NODE_ID}/{key}/config", config


def discovery_entities():
    entities = [
        sensor("operational_state", "Operational State",
               icon="mdi:state-machine"),
        sensor("hot_water", "Hot Water", unit="%", icon="mdi:water-thermometer"),
        sensor("energy_deficit", "Energy Deficit", unit="Wh",
               device_class="energy_storage"),
        sensor("storage_capacity", "Storage Capacity", unit="Wh",
               device_class="energy_storage"),
        # Deliberately not device_class power: the tank reports a nameplate
        # constant (4500 with the element, 356 on the compressor), never a
        # measurement, and labelling it as power invites bad energy dashboards.
        sensor("nominal_power", "Nominal Power Draw", unit="W",
               icon="mdi:flash-outline"),
        sensor("lifetime_energy", "Lifetime Energy", unit="kWh",
               device_class="energy", state_class="total_increasing"),
        sensor("setpoint", "Setpoint", unit="°F", device_class="temperature"),
        binary_sensor("heating", "Heating", device_class="heat"),
        binary_sensor("sgd_error", "SGD Error", device_class="problem"),
    ]
    select_config = {
        "name": "Demand Response",
        "state_topic": STATE_TOPIC,
        "value_template": "{{ value_json.demand_response }}",
        "command_topic": COMMAND_TOPIC,
        "options": list(DR_MODES),
        "unique_id": f"{NODE_ID}_demand_response",
        "availability_topic": AVAILABILITY_TOPIC,
        "icon": "mdi:transmission-tower",
    }
    entities.append(
        (f"{DISCOVERY_PREFIX}/select/{NODE_ID}/demand_response/config",
         select_config))
    return entities


class Daemon:
    def __init__(self):
        self.tank = Tank(PORT_PATH)
        self.client = self.make_client()
        self.device = {}
        self.mode = "Normal"
        self.pending_mode = None
        self.last_heartbeat = 0.0
        self.last_event = 0.0
        self.running = True
        # Lets an incoming command cut the poll sleep short. Commands are still
        # executed on the polling thread -- the serial link is half duplex and
        # not reentrant -- but they no longer wait out the full interval.
        self.wake = threading.Event()

    def make_client(self):
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=NODE_ID)
        except AttributeError:                    # paho-mqtt 1.x
            client = mqtt.Client(client_id=NODE_ID)
        if MQTT_USER:
            client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        client.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        return client

    # -- MQTT ---------------------------------------------------------------

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        log(f"mqtt connected ({reason_code})")
        client.subscribe(COMMAND_TOPIC)
        self.publish_discovery()
        client.publish(AVAILABILITY_TOPIC, "online", retain=True)

    def on_message(self, client, userdata, message):
        requested = message.payload.decode().strip()
        if requested not in DR_MODES:
            log(f"ignoring unknown demand response mode {requested!r}")
            return
        # Applied on the polling thread; the serial link is not reentrant.
        self.pending_mode = requested
        self.wake.set()

    def publish_discovery(self):
        info = {
            "identifiers": [NODE_ID],
            "manufacturer": "A.O. Smith",
            "name": "Water Heater",
            "model": self.device.get("model", "CTA-2045 HPWH"),
        }
        if self.device.get("serial"):
            info["serial_number"] = self.device["serial"]
        for topic, config in discovery_entities():
            config["device"] = info
            self.client.publish(topic, json.dumps(config), retain=True)

    # -- tank ---------------------------------------------------------------

    def apply_mode(self, mode):
        method = DR_MODES[mode]
        if method is None:
            accepted = self.tank.end_shed()
        else:
            accepted = getattr(self.tank, method)(EVENT_DURATION)
        if accepted:
            self.mode = mode
            self.last_event = time.monotonic()
            log(f"demand response -> {mode}")
        else:
            log(f"tank refused demand response mode {mode}")
        return accepted

    def adopt_mode(self):
        """Take over an event that was already running before this process.

        Forcing Normal at startup would silently cancel whatever was active,
        so a restart -- a deploy, a reboot, a crash -- used to quietly undo a
        demand-response event. The event is re-issued rather than left alone
        because the tank never reports how much of its own timer is left.
        """
        state = self.tank.operational_state()
        mode = ADOPTED_MODES.get(state[0]) if state else None
        if mode is None:
            self.apply_mode("Normal")
            return
        log(f"adopting {mode} already active on the tank ({state[1]})")
        self.apply_mode(mode)

    def poll(self):
        now = time.monotonic()

        if now - self.last_heartbeat >= HEARTBEAT_INTERVAL:
            if self.tank.heartbeat():
                self.last_heartbeat = now
            else:
                log("heartbeat not acknowledged")

        if self.pending_mode is not None:
            mode, self.pending_mode = self.pending_mode, None
            self.apply_mode(mode)
        elif self.mode != "Normal" and now - self.last_event >= EVENT_REISSUE:
            self.apply_mode(self.mode)

        state = self.tank.operational_state()
        records = self.tank.commodity()

        payload = {"demand_response": self.mode}

        if state:
            code, name = state
            payload["operational_state"] = name
            payload["heating"] = str(code in cta2045.HEATING_STATES).lower()
            payload["sgd_error"] = str(code == 5).lower()

        if records:
            rate, cumulative = records.get(cta2045.COMMODITY_ELECTRICITY, (0, 0))
            payload["nominal_power"] = rate
            payload["lifetime_energy"] = round(cumulative / 1000.0, 3)
            payload["storage_capacity"] = records.get(
                cta2045.COMMODITY_STORAGE_CAPACITY, (0, 0))[1]
            payload["energy_deficit"] = records.get(
                cta2045.COMMODITY_STORAGE_DEFICIT, (0, 0))[1]
            percent = cta2045.hot_water_percent(records)
            if percent is not None:
                payload["hot_water"] = percent

        setpoint = self.tank.setpoint()
        if setpoint is not None:
            payload["setpoint"] = setpoint

        self.client.publish(STATE_TOPIC, json.dumps(payload))
        return payload

    # -- lifecycle ----------------------------------------------------------

    def stop(self, *_):
        self.running = False
        self.wake.set()

    def run(self):
        self.device = self.tank.device_info()
        log(f"tank: {self.device.get('model', 'unknown')} "
            f"serial {self.device.get('serial', 'unknown')}")

        # Heartbeat first: a tank sitting in SGD Error reports that instead of
        # whatever event is running, which would make adoption read it wrong.
        self.tank.heartbeat()
        self.last_heartbeat = time.monotonic()
        self.adopt_mode()

        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_start()

        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        try:
            while self.running:
                try:
                    payload = self.poll()
                    log(f"{payload.get('operational_state', '?')} "
                        f"{payload.get('nominal_power', '?')}W "
                        f"hot water {payload.get('hot_water', '?')}%")
                except Exception as error:                # keep the loop alive
                    log(f"poll failed: {error!r}")
                # Returns early when a command arrives, so a select change in
                # Home Assistant reaches the tank in about a second rather than
                # waiting out the poll interval.
                if self.wake.wait(POLL_INTERVAL):
                    self.wake.clear()
        finally:
            self.client.publish(AVAILABILITY_TOPIC, "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()
            self.tank.close()
            log("stopped")


def main():
    if not MQTT_PASSWORD:
        log("MQTT_PASSWORD is not set; refusing to connect anonymously")
        return 1
    try:
        Daemon().run()
    except cta2045.PortBusy as error:
        log(f"{error}. Check for a stale reader: sudo fuser -v {PORT_PATH}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
