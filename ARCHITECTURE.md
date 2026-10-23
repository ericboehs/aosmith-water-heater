# ARCHITECTURE.md — the iCOMM module, end to end

How the A.O. Smith HPS10-50H45DV's iCOMM module talks to the world, how we learned
it, and what can be done with each channel. This is the map; the per-channel
documents carry the detail:

| Document | Covers |
|---|---|
| [README.md](README.md) | the CTA-2045 port (RS-485, MQTT, demand response) |
| [ble/README.md](ble/README.md) | the BLE interface: pairing, the register map, LAN mode |
| [icomm/README.md](icomm/README.md) | the cloud protocol: telemetry fields, the command frame |
| [mitm/README.md](mitm/README.md) | interception: TLS, the tooling, the behavioral experiments |
| [PLUGIN.md](PLUGIN.md) | the forward plan: a local plugin that keeps the cloud working |

Everything here was measured against **one** appliance. Where a number is a
measurement, it is stated as one; where it is a hypothesis, it is marked. The
narrative keeps the wrong turns — the ones below each cost an evening, and several
cost three — because *how* the wrong answer was reached is part of the result.

## The appliance, in one table

| Thing | Value |
|---|---|
| Tank | A.O. Smith HPS10-50H45DV (Signature 900 hybrid heat pump) |
| iCOMM module | Wi-Fi cloud gateway, `host_symname: NextGenHP`, device rev 2.14 |
| Module IP / MAC | `10.0.1.187` / `d0:17:69:f8:f3:1c` |
| DSN (Ayla serial) | `AC000W019949045` |
| Ayla device id | `29689890` |
| Cloud ingress | `proxy-ingress-usfield.aylanetworks.com` (`35.186.226.188:443`) |
| Ops channel | UDP 55055–55057 → `ans-field.aylanetworks.com` (rotates; one capture: `34.70.101.46`) |
| Modes | heat pump (`EFFICIENCY`, app name "Eco"), hybrid, electric; 95–150 °F setpoints |

## The five interfaces

```
                 ┌────────────────────────────────────────────────┐
                 │  iCOMM module (inside the tank's lower shroud) │
                 └────────────────────────────────────────────────┘
   TLS 1.2, no SNI            UDP keepalive          GATT            RS-485
   4-suite CBC               40 B nonce             pairing-        + 240 V !!
   (never checks              chain, no               locked          CTA-2045
    the chain)                payload                                "EcoPort"
       │                        │                     │                │
  Ayla cloud ──────────── the mobile app          the app, local   Pi + MQTT
  (telemetry +             (cloud only;           (if it owned     (this repo's
   commands)                LAN mode dead)         the keys)         main project)
```

| Interface | Carries | Can we use it? |
|---|---|---|
| **HTTPS cloud** | telemetry up, commands down, re-registration | yes — interception works; see below |
| **UDP ops** | liveness nonce to Ayla's ops service | observable only; no command path (proven) |
| **BLE** | full register map — mode, setpoint, everything | pairing-locked by an account we don't own; writes possible for the owner's app, not yet for us |
| **CTA-2045** | status, energy take, load control (not setpoint/mode) | fully local, 2 s polls, this repo's `cta2045-mqtt` |
| **HTTP :80** | `regtoken.json` (LAN pairing teaser) and a test page | LAN mode requires OEM-owned keys; dead end (proven) |

The design consequence: the two channels that are *open* to us are the cloud
channel (read-rich, write-slow) and CTA-2045 (instant, but no setpoint/mode).
BLE is the instant write path if its pairing can be satisfied. That tension shapes
everything in [PLUGIN.md](PLUGIN.md).

## The cloud channel

### TLS: the appliance never checks the certificate chain

The module's ClientHello is a 2013-era fingerprint:

- record version `0x0301`, client version `0x0303`, 82 bytes, **no SNI**, no
  supported_groups, no client cert;
- four cipher suites: `DHE-RSA-AES256-SHA`, `AES128-SHA`, `AES256-SHA`, `SSL_RSA_FIPS`
  (`0039/002f/0035/00ff`) — all CBC, one DHE;
- signature algorithms `rsa_pkcs1_sha512/384/256`, `ecdsa_se P-256`, and
  `rsa_pkcs1_sha1` — **no P-384 scheme anywhere**;
- extensions: `encrypt_then_mac`, `extended_master_secret`.

The finding that unlocked everything: **any RSA-2048 leaf signed with SHA-256 and
named `*.aylanetworks.com` completes the handshake and the module sends application
data** — including mitmproxy's throwaway self-signed CA. There is no trust store
consulted at all.

