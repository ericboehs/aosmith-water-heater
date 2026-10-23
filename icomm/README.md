# iCOMM cloud and LAN channels — what the module actually talks to

The iCOMM module has two cloud channels and one LAN channel. All of them were
put under a man-in-the-middle harness from the tank Pi. The result, in one line:
**the TLS channel accepts any RSA certificate — trust is not checked at all — and
the ops channel is a custom encrypted UDP protocol on port 55055 with no TCP
fallback.**

> **Corrected 22 October 2026.** This page first concluded the opposite, that the
> module validates the chain. It does not: mitmproxy's own throwaway CA completed
> the handshake and carried application data in clear. What defeated every earlier
> attempt was the *signature algorithm*, not the CA — the module advertises
> `ecdsa_sha256` (P-256) but no P-384, and anything signed by the recovered eMudhra
> intermediate is P-384, which deadlocks the handshake for its full 30 s timeout.
> See [`../mitm/README.md`](../mitm/README.md) for the pcap and the working recipe.

This note records the method, the five experiments, and the evidence, including
the steps that failed and the conclusions that were wrong before being corrected.

| Channel | Where | Crypto | Access verdict |
|---|---|---|---|
| UDP `55055` to `ans-field.aylanetworks.com` | GCP | custom, 40-byte frames, position-independent block reuse | **No** — no keys, no plaintext |
| TLS `443` to `proxy-ingress-usfield.aylanetworks.com` | GCP | TLS 1.2, 4 legacy suites, no SNI, **validates chain** | **No** — rejects untrusted CA |
| `http://<module>/local_reg.json` (Same-LAN) | LAN | Ayla registration | **No** — hard 404, keys are A.O. Smith's |
| `http://<module>/regtoken.json` | LAN | none | **Read-only** — status only |
| CTA-2045 serial (RS-485) | LAN | plaintext | **Yes** — see the top-level README |

## Identity of the unit

Two identifiers fell out of this work that were not previously known:

| | |
|---|---|
| DHCP hostname (UniFi, wired into the lease) | `iCOMM-AC000W019949045` — the `AC000W…` suffix is the Ayla DSN |
| Per-device Ayla name | `iCOMMNextGenHP-71538015-device.aylanetworks.com` |
| `regtoken.json` response | `{"regtoken":"<rotating>","registered":0,"registration_type":"Same-LAN","host_symname":"NextGenHP"}` |

`host_symname: NextGenHP` confirms the next-gen heat-pump module, so the
next-gen register map (blocks 1/2/11) is the correct one. `registered: 0` with a
rotating token is the Same-LAN flow waiting for a cloud-pushed `lanip` config
and LAN key that belong to A.O. Smith's Ayla OEM account.

## Method: getting in the path

The module and the gateway are both on the same VLAN, so the tank Pi can be
inserted between them with a **targeted unicast ARP redirect** — the module's
cache is told the gateway's MAC is the Pi, and the gateway's cache is told the
module's MAC is the Pi. `net.ipv4.ip_forward=1` carries the traffic.

Everything is bounded and self-restoring. `arpspoof.py` runs under
`timeout(1)`, and on SIGTERM it re-sends the true MACs to both caches before
exiting, so a crashed run leaves at most one ARP-timeout of interruption.

### Use `sendp`, not `send`

The first version used scapy's `send()`, which is the **L3** sender: it discards
a crafted `Ether()` header, tries to resolve the destination from the packet's
IP layer, and falls back to broadcast. The spoofer printed a wall of

```
WARNING: MAC address to reach destination not found. Using broadcast.
```

and put **zero** frames on the wire — while `tcpdump` on the same interface
showed nothing from it. Switching to `sendp()` (the L2 sender) fixed it; the
first frame visible in the capture was then

```
22:23:40.362200 ARP, Reply 10.0.1.187 is-at e4:5f:01:87:88:e3
```

Keep the redirect **unicast**. An earlier draft broadcast the replies, which
would have poisoned every host on the LAN's view of the gateway. Addressing each
reply to the one MAC whose cache needs it confines the effect to two caches.

## Phase 1 — architecture discovery

