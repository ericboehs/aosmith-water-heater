# CTA-2045 factual audit

Audit date: 2026-07-27  
Repository revision audited: `67c547225e1ab603b32af5a79526300c39bb0e8b`  
Device: A.O. Smith HPS10-50H45DV, device revision 2.14  
Scope: RS-485 / CTA-2045 only. The `ble/` directory and BLE-only assertions in
the README were excluded as requested.

## Verdict definitions

- **CONFIRMED** — supported by a live transaction, direct inspection of the
  deployed system/source, or a primary published source.
- **WRONG** — contradicted by live bytes or a primary published source.
- **UNVERIFIED** — plausible or historically observed, but not independently
  reproducible from the remotely accessible hardware or available primary
  sources during this audit.

## Sources and method

The normative source is the February 2021
[ANSI/CTA-2045-B PDF published by CTA][cta-b]. Its SHA-256 in this audit was
`8f2cfb7fd9281e3ed288d0f159aeaa5643cf3dad1871249d06a27fbf060a127f`.
The implementation comparison used EPRI's
[CTA-2045-UCM-CPP-Library][epri] at commit
`dbaffc88624efdfe15260e1efb5ebf2e4afdb24c` (2017-03-14). That library is useful
corroboration for CTA-2045-A-era framing, checksum, temperature, setpoint and
commodity structures, but it predates revision-B additions and must not
override CTA-2045-B.

The product sources were A.O. Smith's [exact model page][model] and
[use-and-care guide][manual]. EPRI's published
[CTA-2045 water-heater test procedures][epri-wh-test] supplied additional
implementation guidance. Raspberry Pi UART configuration was checked against
the [official UART documentation][pi-uart].

Live requests went through `/dev/serial0` at 19200 8N1. The daemon was stopped
before opening the tty. No event duration byte `00` was sent. All byte strings
below include the complete PDU and checksum unless identified as a two-byte
link ACK/NAK.

The device-info exchange was:

```
TX 08 02 00 02 01 01 04 43
RX 06 00
RX 08 02 00 30 01 81 00 42 00 01 18 00 03 02 0e 00 00 01 80 00
   48 50 53 31 30 2d 35 30 48 34 35 44 56 00 00 00
   SS SS SS SS SS SS SS SS SS 00 00 00 00 00 00 00 cc cc
```

(`SS` is the nine ASCII digits of this tank's serial, redacted here along with
the checksum that covers them.)

It decodes as CTA revision B (`42 00`), vendor `0118`, heat-pump water heater
type `0003`, device revision 2.14 (`02 0e`), capability bitmap `00000180`,
model `HPS10-50H45DV`, and the tank's nine-digit serial. The live Pi reported
Debian 13, Python 3.13.5, `/dev/serial0 -> ttyAMA0`, and the documented
`enable_uart=1`, `dtoverlay=miniuart-bt`, `core_freq=250` configuration.

## Priority questions

### 1. Setpoint writes

**CONFIRMED:** a correctly framed write is implemented at the protocol level
but rejected by this tank as "Command Not Implemented"; the earlier result was
not caused by using the wrong opcode.

- Read request: `08 02 00 02 03 03 f9 49`
- Read reply: `08 02 00 08 03 83 00 00 03 00 00 96 11 12`
  — success, heat-pump water-heater type `0003`, °F, setpoint 1 = 150.
- A short write containing only setpoint 1:
  `08 02 00 07 03 03 00 03 00 00 96 e0 c3`
  returned `08 02 00 03 03 83 03 b0 0e` — response 3, length error.
- The complete write, with setpoint 2 set to the specified "do not change"
  value `8000`:
  `08 02 00 09 03 03 00 03 00 00 96 80 00 92 8f`
  returned `08 02 00 03 03 83 01 b4 0c` — response 1, Command Not
  Implemented.
- A subsequent read still returned 150.

CTA-2045-B §11.1.6 independently confirms opcode `03/03`, the exact request
layout, signed 16-bit setpoints, and `8000` as "do not change."

### 2. Present water temperature

