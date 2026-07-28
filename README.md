# A.O. Smith heat pump water heater — local control over CTA-2045

Local monitoring and demand-response control of an A.O. Smith HPS10-50H45DV heat
pump water heater over its CTA-2045 "EcoPort" (RS-485), published to Home
Assistant via MQTT. No cloud account, no iCOMM dependency.

The manufacturer's cloud API goes down often enough to be useless for
automation, and its hot-water reading is quantised to 0 / 50 / 100 %. The tank's
own port reports the same figure to a tenth of a percent and answers in about
two seconds.

## Hardware

| Part | Detail |
|---|---|
| Tank | A.O. Smith HPS10-50H45DV (Voltex hybrid) |
| Adapter | Raspberry Pi Zero 2 W + Waveshare isolated RS-485 HAT |
| Port | `/dev/serial0` → `ttyAMA0` (PL011), **19200 8N1** |
| OS | Raspberry Pi OS Lite (64-bit), Debian 13 trixie, Python 3.13 |

The Pi needs `enable_uart=1`, `dtoverlay=miniuart-bt` and `core_freq=250` in
`/boot/firmware/config.txt` so the real PL011 lands on the GPIO header rather
than the mini-UART.

### ⚠️ There is 240 V in this connector

This is the **AC form factor** of CTA-2045 — two 120 V hot legs share the port
with the RS-485 pair, and there is no neutral. It is not the low-voltage SPI
variant used in EPRI's Arduino simulator. Read this section before touching the
port.

**The rows are swapped relative to the spec diagram on this unit.** Top row is
pins 7–12 left→right, bottom row is pins 1–6 left→right. Verify with a meter
before trusting any harness or pinout drawing, including this one.

| Row | | | | | | |
|---|---|---|---|---|---|---|
| **Top** | **Data+** (7) | Signal GND (8) | — | Earth GND (10) | — | **120 V L1** (12) |
| **Bottom** | **Data−** (1) | — | — | — | **120 V L2** (5) | — |

Only three wires are needed: **Data+ (7), Data− (1), Signal GND (8)**. Never
touch pins 5 and 12.

- **The connector's polarization key was dremeled off on this unit.** Mark the
  port and the harness with a paint dot at the Data+ corner. One position of
  slip puts 120 V onto the RS-485 pair.
- **Use an isolated transceiver.** The data pins sit millimetres from 240 V and
  signal ground is bonded to earth.
- Healthy idle: Data+ 3.232 V, Data− 1.592 V, 1.640 V differential — a 3.3 V
  transceiver with failsafe bias resistors.
- **9.57 Ω across A+/B− with the board unpowered is normal** — substrate diodes
  plus the TVS network, not a short. Powered, it reads `OL`.

The port carries **one** differential pair, so the bus is half duplex in the
wiring, not in software. No UART setting makes it otherwise.

## What works, and what does not

Read:

- Hot water percentage, to 0.1 %
- Operational state (13 states — idle/running × normal/curtailed/heightened, …)
- Energy storage capacity and deficit, in Wh
- Lifetime cumulative energy
- Setpoint (150 °F observed)
- Model and serial number

Write:

- Shed, End Shed, Load Up, Critical Peak, Grid Emergency — all accepted and
  reflected in the operational state

Not available on this tank, despite being in the spec:

- **Setpoint writes** return "not implemented".
- **Present water temperature** is refused: link NAK reason 7, response code 6.
- **Operating mode** (Hybrid / Electric / Heat Pump / Vacation) has no CTA-2045
  command at all. It is front-panel only.

Behavioural findings that are not in any datasheet:

- **Reported power is a nameplate constant, not a measurement.** The tank
  reports exactly 4500 W with the element energised and exactly 356 W on the
  compressor, byte-identical across hundreds of polls. Treat it as a
  categorical "what is running". For real power, use a clamp meter.
- **Storage capacity moves with the operating mode** — 7275 Wh in Electric
  against 4250 Wh in Hybrid — so the hot-water percentage must be computed from
  the capacity and deficit of the *same* poll, never a cached capacity.
- **Commodity code 7 is a deficit, not a level.** It rises as hot water is
  drawn. Hot water % = `100 × (1 − deficit / capacity)`.