With the Pi in path and a passive capture running, the module's traffic resolved
into two independent channels:

```
10.0.1.187.49154 > 34.72.223.197.55055   UDP, length 40     (ops channel)
10.0.1.187.49244 > 35.186.226.188.443    TCP, established   (proxy ingress)
```

Traffic transits in **both** directions (verified by destination MAC, not by
promiscuous capture):

```
d0:17:69:f8:f3:1c > e4:5f:01:87:88:e3    module → Pi
e4:5f:01:87:88:e3 > d0:21:f9:c5:9e:30    Pi → gateway
```

The module never answers ARP broadcasts itself, which made the poisoning quiet:
there is no reply to race against.

An early ICMP redirect

```
10.0.1.168 > 10.0.1.187: ICMP redirect 34.72.223.197 to host 10.0.1.1
```

gives the traffic away and should be suppressed:

```bash
sysctl -w net.ipv4.conf.wlan0.send_redirects=0
sysctl -w net.ipv4.conf.all.send_redirects=0
```

## Phase 2 — what happens when the ops channel is blackholed

`iptables -I FORWARD` DROP on `udp/55055`, both directions, for five minutes.

There is **no TCP fallback.** The module does not escalate to the TLS channel.
What it does instead is rotate endpoints through DNS:

```
14:14:46  DROP installed
14:16:01  retries accelerate from ~24–48 s to 4–8 s
14:17:21  ans-field.aylanetworks.com → 34.29.119.3      new session, dropped
14:18:25  ans-field.aylanetworks.com → 104.198.170.253
14:19:29  ans-field.aylanetworks.com → 35.224.195.101   new session, dropped
14:20:20  DROP removed
14:20:33  recovered in 13 s: new session to 34.46.97.155, exchange succeeds
```

`ans-field.aylanetworks.com` is a **round-robin pool**, confirmed by resolving it
repeatedly: `34.133.156.107`, `104.198.170.253`, `104.154.198.204`,
`34.46.97.155`. Every address is GCP (`*.bc.googleusercontent.com`). The server
side even changes IP mid-session without the module caring — the same client port
`49154` continued against a new address.

DNS is **hardcoded to 1.1.1.1**, not taken from the gateway, which is why the
module keeps working when the local resolver is unavailable and also why its
resolution is interceptable while the Pi is in path.

## Phase 3 — DNS interception, and the TLS profile

First attempt: answer the module's `ans-field` queries ourselves by racing the
real resolver. That **failed**, and the failure is instructive:

```
16:06:54.492  REAL   1.1.1.1 → module   A 34.16.61.58     ← genuine answer
16:06:54.559  FORGED  1.1.1.1 → module   A 10.0.1.168      ← ours, 67 ms later
```

The module used the first answer. **Do not race the resolver — replace it**: drop
the module's own DNS in `FORWARD` and answer locally, so there is no first answer
to lose to. That is what `dns_race_v2.py` does, and it is confirmed working:

```
ANSWER icommnextgenhp-71538015-device.aylanetworks.com = 35.186.226.188
```

(That run did not capture a fresh UDP handshake: the module's existing `ans-field`
session was healthy, so it had no reason to re-resolve until the 55055 DROP went
in eight minutes into the window. Start the DROP first and allow 20+ minutes.)

While the TLS channel was up, the handshake was captured in full. Name resolution
chain:

```
iCOMMNextGenHP-71538015-device.aylanetworks.com
  → CNAME proxy-ingress-usfield.aylanetworks.com
  → 35.186.226.188
```

### The client's TLS fingerprint

```
TLS version:        0303 (TLS 1.2), session_id_len 0
ClientHello:        77-byte handshake (82 bytes with record header) — identical every time
Cipher suites (4):  0x0039  DHE-RSA-AES256-SHA
                    0x002f  RSA-AES128-SHA
                    0x0035  RSA-AES256-SHA
                    0x00ff  renegotiation_info SCSV
Extensions (3):     0x000d signature_algorithms (12 B)
                    0x0016 encrypt_then_mac
                    0x0017 extended_master_secret
SNI:                ABSENT
ALPN / status_request / SCT:  ABSENT
Client certificate: NONE (no mutual TLS)
```

