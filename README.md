# A.O. Smith heat pump water heater — local control over CTA-2045

Local monitoring and demand-response control of an A.O. Smith HPS10-50H45DV heat
pump water heater over its CTA-2045 "EcoPort" (RS-485), published to Home
Assistant via MQTT. No cloud account, no iCOMM dependency.

The manufacturer's cloud API has been unreliable enough here to be unusable for
automation — this site runs uptime monitors and a reload-on-recovery automation
because of it — and its hot-water reading is quantised to 0 / 50 / 100 %. The
tank's port instead supplies the two energy-take values needed to derive
hot-water percentage to 0.1 %, and one complete status poll takes about two
seconds.

## Hardware

| Part | Detail |
|---|---|
| Tank | A.O. Smith HPS10-50H45DV (Signature 900 hybrid) |
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

- **The data pins and the hot legs are at opposite ends of the same rows.**
  Data+ is the first position of its row and a 120 V leg is the last — four to
  five positions apart, with blanks and ground in between. The two hot legs are
  diagonal to each other and read 240 V across.
- **The connector's polarization key was dremeled off on this unit.** A small
  slip therefore lands on blanks or signal ground, but a **reversed insertion**
  swaps the ends of both rows and brings a data conductor onto a hot leg. Mark
  the port and the harness with a paint dot at the Data+ corner and check the
  orientation every time.
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
- Operational state (15 standard states — idle/running ×
  normal/curtailed/heightened, price-stream states, …)
- Total and present energy take capacity, in Wh
- Lifetime cumulative energy
- Setpoint (150 °F observed)
- Model and serial number

Write:

- Shed, End Shed, Load Up, Critical Peak, Grid Emergency — all accepted and
  reflected in the operational state
- **Price Stream** — a forecast of time/price pairs the tank acts on by itself
  (see below)

Not available on this tank, despite being in the spec:

- **Setpoint writes** return "not implemented".
- **Present water temperature** is refused: link NAK reason 7, response code 6.
- **Operating mode** (Hybrid / Electric / Heat Pump / Vacation) cannot be
  changed over this tank's CTA-2045 port. CTA-2045-B does define a mode-related
  SGD Efficiency Level command: this tank reports level 4, but every valid
  write from level 0 through 10 returns "not implemented." The mapping from
  level 4 to the front-panel mode is not published.

Additional CTA-2045-B features found by the audit but not yet exposed by the
daemon:

- Energy-reduction User Preference reads as level 5.
- UTC time and OTA metadata are readable.

Behavioural findings that are not in any datasheet:

- **Reported power is an estimate, not a measurement.** The tank clears the
  CTA-2045 "measured" bit and currently reports exactly 356 W while the
  compressor runs. Earlier element-on observations reported 4500 W, matching
  the two 4500 W elements in A.O. Smith's published specifications. Treat the
  value as a categorical "what is running"; use a clamp meter for real power.
- **Locally observed total energy-take capacity moved with the front-panel
  operating mode** — 7275 Wh in Electric against 4250 Wh in Hybrid. Compute the
  hot-water percentage from total and present take capacity in the *same* poll,
  never from a cached capacity.
- **Commodity code 7 is "present energy storage/take capacity": the energy the
  tank can take now.** For this water heater it behaves like the energy needed
  to become full, so the daemon calls it a deficit. Hot water %
  = `100 × (1 − present_take / total_take)`.
- **Earlier local tests found Shed and Load Up ineffective in Electric mode.**
  In Hybrid, the tested Load Up runs used the compressor and increased stored
  heat without an observed resistive-element transition. These are observations
  of this firmware, not behavior guaranteed by CTA-2045.
- **Basic Load Up cannot heat past the setpoint.** This is normative, not a
  quirk: §11.6 says basic Load Up "can only bring the water heater to set
  point," and only *Advanced* Load Up raises storage temperature above it.
  Advanced Load Up is gated behind capability bit 6, which this tank leaves
  clear; the spec conditions it on an ASSE 1017 thermostatic mixing valve
  being fitted, because without one the outlet can scald. So a Load Up that
  runs long and then stops has not hit some undocumented plateau — it filled
  the tank to setpoint.
