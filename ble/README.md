# iCOMM Bluetooth LE — recovered protocol (blocked)

A parallel line of attack on the same tank. CTA-2045 gives good demand-response
control but cannot read the leak sensor, cannot report tank fullness directly,
and cannot set the mode or setpoint. The iCOMM module's BLE interface can do
all four — the app does exactly that.

**Status: the protocol is fully recovered, and the module answers nothing.**
Every frame is accepted at the ATT layer and no notification ever comes back.
These scripts are the record of getting that far, kept so the next attempt does
not start from zero.

The full write-up — pinout, GATT map, register tables, and every ruled-out
hypothesis — lives in the author's notes; this README covers what is needed to
run the code.

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

- `len` is the total frame length including the CRC.
- Read value is `resp[6] << 8 | resp[5]` — little endian on the wire.
- **CRC-8, poly `0x5E`, init 0, non-reflected, no final XOR.** The table was
  lifted verbatim from the bundle and the polynomial confirmed by brute-forcing
  all 256.
- Frames are built as uppercase hex strings in the app, then sent as raw bytes.
  Do not send ASCII hex.
- **Use write-with-response.** Earlier probing used write-without-response,
  which the module accepts and ignores.

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

PIN derivation: `name.length > 21 ? name.slice(15, len-4) : name.slice(len-6)`.

⚠️ **The app transmits the session frames with no CRC at all** — a bug.
`sendAndRecieveHex(service, hex, skipCrc)` appends the CRC only when `skipCrc`
is falsy, but every session caller passes its logger into that slot. So
`BD F2 05 01` goes out as 4 bytes when its own length byte declares 5. The
firmware evidently tolerates it. Block reads and writes are unaffected.

### Registers worth having

Block 26 is the module's own map:

| Offset | Name |
|---|---|
| 0 | `MODE` — `SMART=1, STANDARD=2, VACATION=4, EFFICIENCY=8, HYBRID=16, ELECTRIC=32` |
| 1 | `SETPOINT` |
| 36 | `WATER_SENSOR_STATUS` — `0 none, 1 water, 2 no sensor, 3 disconnected` |
| 37 | `WATER_AVAILABLE` — the tank-fullness figure |

`EFFICIENCY` is the app's name for Heat Pump mode.

## Why it is blocked

A `btmon` HCI trace from the Pi shows a completely healthy link: ATT MTU
negotiated to **151**, CCCD `0100` written and acknowledged, every frame sent
as an ATT **Write Request** and answered with a **Write Response** — and zero
`Handle Value Notification` in either direction. The module then terminates at
~15 s with `Remote User Terminated Connection (0x13)`.

Ruled out: wrong CRC, CRC present vs absent, wrong PIN (six variants), bonding
(the module refuses SMP outright — `AuthenticationFailed` with no passkey
prompt), advertised-vs-connected name mismatch, MTU, wrong pipe (all four
combinations), write-without-response, and wrong block.

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
and `btmon` is available there for tracing.

**These only read.** Nothing here writes tank configuration; that is deliberate
while the protocol is unverified against a live response.