A four-suite, CBC-only, no-SNI, no-OCSP hello is the signature of a small
embedded TLS stack. It also means there is nothing in the handshake to route on —
interception has to be by IP and port, not by hostname.

The server presents an ordinary public certificate:

```
subject: C=US, O=Ayla Networks, Inc., CN=*.aylanetworks.com    30 Sep 2026 – 16 Apr 2027
issuer:  C=GB, O=Sectigo Limited,
         CN=Sectigo Legacy Server Authentication CA OV R30   23 Jun 2025 –  6 Dec 2030
root:    C=US, O=Entrust, Inc.,
         CN=Entrust Root Certification Authority - G2        7 Jul 2009 –  7 Dec 2030
chain:   ~3793 bytes
```

Nothing exotic: no private CA, no pinning hint, no OCSP stapling.

Two corrections to what this page first recorded. The intermediate **rotated** —
it read `...CA OV R3` when first captured and is now `...CA OV R30`, issued
30 September 2026 — so anything keyed to the old name will not match. And the
chain does **not** terminate in a Sectigo-branded root: the Sectigo-named
intermediate is cross-issued under **Entrust Root Certification Authority - G2**
from 2009, which is therefore the only root this module is known to trust. That
matters because it is where any replacement certificate has to live; see
[`mitm/README.md`](../mitm/README.md).

## Phase 4 — does the module validate the certificate?

This is the decisive test, and it needs no CA compromise: point the module's 443
at mitmproxy, let mitmproxy answer with **its own** CA, and watch what the module
does.

Wire evidence for one attempt:

```
16:35:57.255  module → ClientHello (82 B)        16 03 01 00 4d 01 00 00
16:35:57.xxx  mitmproxy → 916 B                  16 03 03 00 59 02 …   ServerHello + Certificate
16:35:57.370  module ACKs the certificate
16:36:22.122  module → [R] RESET                 no ClientKeyExchange, no Alert, no AppData
```

That loop repeated across **six consecutive fresh connections**
(`49350 → 49351 → … → 49359`), and a filtered count of client payloads over
100 bytes — the size class a `ClientKeyExchange` occupies — returned **zero** for
the whole window.

For comparison, the *real* session sends a 262-byte `ClientKeyExchange` and then
application data. The absence of that message is the whole answer.

**Verdict at the time: certificate chain validation is enforced.** That reading was
wrong. mitmproxy's own CA — a root the module cannot hold — later completed the
handshake and carried application data; the module sends no `bad_certificate` alert
because it never rejects a certificate. The 24-second reset was the module's 30-second
handshake timeout expiring on a *signature it could not verify* (P-384), not on a
trust decision. See [`../mitm/README.md`](../mitm/README.md).

### Building the path took three fixes

Worth recording, because each one looked like "the module ignores us":

1. **DNAT only affects new connections.** With an established session already
   running, the counter sat at 0 while traffic visibly flowed. Wait for a fresh
   SYN.
2. **Hairpin DNAT needs SNAT.** Pointing `443` at a host on the *same* subnet
   makes that host reply directly to the module, bypassing the Pi, so the reverse
   translation never happens. Add
   `-t nat -A POSTROUTING … -j SNAT --to-source <pi-ip>`.
3. **A Mac with two interfaces on one subnet replies asymmetrically.** This host
   has `10.0.1.188` (en0) and `10.0.1.195` (en33); replies left by the wrong
   interface with the wrong source, breaking the translation. A host route fixed
   it instantly:

   ```bash
   sudo route add -host 10.0.1.187 10.0.1.168
   ```

## Phase 5 — is the rejection about the name, or the CA?

The Phase 4 test used a certificate whose SAN was the *IP*, so it could not
distinguish "wrong name" from "untrusted CA". Phase 5 removes that ambiguity by
presenting a certificate with the exact expected name, still signed by mitmproxy:

```
subject: CN=iCOMMNextGenHP-71538015-device.aylanetworks.com
issuer:  CN=mitmproxy, O=mitmproxy
SAN:     DNS:iCOMMNextGenHP-71538015-device.aylanetworks.com
```