- **The tank judges price relative to the forecast, not against a threshold.**
  Established by A/B test with the tank reset to a flat price and allowed to
  settle between phases:

  | Forecast | Result |
  |---|---|
  | cheap now, dear in 3 h | Running **Heightened** — heats while it is cheap |
  | dear now, cheap in 3 h | Running **Curtailed** — waits for the cheap window |
  | uniformly dear ($0.99/kWh flat) | **no reaction at all** |

  That last row is the informative one. An absolute threshold would have
  curtailed on a flat $0.99; nothing happened. Only the *shape* matters, so a
  flat forecast is how you say "no opinion", and the numbers can be real
  cents/kWh or an arbitrary normalized signal — the tank cannot tell.
- **It reacts within about 4 seconds**, and via the ordinary Curtailed and
  Heightened states rather than the dedicated "Running Price Stream" (13) and
  "Idle Price Stream" (14) codes, which were never observed.
- **The tank declared SGD Error about 13½ minutes after the last comm-status
  message in a controlled test.** Running Curtailed was still reported at
  810.154 seconds; the first SGD Error response arrived at 811.704 seconds.
  This masks the active event state but does not wipe the event: one heartbeat
  restored Running Curtailed. A comm-status heartbeat every 5 minutes is
  required by CTA-2045-B and avoids the error.

## Protocol notes