**CONFIRMED:** the request uses the specified opcode and the tank genuinely
refuses it.

- Request: `08 02 00 02 03 04 f7 4a`
- Link response: `15 07`
- Application response:
  `08 02 00 03 03 84 06 a7 13` — response 6, Other Error.

CTA-2045-B §11.1.7 and EPRI's
[`CEA2045MessageGetPresentTemperatureResponse.h`][epri-temp] both define
`03/04` as Get Present Temperature. For a water heater the specified response
would be average tank temperature in hundredths of a degree.

The tank's simultaneous use of link NAK reason 7 is itself nonconforming:
CTA-2045-B Table 8-2 says reason 7 is only for link-layer requests, not
unsupported application commands.

### 3. Operating mode

The original compound claim needs to be split:

- **WRONG:** "there is no CTA-2045 command at all." CTA-2045-B §11.7 defines
  Get/Set SGD Efficiency Level (`01/02`), explicitly says that it is related to
  operating mode, defines levels 1–9 plus optional Off and Vacation, and leaves
  the mapping to manufacturer modes to the manufacturer.
- **CONFIRMED:** this tank cannot be switched among Hybrid, Electric, Heat
  Pump and Vacation with that standard command. Get succeeded at level 4:
  request `08 02 00 02 01 02 02 44`, reply
  `08 02 00 04 01 82 00 04 78 47`. Correctly framed Set requests for every
  level `00` through `0a` all returned
  `08 02 00 03 01 82 01 bf 04` — Command Not Implemented. The level remained
  4.
- **UNVERIFIED:** no published A.O. Smith mapping says which front-panel mode
  level 4 represents.
- **UNVERIFIED:** a manufacturer-proprietary mode-setting opcode cannot be
  disproved exhaustively. Basic opcodes and commodity selectors were swept
  completely, and the relevant standard Intermediate categories were swept,
  but a safe exhaustive search of all 65,536 Intermediate opcode pairs and all
  vendor-proprietary message types was not completed.

The device-info capability bitmap is `00000180`: bits 7 and 8 advertise Price
Stream and Get SGD Efficiency Level, matching the successful Get.

### 4. Reported power

- **CONFIRMED:** it is estimated, not measured. The returned commodity byte is
  `00`; CTA-2045-B §11.3.1 assigns the low seven bits to Electricity Consumed
  and the high bit to Measured (`1`) versus Estimated (`0`). The tank clears
  that bit. The EPRI water-heater test procedure also explicitly permits
  estimates without metering electronics.
- **CONFIRMED:** all 130 daemon samples from 21:46:11 through 23:14:53 in the
  inspected journal reported 356 W, and the live commodity frame also contained
  `00 000000000164 ...` (hex `0164` = 356).
- **UNVERIFIED:** the tank's live 4500 W report was not reproduced during this
  audit because no remotely accessible standard command can select Electric
  mode and the current heating runs stayed on the compressor.
- **CONFIRMED by publication:** the exact model has two 4500 W backup
  elements, per A.O. Smith's model page. Thus 4500 W is a valid nameplate
  value, but that does not independently prove what commodity value the tank
  emits when an element is on.
- **WRONG as written:** "byte-identical across hundreds of polls" overstates
  the evidence available in this audit. The independently counted evidence is
  130 samples for 356 W.

Representative commodity response:

```
08 02 00 44 06 80 00
00 000000000164 00000043ac88
06 000000000000 00000000109a
07 000000000000 000000000671
0a 000000000000 00000000109a
0b 000000000000 000000000671
29 13
```

Here `109a` is 4250 Wh and `0671` is 1649 Wh.

### 5. Mode-dependent storage capacity

- **CONFIRMED:** the current live total energy-take capacity is 4250 Wh.
- **UNVERIFIED:** 7275 Wh in Electric mode and the resulting assertion that
  capacity is mode-dependent could not be independently repeated without a
  person changing the front-panel mode. They remain clearly labeled local
  observations in the proposed README.
- **CONFIRMED:** using total and present take capacity from the same response
  is the robust computation.