The module behaved **identically**: ACK the certificate, 24 seconds of silence,
reset, new connection, repeat.

**Verdict: the gate is CA trust, not hostname matching.** Hypothesis "it only
checks the name" is eliminated.

**Still untested: pinning.** A device that pins a specific certificate or public
key would also reject a publicly-trusted certificate for the right name, and
there is no way to reach that question without a certificate the device already
trusts. Pinning is therefore *unknown*, not *ruled out*.

## The UDP ops channel

Framing is fixed and simple:

```
8-byte constant header:  04 00 00 00 3f b8 0f 00
32-byte body             (looks encrypted)
total 40 bytes, every datagram, both directions
every datagram sent twice (distinct IP IDs, identical payload)
```

Cadence is roughly 24–48 s idle, tightening to 4–8 s when blocked.

The interesting property is block reuse. Across the corpus, 16-byte blocks recur
**verbatim at different offsets**: in 13 of 25 consecutive module→server packets,
the second half of one datagram equals the first half of the next.

```
f0de16c0 f1f6fb8a d6711d1d 563521ec | 50f10bf8 02c8007a ef3975bc bd4c1936
50f10bf8 02c8007a ef3975bc bd4c1936 | 1ac346b3 05b861be d4c4c3e3 c4b52eac
1ac346b3 05b861be d4c4c3e3 c4b52eac | 0609139f f33acebc c1197f7c f9f6f504
```

After de-duplicating the send-twice copies, 58% of 16-byte blocks appear more
than once. Identical ciphertext at different offsets means the transform is
**position-independent** — consistent with ECB-style block encryption, or with a
fixed repeating keystream.

**This is an observation, not an attack.** It says the construction leaks block
equality; it does not hand over the plaintext or the key. No attempt was made to
recover either, and none is planned.

## What this leaves

- The UDP channel is separate and does not become readable just because TLS does.
- Same-LAN registration (`local_reg.json`) is a hard 404 and the key material
  belongs to the OEM account, so it stays closed as well.
- **CTA-2045 is unaffected by any of this.** It is a serial link on the tank, it
  is already implemented, and it is the only control path that survives the
  vendor's cloud being switched off.

### On the CA-compromise shortcut

The eMudhra intermediate CA compromise of October 2026 (eight intermediates whose
private keys are recoverable due to flawed key generation) is the shortcut
to take here: it will produce a publicly-trusted
`*.aylanetworks.com` certificate, and Phase 4 shows this module would accept one.

**Correction, from actually doing it:** the certificate that came out of that
path has an **ECDSA P-384** leaf key, and this module cannot be served an ECDSA
certificate at all. Its four suites are `DHE-RSA-AES256-SHA`, `RSA-AES128-SHA`,
`RSA-AES256-SHA` and the renegotiation SCSV — no ECDHE, so no ECDSA. OpenSSL
answers `NO_SHARED_CIPHER` and the device never reaches chain validation, which
on the wire is indistinguishable from being ignored. A usable leaf needs an
**RSA-2048 key** with the same SAN; `../mitm/check-cert.sh` is the one-line test,
and `../mitm/` is the harness that serves it.

A re-minted RSA-2048 leaf negotiates. The chain is served via the intermediate's
**AAA Certificate Services** cross-sign, so the module only has to trust that
2002 Comodo/Sectigo root — the one it is known to use for Ayla's own chain —
rather than an eMudhra root it may never have shipped.

**Resolved, 22 October 2026:** the answer is that this last doubt was the whole
problem and the trust store never entered into it. The leaf's own signature is
`ecdsa-with-SHA384` and the module's `signature_algorithms` extension offers
`ecdsa_sha256` (P-256) alone — no P-384 scheme — so the leaf is unverifiable
regardless of which root the chain ends at. An RSA/SHA-256 leaf, from any CA at
all, works. There is no certificate-side fix needed and no root to chase.

## What the cloud channel actually carries