- **Shed and Load Up are no-ops in Electric mode** — there is no compressor to
  shift onto. In Hybrid, Load Up raises the target and keeps the compressor
  running but never engages the resistive element, so it banks heat sooner
  without drawing more power.
- **The tank declares SGD Error about 14 minutes after the last comm-status
  message, and that wipes any active event.** A heartbeat every 5 minutes is
  mandatory, not optional.

## Protocol notes

Wire format is `msgType1, msgType2, length (2 bytes big endian), <length>
payload bytes, 2-byte checksum`. Link ACK/NAK are the exception: bare two-byte
messages (`06 00`, `15 <reason>`) with no length and no checksum.

The checksum is a Fletcher over mod-255 sums seeded `0xAA`:

```python
sum1, sum2 = 0xAA, 0x00
for byte in data:
    sum1 = (sum1 + byte) % 0xFF
    sum2 = (sum2 + sum1) % 0xFF
first  = 255 - ((sum1 + sum2) % 255)
second = 255 - ((sum1 + first) % 255)
```

Commodity responses are two op-code bytes, one lead byte, then **13-byte
records**: commodity code (1) + instantaneous rate (6, big endian) + cumulative
amount (6, big endian). Codes seen: `0x00` electricity, `0x06` storage
capacity, `0x07` storage deficit; `0x0a`/`0x0b` mirror `0x06`/`0x07`.

Event durations encode as `seconds = value² × 2`. Zero means indefinite, which
this code deliberately never sends — an event that never expires outlives the
daemon that set it.

### Three things that cost real debugging time

1. **The bus is half duplex.** Transmitting while the tank is mid-response
   collides with it and destroys the frame. Responses are collected in silence
   and only acked once a complete frame is in hand — and the tank does not
   require acks at all, so they are off by default.
2. **The port must be held exclusively.** A second process on the same tty
   silently steals bytes: the driver's `TIOCGICOUNT` shows hundreds received
   while your own `read()` returns nothing. Every symptom looks like a protocol
   bug. `SerialPort` takes an `flock`, so a stale reader is a startup error
   rather than a mystery.
3. **The tank retransmits responses it thinks went unheard.** Bytes still in
   flight when the next request goes out get read as that request's answer, and
   consecutive polls drift permanently out of step. `Link.request` drains for
   400 ms after each exchange.

EPRI's `libcea2045` was tried first. It reported a stream of
`incomplete message received: 16/24/32` errors, which looked like a framing
bug — but those counts are multiples of 8, and the real cause was a forgotten
second process reading the same tty and taking bytes in 8-byte slices. **That
was not the library's fault**, and the finding is recorded here rather than
quietly dropped because it is exactly the kind of misattribution this bus
invites.

This implementation is kept because it is self-contained and debuggable — no
build step, no C++ toolchain, a trace hook on every byte, and the port lock
that makes the contention failure impossible to hit again.

## Layout

```
mqtt/cta2045.py           protocol: framing, checksum, parsing, Tank helpers
mqtt/cta2045_mqtt.py      the daemon: poll → MQTT with HA discovery
systemd/cta2045-mqtt.service
tools/tank-cli.py         manual status and demand-response control
tools/probe.py            raw-wire diagnostic; prints every frame
homeassistant/cards.py    Lovelace card definitions
homeassistant/patch_dashboard.py   installs them via the websocket API
ble/                      iCOMM Bluetooth LE protocol, recovered but blocked
```

## The Bluetooth side

CTA-2045 cannot read the leak sensor, cannot set the mode, and cannot set the
setpoint. The tank's iCOMM module exposes all three over BLE, and its protocol
was fully recovered by decompiling the app's Hermes bundle — framing, CRC-8,
session handshake, register map.

It does not work. The module accepts every frame at the ATT layer and never
sends a notification back. `ble/` has the scripts, the register tables, and
[a write-up of everything ruled out](ble/README.md), so the next attempt does
not start from zero.

## Install