### 6. Communications timeout and event loss

- **CONFIRMED approximately, but the coded constant is WRONG:** Running
  Curtailed was received 810.154 seconds after the last successful comm-status
  transaction; the first SGD Error frame was received at 811.704 seconds.
  Therefore this trial brackets the transition to 13:30.154–13:31.704. (The
  test script printed 812.105 seconds only after its 400 ms post-response
  drain.) The repository constant `SGD_ERROR_TIMEOUT = 851.0` is roughly
  39.3–40.8 seconds too high for this trial.
- **WRONG:** "that wipes any active event." After SGD Error appeared, a single
  comm-status heartbeat was application-ACKed. Two seconds later the state was
  Running Curtailed again, proving the 1800-second Shed had survived. SGD Error
  masks the event state; it does not erase the event on this firmware.
- **CONFIRMED:** the five-minute heartbeat cadence is appropriate.
  CTA-2045-B requires Outside Communication Connection Status every one to five
  minutes.

Key evidence:

```
heartbeat TX 08 01 00 02 0e 01 e2 58
shed TX      08 01 00 02 01 1e cf 5b
state RX     08 01 00 02 13 02 d1 63  # 810.154 s after comm status
state RX     08 01 00 02 13 05 cb 66  # 811.704 s after comm status
heartbeat TX 08 01 00 02 0e 01 e2 58
state RX     08 01 00 02 13 02 d1 63  # Running Curtailed survived
normal TX    08 01 00 02 02 00 09 3f
state RX     08 01 00 02 13 01 d3 62  # Running Normal cleanup
```

The test ran under a transient systemd unit with a 20-minute hard limit and an
unconditional daemon restart. It closed the tty, explicitly sent End/Normal,
and the MQTT daemon restarted successfully.

## Protocol and decoder audit

CTA-2045-B's complete top-level assignment table is:

| Message-type bytes | Assignment |
|---|---|
| `00xx`–`05xx` | Vendor proprietary |
| `06xx` | Reserved to avoid link ACK ambiguity |
| `07xx` | Future |
| `0801` | Basic DR |
| `0802` | Intermediate DR |
| `0803` | Data-Link |
| `0804` | Commissioning and Network Support |
| `0805`–`08ff` | Future |
| `0901`–`090c` | Named pass-through protocols |
| `090d`–`14ff` | Future |
| `15xx` | Reserved to avoid link NAK ambiguity |
| `16xx`–`efff` | Future |
| `f0xx`–`ffff` | Vendor proprietary |

The complete revision-B operational-state list is 0 Idle Normal, 1 Running
Normal, 2 Running Curtailed, 3 Running Heightened, 4 Idle Curtailed, 5 SGD
Error, 6 Idle Heightened, 7 Cycling On, 8 Cycling Off, 9 Variable Following,
10 Variable Not Following, 11 Idle Opted Out, 12 Running Opted Out, 13 Running
Price Stream and 14 Idle Price Stream.

Intermediate response codes are 0 Success, 1 Command Not Implemented, 2 Bad
Value, 3 Command Length Error, 4 Response Length Error, 5 Busy, 6 Other Error,
7 Override and 8 Not Enabled.