The trap that occupied an evening first: an eMudhra-origin ECDSA **P-384** leaf
(copied from Ayla's live cert) does not fail — it **deadlocks**. The module goes
silent for exactly 30.000 s after `ServerHelloDone`, then RSTs, no alert, retries
every 30 s. Silence that reads exactly like a certificate rejection is a signature
algorithm mismatch. Two conclusions were drawn from that, in order: "the signature
algorithm is the gate" (wrong — it is a *precondition*), then "there is no gate"
(right). Also: the `ClientKeyExchange` arrives ~5.7 s after `ServerHelloDone` because
RSA-2048 modexp is slow on this hardware — budget 15 s before calling a pause a
failure.

### The conversation: four requests, always in this order

When something happens cloud-side, the module opens one connection and does:

1. `POST /devices/<id>/properties/superBlockDyn1/datapoints.xml` — telemetry (five
   hex blocks, comma-separated, in the request body);
2. `GET /devices/<id>/commands.xml?signal=<RSSI>` — poll for commands;
3. if a command was returned: `POST .../setBlockParameter/datapoints.xml` with an
   empty body — acknowledgement;
4. telemetry again, reflecting the change.

Ayla's replies to the telemetry posts are **empty 200s** — there is no in-band way
to tell the module a command is waiting from a telemetry response (tested; see the
narrative).

### The command frame: the BLE framing, different map

```
BD  <cmd=0x40>  <total_len>  <block>  <offset>  <value...>  <crc8>
```

- `total_len` counts the whole frame including the CRC — same convention as the
  BLE side's `BD F2 05 01 D8`;
- the trailing byte is `crc8` from the table brute-forced out of the app bundle
  (already in `ble/icomm_ble.py`);
- the setpoint is block `0x0B` (11) offset `0x06`, value big-endian **1/256 °C**
  (122 °F → 12800 → `BD 40 08 0B 06 32 00 56`);
- the *cloud's* map differs from the BLE map (setpoint over BLE is 13/2): the frames
  are the same language, the atlases are not interchangeable.

`mitm/frame.py` regenerates the frames Ayla actually sent, byte for byte, and
builds new ones (`--mode electric` → `BD 40 07 1A 00 20 B2` per the BLE map's
MODE_BASE10).

### The telemetry: five blocks, 1/256 °C throughout

Blocks are `[8, 146, 230, 52, 52]` bytes. The confirmed fields (full table with
evidence in [icomm/README.md](icomm/README.md)):

| Field | Where |
|---|---|
| setpoint (×6 mirrors) | `b[3]` 0, 12; `b[1]` 80, 84, 112, 114 |
| water temperature, tank top | `b[1]` 0 |
| water temperature, tank bottom | `b[1]` 4 |
| max setpoint | `b[1]` 86 |
| mode | `b[3]` 30, 31 — electric `100/1`, heat-pump `0/5` |
| element power (rated) | `b[4]` 12–13 — 4500 W on, 0 off |
| line voltage | `b[1]` 99 — 235 V |
| air at the unit | `b[4]` 30 — 41–49 °F heat pump, 82–83 °F electric |
| Wi-Fi RSSI | `b[4]` 40, signed 16-bit |
| device clock | `b[1]` 70–73, Unix time, local offset |
| tank physically full of water | `b[3]` 35, 39 (always 100) |

Not found anywhere: the app's "hot water available %" (its 0/50/100 quantised
figure) and a measured power value. The app's watts are *derived*:
`4500 × (235/240)² = 4314 W` — its 4315 to within a watt, from a resistive element
following P ∝ V².

### Behavior: event-driven, backoff-heavy, un-wakeable

Measured, one variable per window:

- **Dial cadence**: ~60 s while the tank has state to report (a heat-up window
  showed dials at 19:51:16 and 19:52:16, neither tied to a cloud write).
- **Idle**: a satisfied tank goes silent. Twenty minutes armed, no writes:
  **zero** HTTPS requests — while ops keepalives kept flowing every ~80 s.
- **Polls are rare**: 1 in 9 min, then 0 in 6. A command is only delivered in reply
  to a poll, so a write's latency is "whenever the module polls."
- **Nothing wakes it early**: blocking the ops channel (three tries), hanging up,
  holding polls open, cloud writes — none produced a dial or a poll while it was
  idle. Backoff after broken connections is measured in many minutes, and only
  time cleared it.
- **Degraded state**: enough broken connections and the module stops listening —
  dials every ~4 min for a single telemetry POST, 26-second connections, no polls,
  no long-poll fallback, and A.O. Smith's GraphQL fails every write with "an error
  occurred while attempting to communicate with your water heater" (its generic
  queued-but-never-acknowledged). The cure is un-intercepted time on real Ayla.
