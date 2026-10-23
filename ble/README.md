# iCOMM Bluetooth LE — recovered protocol (dead end)

A parallel line of attack on the same tank. CTA-2045 gives good demand-response
control but cannot read the leak sensor, cannot report tank fullness directly,
and cannot set the mode or setpoint. The iCOMM module's BLE interface can do
all four — the app does exactly that.

**Status: authenticated, and the module still answers nothing.** The PIN is
verified correct by A/B against a wrong one, and an authenticated link now
stays up for minutes instead of being dropped at ~15 s — but every frame on
the `6940` pipe is accepted at the ATT layer and no notification ever comes
back. These scripts are the record of getting that far, kept so the next
attempt does not start from zero.

⚠️ **Read "BLE is the wrong door" before spending time here.** The official iOS
app offers no Bluetooth setup path for this product family at all, and the
bundle shows why. BLE is almost certainly not a transport this tank's firmware
was ever expected to serve registers over.

⚠️ **And the original motivation has largely dissolved.** Of the four things
CTA-2045 cannot do, **three now work through the cloud API** and Home
Assistant's own `aosmith` integration: mode (`eco`/`heat_pump`/`electric`),
setpoint write (95–150 °F), and tank fullness
(`sensor.*_hot_water_availability`). What remains genuinely unreachable is the
**leak sensor** — and one real gap in the cloud path, `current_temperature:
None`, i.e. no actual tank thermistor reading. If you want local tank
temperature, the practical answer is different hardware, not this radio (see
"Where this leaves it").

What is deliberately **not** here: the APK and its 31 MB decompile. Those are
A.O. Smith's. Register names, offsets and framing are interoperability facts
and are published in full below.

## Getting the module to advertise

This module is the **Bluetooth-button** variant: from lights-off, press the
Bluetooth button **once**; it beeps and the light starts flashing. Hold that
button 3 s to leave pairing mode early.

**Pairing mode expires by itself after 10 minutes**, and the module stops
advertising when it does — budget experiments accordingly, and re-enter pairing
mode rather than assuming a stale window is still open. It also goes quiet for
a while after rejecting a PIN.

⚠️ This **turns the tank's WiFi off**, which takes the cloud integration
offline until you turn it back on.

⚠️ The other module variant, with a **function button**, uses *press twice* for
pairing — and on that one a double-press from the *flashing green* state arms a
**factory reset** instead (a 5 s hold then commits it, clearing TOU schedules,
demand-response schedules and Wi-Fi profiles). Same gesture, different outcome
depending on the LED you start from. Not this module, but worth knowing before
following generic A.O. Smith instructions.

## The GATT map

| Service | Write | Notify |
|---|---|---|
| `69400001-B5A3-F393-E0A9-E50E24DCCA99` | `69400003` (write, write-no-resp) | `69400002` (notify + CCCD) |
| `7F510004-B5A3-F393-E0A9-E50E24DCCA9E` | `7F510006` (write, write-no-resp) | `7F510005` (notify + CCCD) |

Plus Generic Access (1800) and Generic Attribute (1801). No encryption
required, connects without bonding.

A 45 s watch of the advertisement (`advert.py`) shows **one static payload**,
never varying: complete local name, and service UUIDs `1800`, `1801`,
`69400001` and `7f510004` — so both vendor services *are* advertised. (An
earlier revision of this file said there were no advertised service UUIDs.
Wrong.) The manufacturer-specific data uses company ID `0x0000` — Ericsson's,
i.e. unregistered filler — and its payload is the module's own BLE address
repeated back. Nothing in the advertisement changes with tank state, so there
is no passive telemetry to scrape here.

The `B5A3-F393-E0A9-E50E24DCCA9E` suffix is the **exact Nordic UART Service
base UUID**, and the layout (service `…04`, notify `…05`, write `…06`) is
Nordic's pattern — two renamed Nordic UART pipes on an nRF5x. The same UUIDs
turn up in Neewer LED lights and LogiTow blocks, i.e. it is a chip-vendor
default, so **there is no prior reverse-engineering to borrow.**