| Claim | Verdict | Evidence |
|---|---|---|
| Basic, Intermediate and Data-Link types are `0801`, `0802`, `0803` | **CONFIRMED** | CTA-2045-B Table 6-2, EPRI `ConvertEnums.h`, and zero-payload live queries all agree. |
| Those are the complete CTA message-type table | **WRONG** if implied | Table 6-2 also assigns `0804` to commissioning/network support, `09xx` pass-through protocols, vendor ranges and reserved ranges. The tank itself ACKed only `0801`–`0803` and returned `15 06` for tested unsupported types. |
| Length is a generic big-endian 16-bit value | **WRONG** | The field is three reserved zero bits plus a 13-bit length. `build()` happens to emit valid small lengths; `_take_frame()` fails to enforce the reserved bits. |
| Fletcher/mod-255 checksum seeded `AA,00` | **CONFIRMED** | CTA-2045-B Appendix C, EPRI [`Checksum.cpp`][epri-checksum], live accepted frames and live bad-checksum NAK `15 03`. |
| Link ACK/NAK are bare two-byte packets | **CONFIRMED** | CTA-2045-B Table 8-1 and live `06 00` / `15 xx`. |
| The tank does not require response ACKs | **CONFIRMED only as a device observation** | The daemon runs successfully with them disabled; however CTA-2045-B requires receivers to ACK/NAK valid PDUs. Leaving them off is intentionally nonconforming, not a general protocol rule. |
| Responses may be retransmitted and a settle/drain prevents request skew | **CONFIRMED for implementation; UNVERIFIED as a fresh failure reproduction** | Code does the drain and deployed operation remains aligned. The historical failure was not deliberately recreated. |
| The bus is two-wire, half duplex | **CONFIRMED** | CTA-2045-B §§6.1.5 and 18.2; live request/response behavior agrees. |
| 19200 8N1 | **CONFIRMED** | CTA-2045-B §18.2, live tty settings and deployed code agree. |
| Commodity record is code + 6-byte rate + 6-byte cumulative amount | **CONFIRMED** | CTA-2045-B §11.3.1, EPRI [`CEA2045MessageCommodityResponse.h`][epri-commodity], and live 13-byte records agree. |
| The byte before records is an unknown "lead byte" | **WRONG** | It is the Intermediate response code. |
| Commodity `00` is electricity consumed; `06` is total storage/take capacity | **CONFIRMED** | CTA-2045-B Table 11-7 and live values. |
| Commodity `07` is a storage deficit | **CONFIRMED as a derived water-heater interpretation; WRONG as the standard name** | The normative name is Present Energy Storage/Take Capacity: energy the device can take now. For this tank it is equivalent to the energy-to-full used by the percentage formula. |
| Commodity `0a/0b` merely mirror `06/07` | **WRONG** | Revision B assigns them Advanced Load Up Total and Present Energy Storage/Take Capacity. This tank currently returns byte-identical values because Advanced Load Up is not enabled (category `0c` returned response 8). |
| Duration is `2 × value²` seconds | **CONFIRMED only for `01`–`fe`** | CTA-2045-B §10.1.2. |
| Duration `00` means indefinite | **WRONG** | It means duration unknown. `ff` means longer than the representable maximum. No `00` event was sent. |
| There are 13 operational states | **WRONG** | CTA-2045-B Table 10-3 defines codes 0–14: 15 states. Revision B adds Running Price Stream (13) and Idle Price Stream (14). |
| The code's operational-state decoding is complete | **WRONG** | `OPERATIONAL_STATES` omits 13 and 14; `HEATING_STATES` consequently omits running price-stream state 13. |
| The repository covers the device's advertised Price Stream support | **WRONG** | Capability bit 7 is set. Live Get Accepted Pairs returned 10, and the standards-defined "no valid prices" Set Price Stream succeeded. The repo implements neither Price Stream nor states 13/14. |
| Setpoint response offsets are unknown and the trailing word is the setpoint | **WRONG** | §11.1.6 fully specifies response code, device type, units, signed setpoint 1 and optional setpoint 2. Taking the trailing word returns the wrong setpoint when setpoint 2 is present. |
| Device-info layout is not mapped | **WRONG** | §11.1.1.2 fully maps it. The live response identifies CTA revision B, vendor `0118`, device type `0003`, revision `020e`, capability bitmap `00000180`, model and serial fields. The ASCII-run heuristic works for this frame but is not a correct decoder. |
| Commodity decoder works for measured records | **WRONG** | It uses the full commodity byte as a dictionary key and does not mask the measured bit. A measured electricity record `80` would not be found as commodity `00`. It also ignores a nonzero response code. |
| Maximum payload support is unknown | **CONFIRMED live** | Data-Link request `08 03 00 02 18 00 ba 75` returned `08 03 00 02 19 07 a9 7e`; `07` encodes 256 bytes. |