With the TLS channel opened (see [`../mitm/README.md`](../mitm/README.md)), a
full session was decoded: **device id 29689890**, and every cloud-side change
produces one connection carrying the same four requests in the same order.

```
POST /devices/29689890/properties/superBlockDyn1/datapoints.xml    telemetry push
GET  /devices/29689890/commands.xml?signal=-54                     command poll (RSSI)
POST /devices/29689890/properties/setBlockParameter/datapoints.xml acknowledgement, empty value
POST /devices/29689890/properties/superBlockDyn1/datapoints.xml    telemetry again
```

Over eight minutes and three setpoint changes, that is all Ayla queued: three
`setBlockParameter` commands, no schedules, no `cmds`, no other property pushes.

### The setpoint command is the BLE frame format

`setBlockParameter` carries the same proprietary framing already recovered from
the BLE console — `0xBD` prefix, `0x40` = block write — with the setpoint as
**16-bit big-endian, 1/256 °C**, exactly the `convertTemperature` formula
documented in [`../ble/README.md`](../ble/README.md):

| Asked for | 1/256 °C | Value |
|---|---|---|
| 122 °F | 12800 `0x3200` | `BD 40 08 0B 06 32 00 56` |
| 121 °F | 12658 `0x3172` | `BD 40 08 0B 06 31 72 28` |
| 120 °F | 12516 `0x30E4` | `BD 40 08 0B 06 30 E4 5E` |

The frame is **the BLE framing exactly**, and it resolves completely:

```
BD  <cmd=40>  <total_len>  <block>  <offset>  <value...>  <crc8>
```

* `0xBD` prefix, `0x40` = block write, and **`total_len` counts the whole frame
  including the CRC** — the same convention as `BD F2 05 01 D8` in `ble/`.
* `block = 0x0B` (11), `offset = 0x06`. Block 11 is not in `ble/README.md`'s
  8/13/26/27 table, but that table covers the block-26 iCOMM path; the next-gen
  layout this module advertises (`host_symname: NextGenHP`) uses blocks 1/2/11/27/28.
* **the trailing byte is `crc8` over the frame**, using the table already
  brute-forced into `ble/icomm_ble.py` — no new reverse engineering needed.

Verified by regenerating the three frames the cloud sent, byte-for-byte including
the CRC, then by building one for a setpoint the cloud never sent:
`BD 40 08 0B 06 33 AB E2` is 125 °F.

**Block 26/0 — the BLE map's mode register — does not work here.** Tested with
`frame.py --mode electric` → `BD 40 07 1A 00 20 B2`: the module **acknowledged**
it (the `setBlockParameter` ack post) and then carried on in heat-pump mode —
`b[3]` 30/31 still `0/5`, air still 47 °F at the evaporator exhaust. The cloud
channel accepts the frame, acks it, and ignores it. The cloud's register map is
not the BLE map; the setpoint already proved that (11/6 here, 13/2 there), and
mode is the second case. The mode register the *cloud* uses is still uncaptured —
the way to get it is a passthrough window while a mode change is made in the app.

**That frame was then served to the module, and it obeyed.** With a response rule
answering `commands.xml`, the device acknowledged the write and its next three
telemetry posts reported `0x33AB` = 125.00 °F — a value Ayla never sent. Writing
120 °F back through the cloud returned it to `0x30E4` = 120.00 °F. The write path
is ours: no Ayla issuance, no key recovery, just a TLS endpoint and a CRC.

### And the telemetry uses the same encoding

`superBlockDyn1` is a comma-separated list of hex blocks, and the temperatures in
it decode with the same 1/256 °C rule, which ties the two sides together:

| Field | Where | Evidence |
|---|---|---|
| current setpoint | `block[3]` offsets **0** and **12**, **mirrored** at `block[1]` offsets **80 / 84 / 112 / 114** | every value we wrote appeared in all six places within one post: 149 → 148 → 149 |
| max setpoint | `block[1]`, offset **86** | `0x418E` = 16782 = 150.0 °F, constant |
| **water temperature, tank top** | `block[1]`, offset **0** | 129 → 108 °F across six posts while the setpoint never moved — the tank being drawn down |
| **water temperature, tank bottom** | `block[1]`, offset **4** | 99 → 87 °F over the same posts, riding ~30 °F below the top |
| hot water available | `block[3]`, offset **30** | 0 while the tank was drawn down, 100 once reheated |
| air at the unit | `block[4]`, offset **30** | 41–48 °F while the heat pump ran, 82–83 °F in electric mode |
| Wi-Fi RSSI | `block[4]`, offset **40**, signed 16-bit | −51…−55 dBm, matching `?signal=-54` on the polls |
| device clock | `block[1]`, offsets **70–73**, 32-bit BE Unix time | advanced 4.2 h between two sessions, matching the wall clock |
| **electric element power** | `block[4]`, offsets **12–13**, 16-bit BE watts | `0x1194` = **4500** while the element heats, `0` when idle — the element's rated figure |
| **line voltage** | `block[1]`, offset **99**, whole volts | 235, drifting to 236 |
| **mode** | `block[3]`, offsets **30** and **31** | electric `100` / `1`, heat-pump `0` / `5` — three sessions, plus the air sensor agreeing |
| air at the unit (mode cross-check) | `block[4]`, offset **30** | 82–83 °F in electric, 41–48 °F in heat-pump (cold evaporator exhaust) |

**Where the app's "4315 W" comes from.** Not a measured field: 4500 W is the
element rating at 240 V, and a resistive element follows P ∝ V², so at the 235 V
this unit actually sees, `4500 × (235/240)² = 4314 W` — the app's number to within
a watt. A plugin should report `watts = 4500 × (volts/240)²` while `block[4]`
12–13 is non-zero, and 0 otherwise.

`block[3]` 30/31 took three attempts and is worth recording as such: called mode,
then "corrected" to element duty, then put back to mode once the air temperature
settled it. Element duty and electric mode coincide whenever the tank is heating,
which is exactly what makes them hard to tell apart — the discriminator is
`block[4]` 30, because a running heat pump exhausts cold air (41–48 °F) and an
electric element does not (82–83 °F).

The "0% full" the app shows is still not in `block[3]`: those `100` values are the
tank being physically full of water while holding no hot water.

**Corrections, kept because they were wrong in public.** Mode is *not* at
`block[1]` 89/91 — that byte reads `0x08` in both heat-pump and electric mode, so
it is something else. Tank fullness is *not* at `block[3]` 35/39 — those read
`100` throughout, including when the tank was drawn down. The real availability
field is `block[3]` 30, which went 0 → 100. The one byte that did track the
heat-pump/electric change is `block[3]` 31: `5` → `1`. Candidate, not confirmed.

`block[1]` 6…12, 76 and 78 decode as `0.0 °C` — zeros, not 32 °F. Reading them as
temperatures is how the wrong mode field got picked in the first place.

Temperatures are `1/256 °C` big-endian throughout. Distinguish the two that look
alike: `0x2E1C` = 11804 = 46.11 °C = **115.0 °F** is the water, while `0x30E4` =
120.0 °F is the setpoint. Only the setpoint moves when a write lands.

## Artifacts

Captures are kept outside the repo, in
`~/Documents/aosmith-water-heater/captures/`, with an `INDEX.md` listing sizes,
SHA-256 prefixes and what each one proves. Phases 1–5 were taken on the tank Pi
(`~` = `/home/ericboehs`) and copied there; phases 6–8 are from the gateway-redirect
windows on the Mac.