Despite the module reporting an Ayla DSN, **no Ayla BLE services are present**
— the Ayla device-bridge UUIDs are absent entirely.

The address is a **BLE random static address, not a hardware MAC** (the top two
bits of the first octet are set), so it can change on power cycle and OUI
lookups return nothing. **Bind by name.** That is why `ICOMM_ADDR` is optional
in these scripts and a name scan is the default.

## What was recovered

From `com.aosmith.icomm` → React Native **Hermes bytecode** bundle
(`assets/index.android.bundle`, magic `c61fbc03c103191f`, version 96, 26,152
functions), decompiled with [P1sec/hermes-dec](https://github.com/P1sec/hermes-dec).
All the logic is JavaScript; `jadx` on the dex files is not needed.

Only the `6940xxxx` service is referenced by the app; `7F51xxxx` never appears
in the bundle or the APK. It was assumed to be nRF DFU — **wrong**, it is a
live ASCII console. See "Why it is blocked".

### Framing

```
read    BD A0 07 <block> <offset> 01 <crc>
write   BD 40 <len> <block> <offset> <value…> <crc>
commit  BD 07 <len> <block> <offset> 01 <crc>
resp    BD A0 <len> <block> <offset> <lo> <hi> <crc>
```

- **`len` is the total frame length including the CRC**, equivalently
  `nparams + 6`. Verified against six independent builders in the bundle:
  `F4 04` (3 bytes + crc), `F2 05 01` (4+1), `F0 16` (21+1), `F1 19` (24+1),
  `constructFrame`, and `querySingle`.
- Read value is `resp[6] << 8 | resp[5]` — little endian on the wire.
- Single-value writes encode as `00 <byte>`.
- **CRC-8, poly `0x5E`, init 0, non-reflected, no final XOR.** The table was
  lifted verbatim from the bundle and the polynomial confirmed by brute-forcing
  all 256. Worked example: `BD F2 05 01` → `D8`.
- Frames are built as uppercase space-separated hex strings in the app, then
  converted to raw bytes. On the wire it is plain binary — do not send ASCII hex.
- **Use write-with-response.** Earlier probing used write-without-response,
  which the module accepts and ignores.
- App timing: 3 s per-frame timeout, retry on timeout or CRC failure, 30 ms
  between frames, 500 ms settle before a set.

### Session handshake

The app authenticates before any block read:

| Frame | Meaning |
|---|---|
| *(ASCII write)* | 6-digit PIN derived from the advertised name |
| `BD F2 05 01` | list stored pair keys |
| `BD F0 16 <18 bytes>` | store our pair key |
| `BD F3 05 01` | delete first stored key (only when full) |
| `BD F4 04` | request a random number |
| `BD F1 19 01 <20 bytes>` | HMAC-SHA1(pairKey, random), first 20 bytes |

PIN derivation: `name.length > 21 ? name.slice(15, len-4) : name.slice(len-6)`,
default `'000000'`. For a 21-character `iCOMM-` name both branches happen to
give the same six digits.

Pair keys are **18 bytes** — the app uses the account contactID or the tank's
assetID, ASCII → hex; `00` ×18 marks an empty slot. ACK codes (`ACKTable`):
`0 unknown, 1 pairing timeout, 2 pair key full, 3 key not found,
4 session key timer expired, 5 random number not valid, 6 crc incorrect,
7 success`.

After the session the app sends `setEssentialParams()` — `remoteEnable()` to
block 13/9 and `setClock()` to 26/3+4, in parallel.

**Session frames carry the CRC like everything else.** An earlier reading of
this claimed the app dropped it — that `sendReceive(service, hex, expectedAck,
log)` shifted its logger argument into the `skipCrc` slot of
`sendAndRecieveHex`. That argument shift is not in the decompile: in
`sendAndRecieveHex` index 2 is the logger and index 3 is `skipCrc`, and
`_sendReceive` passes only three arguments, so `skipCrc` defaults false. The
first request on the wire is literally `BD F2 05 01 D8`, confirmed in a
`btmon` trace.

Two more corrections from the same re-read:

- **The HMAC arguments are the other way round.** The app calls
  `CryptoJS.HmacSHA1(pairKey, random)` and CryptoJS's signature is
  `(message, key)` — so the two random bytes are the **key** and the
  18-character pair key is the **message**.
- **The ACK "codes" are bit positions**, not integers. `checkACK` maps the set
  bit of the response's penultimate byte through `ACKTable`: `0x80` is success,
  `0x08` is "key not found". Both are acceptable replies to the first `F2`.

### Register map

Blocks are `EIGHT=8, THIRTEEN=13, TWENTY_SIX=26, TWENTY_SEVEN=27`. Block 26 is
the comms module's own map and holds everything CTA-2045 cannot reach.

| Block | Offset | Name |
|---|---|---|
| 8 | 2 | `CONTROLLING_SET_POINT` |
| 13 | 0 / 2 / 3 / 4 / 9 / 11 | `ONLINE` / `REMOTE_SETPOINT` / `REMOTE_MODE` / `MAX_SETPOINT` / `REMOTE_ENABLE` / `FAULT_FLAGS` |
| **26** | **0** | **`MODE`** |
| **26** | **1** | **`SETPOINT`** |
| 26 | 2 | `UNITS` (`C`/`F`) |
| 26 | 3 / 4 | `REAL_TIME_LW` (min, hour) / `REAL_TIME_HW` (date…) |
| 26 | 5 / 6 | `BLUETOOTH_CONNECTION_STATUS` / `WIFI_CONNECTION_STATUS` |
| 26 | 13 | `OADR_OVERRIDE_STATUS` |
| 26 | 17 / 18 / 19 | `OADR_ELECTRIC_POWER_USAGE_CUMULATIVE_2 / _1 / _0` |
| 26 | 31 / 32 | `WIFI_CONNECTION_DETAILS` / `WIFI_RSSI_BARS` |
| **26** | **35 / 36** | **`SET_WATER_SENSOR` / `WATER_SENSOR_STATUS`** (leak) |
| **26** | **37** | **`WATER_AVAILABLE`** ← the tank-fullness figure |
| 26 | 38 | `MODULE_FAULT_FLAGS` |
| 27 | 0 / 28–48 / 49 / 50 | `SOFTWARE_VERSION_HOST_MCU` / `SSID` / `PRODUCT_MAX_TEMPERATURE` / `PRODUCT_MIN_TEMPERATURE` |
| 27 | 51 / 113 / 114 / 115 / 116 / 147 | `SET_ADVANCED_LOAD_UP_ENABLE` / `SET_TOU_USER_PREFERENCE` / price-ratio lo / hi / `LOAD_UP_HOUR_BEFORE_SHED` / `UTILITY_ENROLLMENT` |

**Enums**

- `MODE_BASE10`, the value written to 26/0: `SMART=1, STANDARD=2, VACATION=4,
  EFFICIENCY=8, HYBRID=16, ELECTRIC=32`. **`EFFICIENCY` is the app's name for
  Heat Pump mode.**
- `MODE_BITS`, the bit position when reading a bitmask: `VACATION=2,
  EFFICIENCY=3, HYBRID=4, ELECTRIC=5`.
- `LEAK_BITS` for 26/36: `0 NO_WATER_DETECTED, 1 WATER_DETECTED, 2 NO_SENSOR,
  3 DISCONNECTED`.
- `ADVANCED_LOAD_UP_ENABLE = 0xA5`.

⚠️ **Temperature encoding is unresolved — two conventions exist.** A *next-gen
heat pump* module (blocks 1/2/11/27/28) uses `round(v/256*1.8+32)`, i.e.
1/256 °C, with `convertTemperature(F) = round((F-32)/1.8*256)`. The block-26
iCOMM path writes setpoint as `'00 ' + intToHex(value)` — a **single byte**,
which cannot be 1/256 °C. Read 26/1 and compare it against the setpoint on the
front panel **before writing anything**.

Useful helpers in the bundle: `getSpecificService` → 69400001;
`getCharacteristicsForService` → `{listener: 69400002, writer: 69400003}`;
`sendAndRecieveHex(device, hex, log, skipCrc)`;
`constructFrame({block, command, head, param, paramStart})`; `recurSendFrames`;
`querySingle(device, block, offset, log)`; `queryFrames` (multi-chunk writes,
head `BD`, cmd `40`, terminated by an `07 … 01` commit).

## Why it is blocked

A `btmon` HCI trace from the Pi shows a completely healthy link:

- ATT MTU negotiated to **151**. ⚠️ bleak's `client.mtu_size` reports **23** —
  that is a **BlueZ reporting artifact, not the real MTU**. Trust the trace.
- CCCD `0100` written on the notify characteristic, **Write Response** received.
- Every protocol frame sent as an ATT **Write Request**, every one answered with
  a **Write Response** — the module's ATT server is alive and accepting.
- Zero `Handle Value Notification` in either direction.
- Module terminates the link at **~15 s**, every time:
  `Disconnect Complete, Reason: Remote User Terminated Connection (0x13)`.

Two earlier findings shaped the search and are worth keeping:

- **The module sends nothing unprompted.** Subscribed to both notify pipes,
  zero notifications. It waits to be spoken to.
- **The ~14 s hangup is not a generic idle timeout.** Three successful GAP
  reads at 5 s intervals did not reset it (13.19→26.65 s and 2.41→16.49 s in
  separate tests). The module wants its own protocol within ~14 s or it hangs
  up.

### The ~15 s hangup is an authentication watchdog, and the PIN clears it

The trace above was taken while subscribing to `69400002` *before* writing the
PIN. That ordering is wrong, and it was making the module reject the PIN. The
app writes the six raw ASCII PIN bytes first, waits for the Write Response,
sleeps ~600 ms, and only then subscribes. Reproducing that order produces a
clean, repeatable A/B:

| PIN written | Link lifetime |
|---|---|
| correct (last six digits of the advertised name) | **45 s+, never dropped** |
| `000000` (wrong) | **14.07 s, module hangs up** |
| none at all | **14.48 s, module hangs up** |

So the module validates the PIN, the derivation is right, and an authenticated
link stays up for minutes — long enough for BLE to be a real daemon transport
rather than a periodic poll. `icomm_session.py` implements this ordering;
everything else in this directory still uses the old, failing one.

### But 6940 is inert even authenticated

Authenticated, with the CTA-2045 daemon stopped so nothing else was talking to
the tank, the write pipe still answers nothing:

- All of `A0..AF` and `F0..FF` (excluding `40` and `07`, the two commands that
  change tank state), in both the `BD cc 04` and `BD cc 05 01` shapes — 60
  well-formed frames, zero replies.
- Twelve encodings of `BD F2 05 01 D8` — raw, spaced hex text, packed hex text,
  lowercase, base64, with and without `\n`/`\r\n`, with and without the `BD`
  prefix — all silent, listening on both notify pipes.

The module accepts every write and produces nothing.

A second pass (`deep.py`, `at3.py`, `blindpair.py`, `crosspipe.py`) closed the
remaining gaps, each one a separate authenticated run at the hardware:

| Tried | Result |
|---|---|
| **CTA-2045 adapter physically unplugged** from the EcoPort | still silent — stronger than merely stopping the daemon |
| **Real negotiated MTU**, via `await client._backend._acquire_mtu()` | **151.** bleak 0.22 reports its 23-byte default until you ask, which is what made MTU look like a suspect |
| **12 s settle** after the PIN before the first frame | silent |
| **ASCII on `6940`** — `AT?`, `?`, `help`, each with and without CRLF | silent. The pipe parses ASCII (that is what the PIN is) but shares no grammar with `7f51` |
| **Write-without-response**, after authenticating | silent |
| **All 256 values of the frame start byte** (`BD` position), in both the `cc 04` and `cc 05 01` shapes | silent. Scoped to `F2` and `A0` in the command slot, never `40` or `07` |
| **Blind `F0` pair-key store** (18 bytes from `SHA256(DSN)`), then `F2` again | silent — no ACK to the store either |
| **Both notify pipes subscribed at once**, `F2` out each write pipe | only `7f51` ever speaks, in all four combinations |
| **`7f51` console swept harder** — 95 `AT<printable>` + 675 `XY?` probes | still exactly one token: `AT?` → `AT` |

That retires the "blind sweep of all 256 command bytes is the last resort" note
this file used to end on, at least for the start byte. The command byte remains
un-swept on purpose: `40` and `07` write and commit.

### 7f51 is not DFU — it is an ASCII console

The assumption that `7F51xxxx` was nRF DFU is wrong. It is a live,
line-oriented ASCII interface that answers in ~60 ms. Send it anything and it
replies `ERROR:invalidCMD\n`. That reply is an oracle: anything else means the
token was recognised.

Sweeping it found **exactly one** recognised token — the case-sensitive literal
prefix `AT?`, which always answers `AT`. Trailing junk is ignored (`AT?HELP`,
`AT?0` and `AT??` all answer `AT`); `AT`, `at?` and `AT ?` are all invalid. 676
two-letter names and ~130 candidate words produced nothing else. The string
`invalidCMD` appears nowhere in the app bundle or the APK, so this console is
firmware-only and undocumented by the app.

`AT?` looks like a host-side device-detection probe, which on a service that
pairs a writer with a notifier most likely fronts a firmware-update path. It
has not been fuzzed further, deliberately.

This also proves the receive path is sound: 7f51 notifications arrive through
the same BlueZ stack that hears nothing on 6940, so 6940 is not failing to be
*delivered*, it is failing to be *produced*.

### Bonding is not the missing piece — the firmware has no SMP

Worth stating precisely, because "a peripheral that requires encryption before
it will notify" behaves *exactly* like this one and it is the obvious next
theory. It is wrong.

The earlier `AuthenticationFailed` in 70 ms was ambiguous: BlueZ returns that
when no pairing agent is registered, so the stack may simply have had nothing to
answer a passkey request with. `passkey.py` eliminates that reading — it stands
up a real `org.bluez.Agent1` over `dbus_fast` with `DisplayYesNo` capability,
offering the six digits from the advertised name as an SMP passkey (they are the
right shape for one), and registers every callback: `RequestPasskey`,
`DisplayPasskey`, `RequestConfirmation`, `RequestAuthorization`, `AuthorizeService`.

**Not one callback fires.** And `btmon` gives the reason directly:

```
SMP: Pairing Failed (0x05) len 1
    Reason: Pairing not supported (0x05)
```

The module rejects the pairing request before any association model is
negotiated. There is no bonded state to reach, the six digits are an
application credential only, and encryption is not what `6940` is waiting for.

### Ruled out

| Hypothesis | Result |
|---|---|
| Wrong CRC | Table + poly 0x5E verified by hand: `BD F2 05 01` → `D8` |
| Frames need / don't need the CRC byte | Tried both; silent either way |
| Wrong pairing code | Six variants — derived 6-digit, full serial, full name, `000000`, 9-digit tail, NUL-terminated. Silent |
| Link must be bonded / encrypted | Settled at the HCI layer: `SMP: Pairing Failed (0x05) Reason: Pairing not supported (0x05)`. **The firmware has no SMP at all** — see below |
| Advertised name ≠ connected name | GAP 2A00 read returns the identical name |
| MTU too small | Actually 151 |
| Wrong pipe | Pin and frame crossed over both write pipes, listening on both notify pipes — silent in all 4 combinations |
| Write-without-response | Tried, before and after authenticating; silent |
| Wrong block (26 / 27 / 13) | Silent |
| Subscribe-before-PIN ordering | **Was real, and was breaking the PIN** — see above. Fixed, but did not produce a notification |
| Wrong PIN | Disproved by A/B: the correct PIN holds the link, a wrong one does not |
| Module not in a protocol-capable state | Disproved: it validates the PIN and holds an authenticated link |
| Notify vs. indicate | `btmon` shows **zero `Handle Value` events of any kind**, and it sits below BlueZ's GATT layer, so an indication would have been captured even though BlueZ discards it. (BlueZ refuses a hand-written CCCD: `org.bluez.Error.NotPermitted`) |
| Bus contention with the CTA-2045 adapter | Disproved twice: stopped `cta2045-mqtt`, then **physically unplugged the adapter from the EcoPort**; still silent |
| Wrong frame encoding | Twelve variants, all silent |
| Device-type branching (heat pump vs. legacy) | Disproved in the bundle: one provider, one session module, one copy of each command literal and each UUID. `F2` is always the first frame regardless of device type or stored-key state; type only changes UI copy and the *post-session* register map |
| Wrong register map | The next-gen map the bundle specifies for heat pumps — block 11 section read `BD A0 07 0B 00 10 F4`, plus blocks 1 and 2 — is as silent as the legacy block 26/13 one |

Earlier, before the protocol was recovered: **20 generic probes, zero replies**
— both pipes × {`\r\n`, `AT\r\n`, `?\r\n`, `\x00`, `\x01`, `\xaa\x55`,
`\x02\x00\x03`, `{"cmd":"info"}`, `hello\r\n`, `AT+GMR\r\n`}. Read that result
carefully: those probes ran *unauthenticated*, and `AT\r\n` is not `AT?`, so it
missed the one token 7f51 actually answers. The conclusion drawn at the time —
"this is not a line-oriented text protocol" — was wrong for 7f51.

Everything the app could possibly do differently has now been eliminated from
the source side — there is no pre-session write, no unlock, no type branch, no
alternate map, and the app's own contract requires the firmware to answer `F2`
even with zero stored keys. The module validates the PIN and then dispatches
nothing.

Earlier revisions of this file called that "some state we are not in" and left
it there, on the grounds that no amount of bundle reading could settle it. It is
settled now, and the answer is not a state. See the next section.

## BLE is the wrong door

This file used to end on two leads: sniff a real app session, and establish
whether the official app can talk to this module at all. **Both are now closed,
and the second one closes the whole line of attack.**

**The official iOS app offers no Bluetooth setup option for this tank.** Not a
greyed-out one, not one behind a menu — the BLE path is never presented. And the
bundle says why: the setup-method screen is driven by the device's
`hardware.connectors` metadata, which arrives **from the cloud** as part of the
product record. It is not gated on platform, not on model string, not on
firmware version. For this product family that metadata does not list BLE, so
the app never offers it.

Which retires the sniffing lead too. There is no app session to capture on iOS
because the app will not start one, and an Android HCI snoop is not available
here. There is no delta left to diff.

Read together with everything above, the picture is coherent for the first time.
The `F0`/`F1`/`F2`/`F3`/`F4` handshake — store pair key, list keys, delete key,
request random, HMAC-SHA1 challenge, 18-byte key derived from `SHA256(DSN)` — is
shaped like **commissioning**, not telemetry. `AC000W…` in the advertised name
is an **Ayla Networks DSN**. The most economical explanation of a module that
validates a PIN, holds an authenticated link for minutes, and then dispatches
nothing is that **there is no register dispatcher behind `6940` on this
firmware, because registers were never meant to travel over it.**

### The path the vendor actually uses: SoftAP

The bundle does contain a device-local provisioning flow, and it is HTTP over
the module's own access point, not BLE:

| Step | Request |
|---|---|
| 1 | `POST http://192.168.0.1/wifi_scan.json` — trigger a scan |
| 2 | `GET /wifi_scan_results.json` — retrieve the AP list |
| 3 | `POST /wifi_connect.json?bssid=<bssid>&key=<psk>` — join |
| 4 | `GET /wifi_status.json` — poll until connected |

**2.4 GHz only**; the app filters the scan results and rejects 5 GHz. Entering
the mode is a front-panel gesture, and **flashing green means the module is
already advertising its own AP** — the same LED state that arms a factory reset
on the function-button variant, so read the warning at the top of this file
first.

This is Ayla's standard SoftAP flow. It provisions Wi-Fi. It does not expose
registers either.

### Ayla LAN mode: real protocol, wrong owner

> **Update (phase 28): this section's conclusion is overturned.** LAN mode was
> armed without any OEM key: the relay answers the module's own
> `GET /devices/<key>/lan.json` with a locally generated key, and the module
> then accepts local registration and encrypted LAN writes. See
> [ARCHITECTURE.md](../ARCHITECTURE.md) item 12. The *BLE* dead end stands;
> the "no amount of local poking opens it" line below does not.

Once provisioned the module sits on the LAN and answers HTTP. `portscan2.py`
found **exactly one open TCP port, 80**, and it serves:

```
GET /regtoken.json
{"regtoken":"<rotating>","registered":0,
 "registration_type":"Same-LAN","host_symname":"NextGenHP"}
```

Three things fall out of that one response:

- **`host_symname: NextGenHP` settles the device-type question** the bundle
  could not. This *is* a next-gen heat pump module, so the next-gen register map
  (blocks 1/2/11) and the `round(v/256*1.8+32)` temperature convention are the
  correct ones for it. That removes "we were reading the wrong map" as an
  explanation for the silence on `6940` and hardens the conclusion above.
- **`registration_type: Same-LAN`** is Ayla's flow where the client POSTs its own
  address to `/local_reg.json`, the *device* connects back, and a key exchange
  follows. `lanmode.py` stands up a logging HTTP server and knocks. Nothing ever
  calls back: `local_reg.json` **404s on both POST and PUT across five path
  spellings** (`local_reg.json`, `client/local_reg.json`, `local_lan.json`,
  `lan.json`, `localreg.json`).
- **`registered: 0`** with a token that rotates on each read is the tell. LAN
  mode needs a cloud-pushed `lanip` config and a LAN key, and both belong to the
  account that owns the DSN — **A.O. Smith's Ayla OEM account, not ours.** Two
  layers of registration, and the outer one is not ours to satisfy. No amount of
  local poking opens it.

There is also no Ayla LAN client in the app bundle or any of the six DEX files,
so the app itself never uses LAN mode; it goes to the cloud.

⚠️ **Scanning methodology, learned the hard way.** `portscan.py`'s 400-way
concurrent sweep reported *every* port closed — including port 80, which answers
fine on its own. The module's embedded TCP stack simply stops accepting under
load. Both scripts are kept: `portscan2.py` walks two ports at a time with a
pause and re-checks `/regtoken.json` between batches to prove the target is
still alive, and that is the one to use.

## Where this leaves it

BLE on this tank is a dead end, and it is a dead end for a reason that no
further experiment at the radio will change. The remaining ways forward, none of
them BLE:

1. **The cloud API already delivers three of the four goals** — mode, setpoint
   write, tank fullness — through Home Assistant's `aosmith` integration
   (`py-aosmith`). If that is enough, stop here.
2. **`current_temperature` is `None`** in that integration: the cloud never
   reports a tank thermistor. No local transport on this hardware exposes it
   either. Rheem's econet tanks do, over RS485 on a diagnostic port
   (`esphome-econet`), fully locally — which is a hardware-replacement answer,
   not a protocol one.
3. **The leak sensor is unreachable everywhere**, and not just on A.O. Smith:
   Rheem's LeakGuard is equally closed. Registers 26/35 and 26/36 exist in the
   map recovered here, but nothing serves them.
4. **CTA-2045 load-up commands are being ignored**, and the cloud shows
   `isCtaUcmPresent: true` alongside `isDemandResponsePaused: true` and
   `isEnrolled: false`. That is the one genuinely open thread with a plausible
   payoff, and it lives in the CTA-2045 side of this repo, not here.
5. If someone with an **Android** phone and this tank ever wants to try, an HCI
   snoop is still theoretically the decisive capture — but only if the app
   offers that device a BLE path, which for this product family it appears not
   to.

## Gotcha that produced three false conclusions

BlueZ drops the device from its cache shortly after a disconnect, so **every
reconnect needs a fresh discovery**, and a scan started too soon comes back
empty. That empty scan was misread three times as "only advertises in pairing
mode" and "has a post-disconnect cooldown". Both wrong — a watcher found it
advertising again **1 second** after a disconnect.

Also wrap scans as `bluetoothctl --timeout N scan on`, never
`timeout N bluetoothctl scan on` — the latter kills bluetoothctl before
discovery starts and silently returns nothing.

## The scripts

| Script | Purpose |
|---|---|
| `icomm_session.py` | **The current client.** App-faithful ordering (PIN → 600 ms → subscribe → `F2`), full handshake through `F1`. `--subscribe-first` runs the old ordering as a control. Read-only. |
| `icomm_ble.py` | Earlier client: PIN, then reads blocks 26 and 13. Still uses the old subscribe-first ordering. Read-only. |
| `icomm_gatt.py` | Dump services, characteristics, properties |
| `icomm_pin.py` | PIN-derivation variants |
| `icomm_pipes.py` | Cross both write pipes against both notify pipes |
| `icomm_nocrc.py` | Frames with and without the trailing CRC |
| `icomm_diag.py` | Connection/notification diagnostics |
| `bleprobe.py`, `bleprobe_write.py` | Generic probe sweeps (all silent) |
| `blelisten.py` | Subscribe and wait for anything unprompted (nothing) |
| `blekeepalive.py` | Characterise the ~14 s hangup |
| `blescan.py` | Scan and match the iCOMM by name |
| `advert.py` | Dump the advertisement and watch 45 s for change (static) |

Probing `6940`:

| Script | Purpose |
|---|---|
| `deep.py` | The five untried axes in one session: real MTU, ASCII on `6940`, write-without-response, long settle, start-byte sweep |
| `crosspipe.py` | Both notify pipes subscribed at once, every frame down each write pipe |
| `cmdsweep.py` | `A0..AF` / `F0..FF` in both frame shapes |
| `encoding.py` | Twelve encodings of the same `F2` frame |
| `blindpair.py` | Store an `F0` pair key blind, then re-ask `F2` |
| `nextgen.py` | The next-gen register map (blocks 1/2/11) |
| `cccd.py` | Indicate rather than notify |
| `sweep.py` | Where an authenticated answer might come back |
| `watchdog.py`, `wrongpin.py` | The PIN A/B on link lifetime |

Probing `7f51` and SMP:

| Script | Purpose |
|---|---|
| `console.py`, `oracle.py`, `oracle2.py`, `oracle3.py` | The `ERROR:invalidCMD` oracle, then the two-letter name space |
| `at3.py` | The three-character `AT?`-shaped command space |
| `bond.py` | Bond first, then run the ordinary session |
| `passkey.py` | Register a real `org.bluez.Agent1` and offer the six digits as an SMP passkey |

Over the network, once the module is provisioned:

| Script | Purpose |
|---|---|
| `lanmode.py` | Stand up a logging HTTP server, then knock on `/local_reg.json` for a callback |
| `portscan2.py` | Gentle TCP scan (**use this one**) plus the LAN-mode endpoint list |
| `portscan.py` | The 400-way scan that produced a false all-closed result. Kept as a caution |

Needs `bleak`; `passkey.py` also needs `dbus_fast`. Run from the Pi — its radio
is closer to the tank than a laptop, and `btmon` is available there for tracing.
Set `ICOMM_ADDR` to pin a specific module; leave it unset and they scan for the
first `iCOMM-*` advertiser. The three network scripts want `ICOMM_IP`.

**These only read.** Nothing here writes tank configuration; that is deliberate
while the protocol is unverified against a live response. The one exception is
`blindpair.py`, which stores a pair key — a slot the app itself manages and
deletes from, not tank configuration.

## If the module ever answers

Read block 26 offsets 0, 1, 36 and 37 first and check them against ground truth
you already have — the setpoint and mode on the front panel, and the cloud's own
hot-water figure. That single read settles the temperature encoding, confirms
the mode enum, and tells you whether `WATER_AVAILABLE` is the real tank-fullness
number or just the same 0/50/100 quantisation the cloud reports.

The prize is now narrower than when this work started, because the cloud API
covers mode, setpoint and tank fullness already. What `6940` would still add
that nothing else can: **26/36 `WATER_SENSOR_STATUS`, the leak sensor** — and a
real tank temperature, if one of these blocks turns out to carry it.