- **Mode writes**: A.O. Smith's GraphQL `updateMode` queues at Ayla, the module
  polls within seconds to ~2 min and fetches `BD 40 08 0B 0F <pair> <crc8>` —
  block 11 offset 15, the same two bytes the telemetry reports at `b[3]` 30/31.
  All three modes are captured: electric `64 01`, hybrid `00 04`, heat pump
  `00 05` — and local injection of the same frames flips the appliance within
  seconds. The BLE map's block-26 MODE register is acked and ignored over the
  cloud — the two maps are different atlases.
- **Single-seat sessions**: the A.O. Smith account allows one active session. HA's
  integration keeps re-taking it — the mobile app gets logged out immediately, and
  a direct script's write got 401 twice, including after a fresh login, while its
  own login+read had just succeeded. A local plugin that uses the account must
  expect this fight; the relay design never needs the account at all.
- **Re-registration and recovery**: after a day of broken connections the module fetched
  `GET /dsns/<DSN>/regtoken.xml` on its next dial — the first time that path has
  appeared. It re-provisions itself before returning to the normal conversation.
  Measured overnight: re-registration at arm, then **26.6 minutes of silence**,
  then telemetry resumed at its ~60 s cadence. That 26.6 minutes is the recovery
  number — the module's backoff after its connections are broken.
- **Polls only on notification**: five overnight telemetry posts, zero polls —
  and the morning's queued 140 °F landed on the first poll after disarm, seconds
  after real Ayla became reachable. The module does not poll on any schedule;
  a poll happens when Ayla says a command exists. Every successful injection in
  this repo rode a poll that existed for another reason, which is the central
  constraint on the local write path.
- **Conntrack**: arming a DNAT does nothing to an established connection — the
  module keeps using its live one to real Ayla until `conntrack -D` breaks it
  (six armed minutes with zero requests proved this before it was fixed).

## The ops channel: neither signed nor encrypted — nor useful

Every packet, both directions, is exactly **40 bytes**:

```
04 00 00 00 3f b8 0f 00 | <16 bytes> | <16 bytes>
```

Header `0x000fb83f` constant; the two 16-byte blocks chain **per direction** — each
side echoes its own previous second block as its next first block and appends a
fresh random one. It is a rolling-token liveness keepalive: 40 fixed bytes leave no
room for a nonce *and* a MAC, let alone a payload, and no packet ever correlated
with a cloud command. It cannot carry a command, and it cannot be used to wake the
module (tested three ways). Decode it further and you learn nothing; the value was
in *proving* it is a dead end.

## Interception: the transparent relay

The working architecture, in one picture:

```
module ──DNAT/SNAT──▶ relay ──forward──▶ Ayla ingress
                      │  ▲
              rewrite only    │
              when we have    │ upstream health budget
              something to    ▼
              say       synthetic 200s while degraded
```

- **Pass-through by default.** Ayla must keep seeing the device's polls or the cloud
  declares it offline and Home Assistant shows `unavailable` — measured during a
  local-answering session. The relay forwards everything and rewrites a reply only
  when a frame is queued (`inject` rules in `mitm/ayla-mitm.py`).
- **Rewrite, don't originate.** Commands are only ever delivered as the *reply* to
  the module's own poll; the relay cannot push. This keeps the protocol shape the
  module expects.
- **Steering**: gateway DNAT+SNAT with a conntrack flush. A DNS override does
  **not** work — the module queries 1.1.1.1 directly for its endpoints, ignoring
  the gateway's resolver entirely (measured on the wire). ARP wedges this module.
- **Degradation is the product requirement**: Ayla is often down or slow — a
  Home Assistant write hung for 90 s three times in one night while reads answered
  in 19 ms. The relay must answer the *module* locally when upstream fails, so the
  appliance never learns the cloud was gone. (Designed, not yet built — see
  [PLUGIN.md](PLUGIN.md).)

## How we got there

A timeline of the load-bearing experiments, including the ones that produced wrong
answers.

**1 — The certificate gate (two wrong answers before the right one).** The first
approach treated the handshake as a trust problem: recover Ayla's actual
certificate chain, re-issue a leaf from the same CA (eMudhra `EM OV TLS CA - G3C`,
ECDSA P-384). Result: 30 s of silence, RST, retry — *no alert*. First conclusion:
"the signature algorithm is the gate" — P-384 is absent from the module's
signature_algorithms. Then a plain RSA-2048/SHA-256 leaf from mitmproxy's throwaway
CA completed the handshake and application data flowed: **there was no gate**. The
P-384 deadlock was a precondition failure that *looked like* a trust rejection.
Lesson: silent failure modes lie; change one variable and re-measure.

**2 — The conversation (an 8-minute window, three setpoint changes).** Every
cloud-side change opened one connection with the same four requests. Nothing else
was ever queued — no schedules, no other command types. That window also produced
the setpoint frames that cracked the format.