📄 **Read the standard — it is free.** CTA publishes the complete
[ANSI/CTA-2045-B (February 2021, 158 pp)](https://cdn.cta.tech/cta/media/media/ansi-cta-2045-b-final-2022.pdf)
on its own CDN, no account and no purchase. This project spent real effort
inferring things that are stated normatively in that PDF — the operating-state
codes (Table 10-3), the intermediate response codes (Table 11-2), and the
electrical interface (Appendix B) among them. Everything below is a note on
*this tank's* behaviour; the spec is the authority on what the protocol says.

Wire format is `msgType1, msgType2, reserved (3 zero bits), payload length
(13 bits, big endian), <length> payload bytes, 2-byte checksum`. Link ACK/NAK
are the exception: bare two-byte messages (`06 00`, `15 <reason>`) with no
length and no checksum. This tank ACKs the Basic (`08 01`), Intermediate
(`08 02`) and Data-Link (`08 03`) message types and rejects the other standard
message types tested with NAK reason 6.

The checksum is a Fletcher over mod-255 sums seeded `0xAA`:

```python
sum1, sum2 = 0xAA, 0x00
for byte in data:
    sum1 = (sum1 + byte) % 0xFF
    sum2 = (sum2 + sum1) % 0xFF
first  = 255 - ((sum1 + sum2) % 255)
second = 255 - ((sum1 + first) % 255)
```

Commodity responses are two op-code bytes, a response-code byte, then **13-byte
records**: commodity code (1) + instantaneous rate (6, big endian) + cumulative
amount (6, big endian). The commodity byte's high bit says measured (1) or
estimated (0); its low seven bits identify the commodity. Codes seen are
`0x00` electricity consumed, `0x06` total energy storage/take capacity,
`0x07` present energy storage/take capacity, `0x0a` Advanced Load Up total
capacity and `0x0b` Advanced Load Up present capacity. The last two currently
carry the same values as `0x06`/`0x07`, but their specified meanings differ.

For duration bytes `0x01` through `0xfe`, event durations encode as
`seconds = value² × 2`. The special value `0x00` means the duration is unknown,
not indefinite; `0xff` means longer than the representable maximum. This code
deliberately sends neither special value.

### Three things that cost real debugging time

1. **The bus is half duplex.** Transmitting while the tank is mid-response
   collides with it and destroys the frame. Responses are collected in silence
   and only acked once a complete frame is in hand. CTA-2045-B requires
   responses to be ACKed; this tank has continued without them in long local
   tests, so this implementation currently leaves response ACKs off by default
   as a device-specific interoperability choice.
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
tools/price-stream-probe.py         does the tank act on prices, or just accept them?
tools/price-stream-characterize.py  A/B test: relative shape vs absolute threshold
homeassistant/cards.py    Lovelace card definitions
homeassistant/patch_dashboard.py   installs them via the websocket API
ble/                      iCOMM Bluetooth LE protocol, recovered but a dead end
icomm/                    iCOMM cloud + LAN channels
mitm/                     TLS interception harness for the iCOMM cloud channel
```

## The Bluetooth side

CTA-2045 has no leak-sensor readout, and this tank rejects both the setpoint
write and the mode-related efficiency-level write. The iCOMM module exposes all
three over BLE, and its protocol was fully recovered by decompiling the app's
Hermes bundle — framing, CRC-8, session handshake, register map.

It does not work, and it is not going to. The module validates the PIN, holds an
authenticated link for minutes, accepts every frame at the ATT layer, and never
sends a notification back. The reason turned out not to be a wrong frame or a
missing state: **the official app offers no Bluetooth setup path for this product
family at all** — the setup method comes from cloud metadata — so BLE is not a
transport this firmware was ever expected to serve registers over. The vendor's
real provisioning path is SoftAP, and Ayla LAN mode is gated behind A.O. Smith's
own OEM account.

Two of the three gaps closed elsewhere in the meantime: mode and setpoint write
both work through the cloud API and Home Assistant's `aosmith` integration. The
leak sensor remains unreachable by any route.

`ble/` has the scripts, the register tables, and [the full write-up](ble/README.md)
— everything ruled out, why it is ruled out, and what is left.

## The cloud and LAN side

The module's other channels were mapped from a man-in-the-middle position on the
tank Pi: a unicast ARP redirect that puts the Pi between the module and the
gateway, bounded and self-restoring. Two cloud channels exist — an encrypted UDP
ops stream on port `55055` to a GCP pool behind `ans-field.aylanetworks.com`, and
periodic TLS 1.2 to `proxy-ingress-usfield.aylanetworks.com`.

The TLS client offers four legacy CBC suites, sends no SNI, presents no client
certificate — and **does not validate the chain at all.** A certificate from
mitmproxy's own throwaway CA completes the handshake and carries the session, so
the cloud protocol can be read in clear locally.

What defeated the first attempts was the signature algorithm, not the CA. The
module advertises `rsa_pkcs1_sha256/384/512` and `ecdsa_sha256` (**P-256 only**) —
no P-384 — and everything signable from the recovered eMudhra intermediate is
P-384, which deadlocks the handshake for its full 30-second timeout and looks
exactly like a trust rejection. An RSA-2048, SHA-256 leaf replaces it; no
procurement required, and no root to chase. The UDP stream is a separate 40-byte
custom protocol that TLS keys would not open anyway.

[`mitm/README.md`](mitm/README.md) has the measured capability table — cipher
suites, leaf key type, signature algorithm, SNI, cadence, timeouts — each with
the evidence that establishes it, so none of it has to be re-derived.

[`mitm/`](mitm/README.md) is the harness for that: a reverse proxy that
terminates the module's TLS with an `*.aylanetworks.com` certificate, logs every
flow in clear, and can inject responses. The certificate it needs is an
**RSA-2048** leaf — the module offers no ECDSA suite, so an EC key cannot be
negotiated at all. `mitm/check-cert.sh` answers that in one line.

[`icomm/README.md`](icomm/README.md) has the five experiments, the evidence, the
TLS fingerprint, the captures, and the mistakes that produced wrong conclusions
along the way.

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

Thirteen entities arrive by MQTT discovery as a single device:

| Entity | Notes |
|---|---|
| `sensor.*_operational_state` | e.g. Running Heightened |
| `sensor.*_hot_water` | % |
| `sensor.*_energy_deficit` | Wh; CTA-2045 "present energy take capacity" |
| `sensor.*_storage_capacity` | Wh; CTA-2045 "total energy take capacity" |
| `sensor.*_nominal_power_draw` | estimated/categorical, deliberately **not** `device_class: power` |
| `sensor.*_lifetime_energy` | kWh, `total_increasing` |
| `sensor.*_setpoint` | °F, read only |
| `binary_sensor.*_heating` | |
| `binary_sensor.*_sgd_error` | tank lost the RS-485 link |
| `select.*_demand_response` | Normal / Shed / Load Up / Critical Peak / Grid Emergency |
| `sensor.*_current_price` | USD/kWh in effect now, from the live forecast |
| `sensor.*_price_response` | Heightened / Curtailed / Following / Inactive |
| `binary_sensor.*_price_stream_active` | a forecast is live and unexpired |

The select is the only writable *entity*; the other way in is the price topic
below.

`price_response` is the daemon's reading of *why* the tank is in the state it
is in. The tank never says, so this only attributes Curtailed and Heightened to
price while a forecast is live **and** the daemon is not itself running an
event — otherwise it reports `Inactive`.

### Price Stream

Publish a forecast to `cta2045/waterheater/price_stream/set`, either as a bare
list of `[unix_time, price]` pairs or as an object:

```jsonc
{"pairs": [[1785243600, 14000], [1785265200, 28000]],
 "digits": 5,        // price is scaled by 10^digits, so 14000 = $0.14000
 "currency": 840}    // ISO 4217, USD
```

Publishing the literal string `invalidate` sends the §11.8.3.3 "no valid
prices" message. Note that this marks the stored forecast **stale rather than
deleting it** — the tank keeps acting on the shape it has. To actually stand
the tank down, send a *flat* forecast.

Two limits are negotiated with the tank at startup rather than assumed: the
maximum payload (256 bytes here, so 31 pairs per message) and how many pairs it
will store — **ten** on this tank. A longer schedule is trimmed to the ten
starting from the price in effect now, so publishing a whole day's rates works;
just re-publish periodically so the window slides forward. The daemon logs the
trim and reports `price_now` from the trimmed set, since that is what the tank
is actually holding.

The forecast is also written to `/var/lib/cta2045-mqtt/prices.json`. That is
not a cache: the tank has no way to read a stored forecast back (§11.8.4's
"export" is about selling prices), so without a local copy a restart would find
the tank Heightened, mistake price-driven behavior for an event, and issue a
real Load Up on top of it — then re-issue that forever. With the copy, the
daemon recognises the forecast and leaves the tank alone.

Commands are applied on the polling thread — the link is half duplex and not
reentrant — but an incoming command cuts the poll sleep short, so a change in
Home Assistant reaches the tank in about a second.

On startup the daemon reads the operational state and **adopts** any event
already running, so a restart or reboot no longer silently cancels it. The tank
reports the effect rather than the command, so Curtailed is adopted as Shed and
Heightened as Load Up — unless a price forecast is live, which produces those
same states on its own and must not be adopted.

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

- [ANSI/CTA-2045-B](https://cdn.cta.tech/cta/media/media/ansi-cta-2045-b-final-2022.pdf)
  — the published protocol and electrical-interface specification.
- [epri-dev/CTA-2045-UCM-CPP-Library](https://github.com/epri-dev/CTA-2045-UCM-CPP-Library)
  — EPRI's CTA-2045-A-era reference implementation, and the bring-up stack
  here. It predates the revision-B efficiency-level, price-stream and Advanced
  Load Up additions.
- [epri-dev/CTA-2045-Desktop-Simulator](https://github.com/epri-dev/CTA-2045-Desktop-Simulator)
  — another useful implementation of the intermediate response-code table,
  which is normative in CTA-2045-B Table 11-2.
- [EPRI/OpenADR mapping guide](https://www.bpa.gov/-/media/Aep/energy-efficiency/emerging-technologies/ET-Documents/epri-report_openadr-2045-guidelines-may-2019-000000003002008854.pdf)
  — additional integration guidance. The normative operational-state
  enumeration is in CTA-2045-B itself.

## Caveats

Everything here was worked out against **one** tank — an HPS10-50H45DV running
device rev 2.14. Capability bitmaps and unimplemented commands vary by model
and firmware, so treat the "not available" list as a finding about this unit
rather than about A.O. Smith generally.

The tank is not a metering device. Use a clamp meter for real power.

## License

MIT — see [LICENSE](LICENSE).