CTA-2045-B's low-seven-bit commodity assignments through revision B are:

| Code | Assignment |
|---|---|
| `00` | Electricity Consumed |
| `01` | Electricity Produced |
| `02` / `04` | Natural Gas, English / metric units |
| `03` / `05` | Water, English / metric units |
| `06` | Total Energy Storage/Take Capacity |
| `07` | Present Energy Storage/Take Capacity |
| `08` / `09` | Rated Maximum Electricity Consumption / Production |
| `0a` | Advanced Load Up Total Energy Storage/Take Capacity |
| `0b` | Advanced Load Up Present Energy Storage/Take Capacity |

## Opcode and commodity sweeps

The full Basic opcode space `00`–`ff` was exercised with a safe, nonzero
Opcode2. Implemented or meaningfully handled opcodes were:

- `01` Shed, `02` End/Normal, `07` Present Relative Price, `09` Time Remaining
  in Price Period, `0a` Critical Peak, `0b` Grid Emergency, `0c` Grid Guidance,
  `0e` Outside Communication Status, `12` State Query and `17` Load Up.
- `08` Next Relative Price and `16` Simple Time Sync returned application NAK.
- Other tested values returned the generic Basic application NAK, except
  application ACK/NAK opcodes `03/04`, for which no application reply is
  expected.

This found no hidden Basic operating-mode function. Neutral price/guidance,
comm status and End/Normal were sent after the sweep.

All 256 commodity selector bytes were tested. Only low-seven-bit codes
`00`, `06`, `07`, `0a` and `0b` succeeded; selectors with the high bit set were
masked by the tank to the corresponding low code. All others returned response
2 with unsupported `ff ff ff ff ff ff` quantities. No hidden commodity was
found.

Intermediate category Opcode1 was swept across `00`–`ff` with Opcode2 `00`.
Full Opcode2 sweeps completed for the standard categories through `0a` before
the first version of the test was stopped for taking too long:

- `01/01` Device Info and `01/02` SGD Efficiency Level succeeded.
- `02/00` UTC Time succeeded.
- `03/03` Setpoint read succeeded; `03/04` Present Temperature returned Other
  Error. No other `03` subcommand exposed an operating mode.
- `06/00` Commodity succeeded.
- `09/00` OTA metadata succeeded.
- `0c/00` Advanced Load Up returned Not Enabled.

Targeted standards-shaped probes after the broad sweep found two additional
features omitted by the repository:

- User Preference Energy Reduction:
  request `08 02 00 03 0b 00 01 1f 1d`, reply
  `08 02 00 04 0b 80 00 05 4c 6a` — success, level 5.
- Price Stream Accepted Pairs:
  request `08 02 00 02 0d 01 df 5b`, reply
  `08 02 00 04 0d 81 00 0a 34 7a` — success, maximum 10 pairs, no
  export-support byte. Ten satisfies the general default minimum of eight, but
  is below the 64-pair minimum for a CTA-2045-B Level 2 water heater. The
  device-info reply does not state a certification level, so whether this is a
  certification violation is **UNVERIFIED**.
- The safe CTA-2045-B §11.8.3.3 invalidation message, which consists of all
  zero mandatory Price Stream fields, was accepted:
  request
  `08 02 00 0f 0d 02 00 00 00 00 00 00 00 00 00 00 00 00 00 f4 38`,
  reply `08 02 00 03 0d 82 00 91 27` — success. State remained Running
  Normal.

The initial broad sweep process was found still holding the tty after about 13
minutes and was killed. It was not resumed. Therefore manufacturer-specific
Intermediate categories and the entire two-byte opcode Cartesian product remain
**UNVERIFIED**, not silently treated as unsupported.

## README claim matrix