```bash
sudo apt install python3-paho-mqtt
mkdir -p ~/cta2045-mqtt
cp mqtt/cta2045.py mqtt/cta2045_mqtt.py ~/cta2045-mqtt/

# Credentials, root owned, mode 0600 — never in the repo.
sudo install -m 0600 /dev/null /etc/cta2045-mqtt.env
sudo tee /etc/cta2045-mqtt.env >/dev/null <<'EOF'
MQTT_HOST=...
MQTT_USER=...
MQTT_PASSWORD=...
EOF

sudo cp systemd/cta2045-mqtt.service /etc/systemd/system/
sudo systemctl enable --now cta2045-mqtt
```

Everything else is environment driven: `CTA2045_PORT` (`/dev/serial0`),
`CTA2045_POLL_INTERVAL` (30 s), `CTA2045_HEARTBEAT_INTERVAL` (300 s),
`CTA2045_EVENT_DURATION` (1800 s), `CTA2045_EVENT_REISSUE` (600 s),
`MQTT_PORT`, `MQTT_TOPIC_BASE`, `MQTT_DISCOVERY_PREFIX`, `MQTT_NODE_ID`.

The daemon refuses to start without `MQTT_PASSWORD` rather than connecting
anonymously.

## Home Assistant

Ten entities arrive by MQTT discovery as a single device:

| Entity | Notes |
|---|---|
| `sensor.*_operational_state` | e.g. Running Heightened |
| `sensor.*_hot_water` | % |
| `sensor.*_energy_deficit` | Wh |
| `sensor.*_storage_capacity` | Wh, mode dependent |
| `sensor.*_nominal_power_draw` | nameplate, deliberately **not** `device_class: power` |
| `sensor.*_lifetime_energy` | kWh, `total_increasing` |
| `sensor.*_setpoint` | °F, read only |
| `binary_sensor.*_heating` | |
| `binary_sensor.*_sgd_error` | tank lost the RS-485 link |
| `select.*_demand_response` | Normal / Shed / Load Up / Critical Peak / Grid Emergency |

The select is the only writable control, because it is the only thing the tank
actually lets you change.

Commands are applied on the polling thread — the link is half duplex and not
reentrant — but an incoming command cuts the poll sleep short, so a change in
Home Assistant reaches the tank in about a second.

On startup the daemon reads the operational state and **adopts** any event
already running, so a restart or reboot no longer silently cancels it. The tank
reports the effect rather than the command, so Curtailed is adopted as Shed and
Heightened as Load Up.

## Manual control

```bash
tools/tank-cli.py status
tools/tank-cli.py watch 30 20
tools/tank-cli.py loadup 45
tools/tank-cli.py normal
```

These take the same lock as the daemon, so running one while the service is up
fails loudly instead of stealing its bytes. Stop the service first.

## Wi-Fi

On a Pi Zero 2 W, disable Broadcom power save or expect ~20 % packet loss and
300 ms latency spikes on an otherwise strong link:

```
# /etc/NetworkManager/conf.d/wifi-powersave-off.conf
[connection]
wifi.powersave = 2
```

Measured before and after on an otherwise strong link (−50 dBm, 60/70, no
kernel errors): 20 % → 0 % loss, 77.8 ms → 14.5 ms average.

## Prior art

- [epri-dev/CTA-2045-UCM-CPP-Library](https://github.com/epri-dev/CTA-2045-UCM-CPP-Library)
  — the reference implementation, and the bring-up stack here.
- [epri-dev/CTA-2045-Desktop-Simulator](https://github.com/epri-dev/CTA-2045-Desktop-Simulator)
  — the intermediate response-code table is in `Main_Form.Designer.vb`, and is
  not in any free PDF.
- [EPRI/OpenADR mapping guide](https://www.bpa.gov/-/media/Aep/energy-efficiency/emerging-technologies/ET-Documents/epri-report_openadr-2045-guidelines-may-2019-000000003002008854.pdf)
  — source of the operational-state enumeration.

The CTA-2045-B standard itself is paid, which is why several fields here are
described by observed behaviour rather than by citation.

## Caveats

Everything here was worked out against **one** tank — an HPS10-50H45DV running
device rev 2.14. Capability bitmaps and unimplemented commands vary by model
and firmware, so treat the "not available" list as a finding about this unit
rather than about A.O. Smith generally.

The tank is not a metering device. Use a clamp meter for real power.

## License

MIT — see [LICENSE](LICENSE).