| File | Size | Contents |
|---|---|---|
| `icomm_phase1_final.pcap` | 133 KB | architecture discovery, both channels, both directions |
| `icomm_phase2_fallback.pcap` | 253 KB | UDP blackout: retry acceleration, DNS endpoint rotation, recovery |
| `icomm_phase3_dns.pcap` | 427 KB | DNS interception attempt (race lost), full TLS handshake + server chain |
| `icomm_phase4_mitm.pcap` | 329 KB | mitmproxy served its own CA — module resets |
| `icomm_phase5_pinning.pcap` | 63 KB | correct-name cert, same rejection |
| `icomm_phase6_p384_stall.pcap` | 10 KB | P-384 leaf: full flight delivered, then 30.000 s of silence, RST, retry |
| `icomm_phase7_rsa_accepted.pcap` | 21 KB | RSA/SHA-256 throwaway CA **accepted**: full handshake, app data in clear |
| `icomm_phase8_rsa_logged.pcap` | 20 KB | same session, captured alongside the flow log |
| `icomm_phase8_flows.jsonl` | 14 KB | six decrypted exchanges, bodies included |
| `icomm_phase9_long_session.pcap` | 64 KB | eight minutes, three cloud-side changes |
| `icomm_phase9_flows.jsonl` | 38 KB | 15 exchanges: command polls, acknowledgements, telemetry posts |

Also captured but not preserved: the truncated first Phase 1 capture, lost to a
stale-ownership `Permission denied` on the output file.

## Scripts

| File | Role |
|---|---|
| `arpspoof.py` | unicast ARP redirect, module ↔ gateway; restores on SIGTERM. **Use `sendp`.** |
| `dns_race_v2.py` | answers the module's DNS from capture; pairs with a `FORWARD` DROP on its port 53 |
| `silent55055.py` | receive-only UDP 55055 listener; never replies, so no ICMP unreachable is generated and the module keeps sending |

`../mitm/` builds on the last of these: it terminates the TLS channel with a
certificate the module will believe, logs the flows in clear, and can inject
responses. Its README is the next experiment in this chain.

Prior work that this note builds on lives in `../ble/`: `portscan2.py` (the gentle
scan that established port 80 as the only open TCP port) and `lanmode.py` (the
Same-LAN knock that established `local_reg.json` 404s).

### Rerun

```bash
# on the Pi, as root
sysctl -w net.ipv4.ip_forward=1
sysctl -w net.ipv4.conf.wlan0.send_redirects=0
iptables -t nat -A PREROUTING  -s 10.0.1.187 -d 35.186.226.188 -p tcp --dport 443 \
         -j DNAT --to-destination <proxy-host>:<port>
iptables -t nat -A POSTROUTING -s 10.0.1.187 -d <proxy-host> -p tcp --dport <port> \
         -j SNAT --to-source 10.0.1.168

timeout 900 python3 arpspoof.py wlan0 &
timeout 880 tcpdump -i wlan0 -U -w /tmp/icomm.pcap "host 10.0.1.187"

# on the proxy host, if it has two interfaces on this subnet
route add -host 10.0.1.187 10.0.1.168
```

Clean up with `iptables -F` in `FORWARD`, and in both `nat` chains, and delete the
host route. Verify the module is still serving `regtoken.json` afterwards.

## Gotchas that produced wrong conclusions

- **`pkill -f arpspoof.py` kills the shell that runs it** — the remote `bash -c`
  command string contains the pattern. Kill by PID.
- **A 0-byte pcap is not a failure.** `tcpdump` buffers; check with a live capture
  before concluding nothing arrived.
- **Promiscuous capture is not proof of transit.** Verify by destination MAC.
- **DNAT counters stay at 0 for established flows.** Only new connections are
  translated.
- **SIGKILL on the spoofer skips the ARP restore.** Start the replacement first,
  then kill the old one, so there is no poison-free gap.
- **Unsolicited ARP *replies* (`op=2`) alone did not take on this module in the
  October 2026 retry.** With the spoofer running, cloud traffic to the module
  transited the Pi (so the gateway's cache was poisoned) but not one frame came
  back from the module in four minutes of watching, and the DNAT counter stayed
  at 0: its traffic still went straight to the gateway.
- **Gratuitous ARP *requests* (`op=1`) wedge this module's cache.** Adding them
  to the cycle, plus instant answers to the module's own `who-has` for the
  gateway, produced a request storm — 168 requests in 90 seconds, every one
  answered within milliseconds and none accepted. The cache is being invalidated
  rather than filled. Do not use `op=1` against this device; it briefly breaks
  its cloud link.
- **The module times out its own HTTP endpoint under TLS retry stress** — for
  about 20 seconds after a failed interception window, then recovers. Do not
  read that as a dead module.