| README subject | Verdict | Notes |
|---|---|---|
| Local CTA monitoring/control, MQTT discovery, no cloud/iCOMM dependency | **CONFIRMED** | Live daemon, source and service inspection. |
| Cloud API reliability and 0/50/100 quantization | **UNVERIFIED** | Historical external-service observation; not necessary to CTA operation and not independently exercised. |
| Port-derived percentage to 0.1% and roughly two-second status | **CONFIRMED** | Live commodity values vary at Wh resolution; code rounds the derived percentage to 0.1. Full sequential status polling is approximately two seconds. It is derived, not directly reported as a percentage. |
| Exact tank model and revision | **CONFIRMED** | Device-info raw frame and A.O. Smith model page. Revision bytes `02 0e`. |
| "Voltex hybrid" product name | **WRONG** | A.O. Smith's exact-model page calls it Signature 900. |
| Pi Zero 2 W, `/dev/serial0 -> ttyAMA0`, PL011, Debian 13, Python 3.13 | **CONFIRMED** | Live host inspection. |
| Waveshare isolated HAT | **UNVERIFIED** | Cannot identify the physical adapter through SSH; consistent with the deployed wiring and user-supplied hardware description. |
| `enable_uart=1`, `miniuart-bt`, `core_freq=250` and PL011-on-GPIO explanation | **CONFIRMED** | Live boot configuration plus Raspberry Pi documentation. |
| AC form factor, pins 1/7/8 and 5/12, 208/240 V service phases, no neutral for this 240 V device | **CONFIRMED** | CTA-2045-B Appendix B. |
| This unit's swapped visual rows, removed key, exact meter voltages, `9.57 Ω`/`OL`, bonding and spacing | **UNVERIFIED** | Physical observations cannot be repeated through SSH and are not model publications. Keep explicitly labeled as observations of this unit. |
| Isolated serial interface and half-duplex pair | **CONFIRMED** | CTA-2045-B requires 1500 VAC isolation between serial and AC circuits and specifies two-wire RS-485 half duplex. |
| Readable commodity, state, cumulative energy, setpoint, model and serial values | **CONFIRMED** | Live frames. |
| All five listed demand-response selections are accepted | **CONFIRMED** | Live Basic opcode sweep. Curtailed and Heightened states were observed. |
| Setpoint and present-temperature limitations | **CONFIRMED** | See priority tests. |
| Front-panel modes cannot be written through this tank's standard CTA interface | **CONFIRMED** | See efficiency-level test; "no command exists" was wrong. |
| 356 W estimated compressor category | **CONFIRMED** | Live frame, high measured bit clear, and 130 journal samples. |
| 4500 W emitted during element use | **UNVERIFIED live** | 4500 W element nameplate is published; emitted value was not reproduced. |
| 4250/7275 Wh and Electric-mode Shed/Load Up behavior | **UNVERIFIED in this audit** | Requires a physical front-panel mode change. Retained as qualified local observations. |
| Load Up never uses an element | **UNVERIFIED as an absolute** | The observed tested Hybrid run remained at estimated 356 W, but one run cannot establish "never." |
| ~14-minute SGD Error | **CONFIRMED approximately** | Transition bracketed to 810.154–811.704 seconds after comm status; the hard-coded 851.0-second figure is **WRONG** for this trial. |
| SGD Error wipes the active event | **WRONG** | Heartbeat cleared the error and exposed the still-active Running Curtailed state. |
| Wire framing, ACK/NAK, checksum and 13-byte records | **CONFIRMED with corrections** | See protocol table: 13-bit length, response-code byte and measured bit matter. |
| Three historical debugging failures and the EPRI error-count root cause | **UNVERIFIED as history** | Code structure is consistent and the stale reader was reproduced during this audit, but the older `TIOCGICOUNT`, retransmission and libcea2045 logs were not available to independently attribute every detail. |
| Self-contained Python, trace hook, no C++ build, `flock` | **CONFIRMED** | Source inspection. `flock` only excludes cooperating processes; it cannot stop an unrelated reader that ignores the lock. |
| Repository layout | **CONFIRMED** for in-scope files | Direct inspection; `ble/` content excluded. |
| Install commands, environment defaults, password refusal | **CONFIRMED** | Source and deployed unit inspection. |
| Ten Home Assistant entities and only one writable select | **CONFIRMED** | Discovery source. "Only thing the tank lets you change" should be read as user-facing control; other DR/price/time messages also accept writes. |
| Poll-thread command handling, wake-up latency, event adoption | **CONFIRMED** | Source inspection; deployed journal showed an already-active Load Up being adopted. Exact latency remains scheduler/network dependent. |
| Manual tools use the same lock and `probe.py` traces raw chunks/frames | **CONFIRMED** | Source inspection. |
| Wi-Fi 20% loss, 300 ms spikes and before/after figures | **UNVERIFIED** | Historical site-specific measurement; no preserved dataset and not part of CTA. |
| EPRI library is a reference implementation | **CONFIRMED with qualification** | It is EPRI-authored and useful for revision A, but predates CTA-2045-B additions. |
| Intermediate response table is absent from a free specification | **WRONG** | The official CTA-2045-B PDF is publicly accessible and Table 11-2 contains it. |
| Operational-state enumeration comes only from the OpenADR guide | **WRONG** | It is normative in CTA-2045-B Table 10-3. |
| CTA-2045-B itself is paid | **WRONG currently** | CTA serves the complete February 2021 PDF publicly at the cited URL. |
| Findings apply to one model/revision, not all A.O. Smith devices | **CONFIRMED** | Correct and important limitation. |
| Tank is not a real-power metering device | **CONFIRMED for its CTA output** | Measured bit is clear. |
| MIT license | **CONFIRMED** | `LICENSE`. |

