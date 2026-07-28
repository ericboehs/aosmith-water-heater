# iCOMM Bluetooth LE — recovered protocol (blocked)

A parallel line of attack on the same tank. CTA-2045 gives good demand-response
control but cannot read the leak sensor, cannot report tank fullness directly,
and cannot set the mode or setpoint. The iCOMM module's BLE interface can do
all four — the app does exactly that.

**Status: the protocol is fully recovered, and the module answers nothing.**
Every frame is accepted at the ATT layer and no notification ever comes back.
These scripts are the record of getting that far, kept so the next attempt does
not start from zero.

What is deliberately **not** here: the APK and its 31 MB decompile. Those are
A.O. Smith's. Register names, offsets and framing are interoperability facts
and are published in full below.

## Getting the module to advertise

Hold **WiFi** until the light goes out, then hold **Bluetooth** until it
flashes green.

⚠️ This **turns the tank's WiFi off**, which takes the cloud integration
offline until you turn it back on. It may also matter more than that — see
"Why it is blocked".

## The GATT map

| Service | Write | Notify |
|---|---|---|
| `69400001-B5A3-F393-E0A9-E50E24DCCA99` | `69400003` (write, write-no-resp) | `69400002` (notify + CCCD) |
| `7F510004-B5A3-F393-E0A9-E50E24DCCA9E` | `7F510006` (write, write-no-resp) | `7F510005` (notify + CCCD) |

Plus Generic Access (1800) and Generic Attribute (1801). No advertised service
UUIDs, no encryption required, connects without bonding.

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

Only the `6940xxxx` service is referenced by the app. `7F51xxxx` never appears
— likely nRF DFU.

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

⚠️ **The app transmits the session frames with no CRC at all** — a bug.
`sendAndRecieveHex(service, hex, skipCrc)` appends the CRC only when `skipCrc`
is falsy, but every session caller routes through
`sendReceive(service, hex, expectedAck, log)`, which forwards its 4th argument
— the *logger*, always truthy — straight into the `skipCrc` slot. So
`BD F2 05 01` goes out as 4 bytes when its own length byte declares 5. The
firmware evidently tolerates it. Block reads and writes (`querySingle`,
`queryFrames`) do **not** skip the CRC.

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
  up. Whether valid frames stop the hangup is still unknown, and it decides
  whether BLE could ever be a daemon transport or only a periodic poll.

Ruled out:

| Hypothesis | Result |
|---|---|
| Wrong CRC | Table + poly 0x5E verified by hand: `BD F2 05 01` → `D8` |
| Frames need / don't need the CRC byte | Tried both; silent either way |
| Wrong pairing code | Six variants — derived 6-digit, full serial, full name, `000000`, 9-digit tail, NUL-terminated. Silent |
| Link must be bonded / encrypted | `pair()` and `bluetoothctl pair` both fail instantly with `AuthenticationFailed` and **no passkey prompt** — the module refuses SMP outright |
| Advertised name ≠ connected name | GAP 2A00 read returns the identical name |
| MTU too small | Actually 151 |
| Wrong pipe | Pin and frame crossed over both write pipes, listening on both notify pipes — silent in all 4 combinations |
| Write-without-response | Tried; silent |
| Wrong block (26 / 27 / 13) | Silent |

Earlier, before the protocol was recovered: **20 generic probes, zero replies**
— both pipes × {`\r\n`, `AT\r\n`, `?\r\n`, `\x00`, `\x01`, `\xaa\x55`,
`\x02\x00\x03`, `{"cmd":"info"}`, `hello\r\n`, `AT+GMR\r\n`}. This is not an
AT-command bridge or a line-oriented text protocol, and nothing generic will
ever get a reply from a device that only answers CRC-checked frames.

Two leads remain, in order:

1. **Re-enable the tank's Wi-Fi first.** The button dance that enters pairing
   mode disables the radio. If the protocol stack only runs with Wi-Fi up, the
   advertising state being probed is a bare provisioning shell.
2. **Sniff a real app session.** Android: developer options → *Bluetooth HCI
   snoop log*, then `adb pull` the btsnoop. iOS: Apple's Bluetooth logging
   profile plus PacketLogger. Either settles it in one shot.

A blind sweep of all 256 command bytes is the last resort — it risks hitting a
factory-reset or firmware command.

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
| `icomm_ble.py` | The real client: PIN, then reads blocks 26 and 13. Read-only. |
| `icomm_gatt.py` | Dump services, characteristics, properties |
| `icomm_pin.py` | PIN-derivation variants |
| `icomm_pipes.py` | Cross both write pipes against both notify pipes |
| `icomm_nocrc.py` | Frames with and without the trailing CRC |
| `icomm_diag.py` | Connection/notification diagnostics |
| `bleprobe.py`, `bleprobe_write.py` | Generic probe sweeps (all silent) |
| `blelisten.py` | Subscribe and wait for anything unprompted (nothing) |
| `blekeepalive.py` | Characterise the ~14 s hangup |
| `blescan.py` | Scan and match the iCOMM by name |

Needs `bleak`. Run from the Pi — its radio is closer to the tank than a laptop,
and `btmon` is available there for tracing. Set `ICOMM_ADDR` to pin a specific
module; leave it unset and they scan for the first `iCOMM-*` advertiser.

**These only read.** Nothing here writes tank configuration; that is deliberate
while the protocol is unverified against a live response.

## If the module ever answers

Read block 26 offsets 0, 1, 36 and 37 first and check them against ground truth
you already have — the setpoint and mode on the front panel, and the cloud's own
hot-water figure. That single read settles the temperature encoding, confirms
the mode enum, and tells you whether `WATER_AVAILABLE` is the real tank-fullness
number or just the same 0/50/100 quantisation the cloud reports.

Then the four things CTA-2045 cannot do — mode, setpoint write, leak sensor,
tank fullness — all become reachable, which is the entire reason this line of
attack exists.