**3 — The frame (two wrong parses before the right one).** The trailing three bytes
were first read as "a 2-byte value plus a checksum byte" — then as Q16.16 fixed
point, which fit *suspiciously well* (a warning about coincidences). The truth: the
last byte is `crc8` (the table already recovered from the app bundle) and the value
is 2-byte 1/256 °C. Then `08 0B 06` resolved as length + block + offset, matching
the BLE convention. The proof was regenerating all three observed frames byte for
byte, then building one the cloud never sent — 125 °F — and having the appliance
obey it, confirmed from its own telemetry.

**4 — The field map (three readings of the same two bytes).** `b[3]` 30/31 was
called mode, then corrected to element activity, then put back to mode. What settled
it: the air sensor. A running heat pump exhausts cold (41–49 °F) and an electric
element does not (82–83 °F) — the discriminator that separates two fields that
co-vary whenever the tank is heating. Related errors kept in the docs: `b[1]`
6–12 are zeros that *read as* 32.0 °F, which is how a wrong mode field got picked;
the `100`s in `b[3]` 35/39 are physical tank fullness, not hot-water availability.

**5 — The doorbell (a retraction).** "Ayla's ops channel is the doorbell" was
committed after a poll appeared seconds after a cloud write. The gateway capture
then showed that poll *preceding* its own write by four seconds — correlation read
backwards. Retracted in the next commit, with the evidence. The module dials on its
own cadence; nothing in-band hurries it. (The ops-block idea then failed twice more
as a wake-up lever — three strikes, documented.)

**6 — The hold and the hangup (two obviously-right ideas, both wrong).** Holding
the poll open should have created a push channel — instead it starved the module,
which went silent for five minutes after. Hanging up after each telemetry post
should have provoked re-dials and polls — zero polls in 181 s. Both are recorded
with their numbers; they are the design constraints that rule out "instant writes
over the cloud channel."

**7 — Watts and the ops channel (one diff, one decode).** An element-on window vs
an element-off window located 4500 W at `b[4]` 12–13 and 235 V at `b[1]` 99, and the
app's 4315 W fell out as `4500 × (235/240)²`. The same night's gateway capture
decoded the ops packet structure — nonce chain, no payload, dead end proven rather
than assumed. (Also learned expensively: the module's VLAN is only visible on the
gateway's `br0`; two earlier captures on `switch0`/`eth9` were empty files, not
empty traffic.)

**8 — Idle silence and conntrack.** The rebuilt session script exposed both: a
flat tank produces *no traffic at all* (so a session needs a cloud write to be worth
running), and DNAT does not touch established connections (so `conntrack -D` is the
nudge that forces a re-dial).

**9 — Invisible to the cloud (the architecture lesson).** Injecting by answering
polls locally made Home Assistant report the device `unavailable` for the whole
session — Ayla had stopped seeing it, and a mid-session cloud write could not reach
the appliance. The fix is the transparent relay: forward upstream, rewrite only the
reply. With that in place, HA kept a live state and cloud writes kept flowing while
armed.

**10 — Ayla's own fragility (the requirement).** One night of HA writes hanging
40–90 s while reads answered instantly, surviving an HA core restart, and the
module holding a long backoff — that is the environment the plugin must treat as
normal. Local-first, cloud-preserving, degrade-gracefully: [PLUGIN.md](PLUGIN.md).

## The method (what made the map trustworthy)

- **One variable per window.** Every claim above is a diff between two windows that
  differ in exactly one thing. The element-on/element-off pair located watts; the
  air-temperature discriminator settled mode.
- **Wrong turns are kept, corrected in public.** Each wrong claim above was
  committed, then retracted with the measurement that killed it and the reason it
  looked right. The root causes are documented (zeros reading as 32.0 °F, a poll
  preceding its own write, P-384 silence reading as rejection).
- **Ground truth beats telemetry.** The appliance's own telemetry confirmed every
  write (125 °F, 148 °F), and the app/HA states were cross-checked against decoded
  blocks rather than trusted.
- **Negative results are results.** "Nothing wakes the module" and "the ops channel
  carries nothing" each took hours and each eliminated a design branch.
- **Tools encode the findings.** `frame.py` regenerates known-good frames (a
  regression test for the codec), `summarise.py` decodes sessions the same way every
  time, and the session scripts refuse to run with the known failure modes armed
  (taken port, dead mitmdump, un-armed redirect).

## What is still unknown

| Question | Why it matters |
|---|---|
| What triggers a poll at all? | write latency; nothing found yet that provokes one |
| Where does the app's hot-water % come from? | a read-only plugin ships without it |
| Can BLE pairing be satisfied by anyone but the OEM account? | the only instant write path |
| What makes the module's backoff end? | recovery time after every cloud outage |