## Python docstring and comment matrix

### `mqtt/cta2045.py`

| Claim | Verdict |
|---|---|
| Module identity, 19200 8N1, self-contained implementation, trace hook, and original two-reader diagnosis | **CONFIRMED** except the historical attribution is **UNVERIFIED** without the old logs. |
| Half-duplex response handling and exclusive cooperative lock | **CONFIRMED**. |
| 4500/356 exact nameplate constants across hundreds of polls | **WRONG/UNVERIFIED** as a compound claim; see priority question 4. |
| Generic 16-bit wire length | **WRONG**; it is reserved 3 bits + 13-bit length. |
| Checksum docstring | **CONFIRMED**. |
| Duration docstring | Formula **CONFIRMED** for `01`–`fe`; zero-is-indefinite **WRONG**. |
| `PortBusy`, `Frame`, raw port, `read`, `Link`, frame extraction, request and drain behavior | **CONFIRMED as implementation behavior**. `_take_frame` is not a general CTA resynchronizer because it accepts only PDUs beginning with `08` and does not enforce reserved length bits. |
| Operational-state parser | **CONFIRMED for 0–12**, incomplete for revision B states 13–14. |
| Commodity parser layout | **CONFIRMED** except "lead byte" is response code; measured-bit and response-code handling are missing. |
| Hot-water formula and code-7 interpretation | Formula **CONFIRMED**; standard terminology and the 7275 Electric observation need the qualifications above. |
| Setpoint parser docstring | Read-only result **CONFIRMED**; layout-not-pinned-down **WRONG**; trailing-word logic is unsafe with optional setpoint 2. |
| Device-info parser docstring | Returned model/serial **CONFIRMED**; struct-not-mapped **WRONG**. |
| Tank helper, heartbeat and application-ACK descriptions | **CONFIRMED**. The exact timeout effect is pending the live test. |

### `mqtt/cta2045_mqtt.py`

| Claim | Verdict |
|---|---|
| MQTT/Home Assistant bridge without cloud/iCOMM | **CONFIRMED**. |
| Heartbeat every few minutes and ~14-minute timeout | Five-minute cadence **CONFIRMED**; SGD Error transition bracketed to 810.154–811.704 seconds. The event-dropping claim is **WRONG**. |
| Events are reissued; bounded duration protects cleanup | **CONFIRMED as daemon behavior**. Calling duration `00` "indefinite" is **WRONG**; the standard says unknown duration, although such an event may still require an explicit End. |
| Configuration entirely from environment; password not written | **CONFIRMED for this program**. |
| Event adoption maps Curtailment to Shed and Heightened to Load Up | **CONFIRMED as policy and live adoption behavior**. The state reports effect, so distinct curtailment commands cannot be recovered from it. |
| 4500/356 comments and non-power device class | Classification **CONFIRMED**, exact live 4500 **UNVERIFIED**. |
| Poll thread is non-reentrant; wake shortens command delay | **CONFIRMED by source**; "about a second" is approximate. |
| Tank never reports its remaining DR-event timer | **CONFIRMED for the standardized query surface inspected**. Opcode `09` is remaining price-period time, not remaining Shed/Load Up time. |
| Heartbeat before adoption avoids reading SGD Error instead of event | **CONFIRMED**. SGD Error masked Running Curtailed; heartbeat exposed it again, allowing correct adoption. |

### `tools/probe.py` and `tools/tank-cli.py`

All docstring claims are **CONFIRMED by source inspection**:

- `probe.py` sends the stated set of requests and prints decoded frames; with
  `--trace` it prints each raw chunk supplied by the OS. "Every raw byte" is
  true in the sense that every received chunk is hex-dumped, not one line per
  individual byte.
- `tank-cli.py` provides the documented status/watch/event/normal commands,
  uses the same cooperative `flock`, heartbeats before actions, and fails
  loudly while the daemon holds that lock.

## Final hardware state

**CONFIRMED:** the last manual state response was
`08 01 00 02 13 01 d3 62` (Running Normal). The cleanup closed its serial
descriptor. At audit handoff, `cta2045-mqtt.service` was enabled and active,
its process was the sole reported holder of `/dev/ttyAMA0`, and three subsequent
daemon polls reported Running Normal.

## Concrete code follow-ups

These are not included in the README-only proposed diff:

1. Parse and validate the three reserved length bits, then mask the 13-bit
   length.
2. Add operating states 13/14 and add state 13 to `HEATING_STATES`.
3. Parse Intermediate response codes before consuming response bodies.
4. Mask the commodity measured bit, retain/expose its measured/estimated flag,
   and rename code 7 internally to Present Take Capacity (the MQTT compatibility
   name can remain `energy_deficit`).
5. Replace the setpoint and device-info heuristics with the published layouts,
   signed values and units.
6. Correct both module docstrings' duration, estimated-power, timeout and
   event-survival language; replace or remove the incorrect 851-second constant.
7. Add read helpers for User Preference, Price Stream capability, UTC time and
   OTA metadata; add a safe Price Stream invalidation helper.
8. Decide explicitly whether to become CTA-conforming by ACKing complete
   application responses after bus turnaround.

[cta-b]: https://cdn.cta.tech/cta/media/media/ansi-cta-2045-b-final-2022.pdf
[epri]: https://github.com/epri-dev/CTA-2045-UCM-CPP-Library/tree/dbaffc88624efdfe15260e1efb5ebf2e4afdb24c
[epri-checksum]: https://github.com/epri-dev/CTA-2045-UCM-CPP-Library/blob/dbaffc88624efdfe15260e1efb5ebf2e4afdb24c/cea2045/cea2045/util/Checksum.cpp
[epri-commodity]: https://github.com/epri-dev/CTA-2045-UCM-CPP-Library/blob/dbaffc88624efdfe15260e1efb5ebf2e4afdb24c/cea2045/cea2045/message/CEA2045MessageCommodityResponse.h
[epri-temp]: https://github.com/epri-dev/CTA-2045-UCM-CPP-Library/blob/dbaffc88624efdfe15260e1efb5ebf2e4afdb24c/cea2045/cea2045/message/CEA2045MessageGetPresentTemperatureResponse.h
[epri-wh-test]: https://restservice.epri.com/publicdownload/000000003002016940/0/Product
[model]: https://www.aosmithatlowes.com/products/water-heaters/electric-water-heaters/hps10-50h45dv/
[manual]: https://assets.aosmith.com/damroot/Original/10004/2000603095.pdf
[pi-uart]: https://www.raspberrypi.com/documentation/configuration/computers/raspberry-pi.html#configure-uarts
