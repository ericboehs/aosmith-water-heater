# Intercepting the iCOMM module's TLS channel

`icomm/` established that the module validates the certificate chain and rejects
a locally-minted CA. This directory is the harness that terminates that TLS
channel with a certificate the module should believe, logs what crosses it, and
can inject responses.

## Result, 22 October 2026: any RSA certificate is accepted

The decisive experiment served mitmproxy's **own throwaway CA** — an RSA-2048 leaf,
`sha256WithRSAEncryption`, carrying `CN=*.aylanetworks.com`, under a root that is in
no trust store anywhere. The module completed the handshake and carried the session:

```
0.008  module  ClientHello (82 B)
0.096  proxy   ServerHello | Certificate(999 B) | ServerKeyExchange | ServerHelloDone
5.777  module  ClientKeyExchange | CCS | Finished        <- 5.7 s of DH modexp
5.785  proxy   CCS | Finished
5.807  module  app data
...
GET  /devices/29689890/commands.xml?signal=-53                          -> 200
POST /devices/29689890/properties/setBlockParameter/datapoints.xml      -> 200
POST /devices/29689890/properties/superBlockDyn1/datapoints.xml         -> 200
```

So this module **does not validate the certificate chain.** The trust store is
irrelevant; the whole "which root does it hold" question was a red herring.

### Why the eMudhra certificate failed, in the device's own words

The module's ClientHello carries a `signature_algorithms` extension:

```
rsa_pkcs1_sha512, rsa_pkcs1_sha384, rsa_pkcs1_sha256, ecdsa_sha256 (P-256), rsa_pkcs1_sha1
```

No P-384 scheme. Every certificate signable from the recovered `EM OV TLS CA - G3C`
key is signed **P-384** (`ecdsa-with-SHA384`), so the module has no algorithm with
which to verify it — and the failure is a hang, not a verdict:

```
ServerHelloDone -> 30.000 s of silence -> RST, no TLS alert, retry every 30 s
```

An unsupported *root* is accepted; an unsupported *signature algorithm* deadlocks the
handshake for its full 30-second timeout. That difference is the entire story, and the
earlier 24.9 s figure was a coarser TCP-level observation of the same timer.

### The working recipe

* any **RSA-2048** leaf, signed **SHA-256**, with a name matching `*.aylanetworks.com` —
  mitmproxy's default CA is fine, no procurement and no key recovery
* a **2048-bit** DH group (`<confdir>/mitmproxy-dhparam.pem`; the default is 4096)
* TLS 1.2 and the module's four legacy suites
* budget **≥ 15 s**: the session only starts after ~5.7 s of DH exponentiation

## Captures

The pcaps and flow logs live outside the repo, in
`~/Documents/aosmith-water-heater/captures/`, with an `INDEX.md` giving each
file's size, SHA-256 prefix and what it proves. They are not committed: pcaps are
gitignored by policy and they carry the tank's telemetry.

Read one with the reassembler in this directory rather than a hexdump — a server
flight is split across TCP segments, and per-segment parsing lies about which
handshake messages were actually delivered:

```bash
./mitm/tls-window.py ~/Documents/aosmith-water-heater/captures/icomm_phase7_rsa_accepted.pcap
```

The pair worth comparing is `icomm_phase6_p384_stall` against
`icomm_phase7_rsa_accepted`: identical in every respect except the certificate's
signature algorithm, and opposite outcomes. `icomm_phase8_flows.jsonl` is the
decrypted conversation from the accepted one, and `icomm_phase9_*` is a longer
session that decodes the cloud's setpoint command and the matching telemetry
offsets — see that directory's `INDEX.md`.

## What the appliance supports, measured

Everything here came off the wire from this unit on 21–22 October 2026. None of it
is from the module's datasheet, and several of these facts cost a full evening to
establish — re-derive them and you will spend the same evening.

| Property | Measured behaviour | How it was established |
|---|---|---|
| TLS version | 1.2 only; ClientHello is 77 bytes of handshake, 82 with the record header, and identical every time | Phase 3 capture, reproduced with our own client |
| Cipher suites | exactly four: `DHE-RSA-AES256-SHA`, `RSA-AES128-SHA`, `RSA-AES256-SHA`, `renegotiation_info` SCSV. No ECDHE, no GCM, no TLS 1.3 | the hello itself |
| Authentication type | **RSA only.** No ECDSA suite is offered, so no ECDSA-keyed certificate can be served at all | `--emudhra` and an ECDSA leaf both died at `no shared cipher` before any certificate was read |
| Leaf **key** | must be RSA. RSA-2048 negotiates on the first try; the module's own preference wins (`DHE-RSA-AES256-SHA`) | minted leaves |
| Leaf **signature** | may be ECDSA. An RSA-2048 leaf signed `ecdsa-with-SHA384` negotiates normally | our mint |
| SNI | **absent**, every connection. The certificate must be selected without it | all captures |
| Client certificate | none requested, none presented | Phase 3 |
| Chain validation | **not enforced.** A leaf from mitmproxy's own throwaway CA — a root it cannot possibly hold — completed the handshake and carried application data | pcap, 22 Oct 2026 |
| Name check | the leaf that worked carried `CN=*.aylanetworks.com`; whether a mismatched name is rejected has not been tested | pcap |
| Trust store | **irrelevant in practice.** Ayla's own chain roots at `Entrust Root Certification Authority - G2` (2009), but nothing on this device checks for it | controlled experiment, below |
| Leaf signature algorithm | must be one it advertises: `rsa_pkcs1_sha256/384/512`, `rsa_pkcs1_sha1`, or `ecdsa_sha256` (**P-256 only**). **P-384 hangs the handshake** | its own `signature_algorithms` extension |
| Revocation checking | not observable; the module makes no HTTP requests except to Ayla, so it cannot fetch a CRL or an OCSP responder | traffic |
| Trust store writable? | **no known route**: port 80 serves `regtoken.json` only — a 30-path sweep (`/config`, `/setup`, `/debug`, `/trust.pem`, `/cert.pem`, …) returns the same branded 404 for everything else; there is no shell; the one writable provisioning path (Same-LAN registration) belongs to A.O. Smith's Ayla OEM account | `ble/portscan2.py`, `ble/lanmode.py`, sweep of 22 Oct 2026 |
| Failure signature | **30.000 s** of silence after `ServerHelloDone`, then RST, no TLS alert, fresh retry every 30 s | pcap |
| Handshake cost | ~5.7 s between `ServerHelloDone` and `ClientKeyExchange` — RSA-2048 modexp on this CPU. Do not call a 10 s pause a failure | pcap |
| TLS channel cadence | **event-driven, not polled**, and a press of the cloud (setpoint write) makes it dial in ~10 s | `conntrack`, HA write, three windows |

### The chain it does trust

Ayla's ingress, as served and as this module accepts it:

```
leaf   C=US, O=Ayla Networks, Inc., CN=*.aylanetworks.com      30 Sep 2026 – 16 Apr 2027
  <-   C=GB, O=Sectigo Limited, CN=Sectigo Legacy Server
         Authentication CA OV R30                              23 Jun 2025 –  6 Dec 2030
  <-   C=US, O=Entrust, Inc., CN=Entrust Root Certification
         Authority - G2            (self-signed, in the chain)    7 Jul 2009 –  7 Dec 2030
```

Two things worth keeping. First, **the root is Entrust, not Sectigo** — the
Sectigo-named intermediate is cross-issued under the 2009 Entrust root, so what
this module demonstrably trusts is a 2009-era root, and its bundle is old and
broad rather than modern and narrow. Second, the intermediate **rotated**: what
`icomm/README.md` recorded as `...CA OV R3` is now `...CA OV R30`, dated
30 September 2026. Anything keyed to the old name will not match.

That chain is also why mitmproxy needs `upstream-entrust-g2.pem`: mitmproxy ships
certifi, certifi has dropped Entrust, so a perfectly valid Ayla chain fails
upstream verification and surfaces as a 502 that looks like the module's fault.

### Pre-flight facts, before anyone orders a certificate

**The leaf the module accepts today is `sha256WithRSAEncryption`, RSA-2048.** That
settles the question a procurement review will ask: an SHA-1-parity problem does
not exist here, because this verifier is demonstrably happy with RSA/SHA-256.
A new RSA/SHA-256 leaf is not going to be rejected on signature grounds.

Those are also the only two signature algorithms that matter, and the module has
only ever been offered the other one. Everything we can sign from the recovered
`EM OV TLS CA - G3C` key is **ECDSA**, under every available cross-sign — so no
experiment so far has actually tested the trust store. The rejection of the AAA
and emSign chains is *confounded*: RSA/SHA-256 accepted, ECDSA never accepted.
For a small mbedTLS build that is the expected shape — `MBEDTLS_ECDSA_C` is
optional, and a device that only ever validates its own RSA chain has no reason
to include it.

**DH size matters, but not yet.** mbedTLS verifies the `Certificate` message
before it ever looks at `ServerKeyExchange`, so a DH mismatch cannot be what
produced the 24.9 s stall. It will matter the moment a trusted RSA leaf exists,
and there is a real trap:

| | Detail |
|---|---|
| mitmproxy's default group | **4096-bit** (`DEFAULT_DHPARAM` in `certs.py`) |
| 2048-bit group | works, verified with `s_server` and with the proxy |
| 1024-bit group | refused outright by OpenSSL 3.5/4.0 at its default security level — the handshake dies before a cipher is chosen, which on the wire looks like the client never getting a server flight |

To change it, note that `CertStore.create_store()` **overwrites**
`<confdir>/mitmproxy-dhparam.pem` when it creates the CA, so the order is:
start the proxy once to create the confdir, replace that file, then restart.

**Our leaf cannot use static-RSA suites.** Its `keyUsage` is
`Digital Signature` only, with no `keyEncipherment`, so `RSA-AES128-SHA` and
`RSA-AES256-SHA` are unusable with it — only the DHE suites are. Any test that
wants to remove DH from the picture entirely (`RSA-AES128-SHA` has no
`ServerKeyExchange`) needs the leaf re-minted with `keyEncipherment` first.

## The two gates, and where they stand

> **Superseded on 22 October 2026.** Gate 2 was not a gate: the module accepts any RSA
> certificate, trusted or not. Kept because it documents how the wrong hypothesis was
> reached, and because `check-cert.sh` is still the fast way to prove an offer is
> negotiable.

**Gate 1 — the leaf key must be RSA. Passed.**

The module's ClientHello offers four suites and every one of them authenticates
with an RSA server key:

```
0x0039  DHE-RSA-AES256-SHA
0x002f  RSA-AES128-SHA
0x0035  RSA-AES256-SHA
0x00ff  renegotiation_info SCSV
```

No ECDHE, no ECDSA. The first leaf minted for this was ECDSA P-384 and could not
be negotiated at all — OpenSSL answered `NO_SHARED_CIPHER`, so the device never
reached chain validation. On the wire this is a closed connection, which is the
same shape as "the module ignores us" and easy to misread. Re-minted with an
RSA-2048 key, it negotiates on the first try and the module's own preference
wins (`DHE-RSA-AES256-SHA`, 256-bit — consistent with the 262-byte
`ClientKeyExchange` in the real session, which is a 2048-bit DH public value, not
an encrypted premaster).

**Gate 2 — a chain to a root the module trusts. Assembled, not yet proven.**

The minted leaf is signed by `EM OV TLS CA - G3C`, one of the eight compromised
eMudhra intermediates, which exists in two publicly served variants. Both are
here, in PEM, leaf-first ordering not required:

| Chain file | Path the module must build | Root the module must trust |
|---|---|---|
| `chain-aaa.pem` | leaf → G3C → AAA Certificate Services | Comodo/Sectigo **AAA**, RSA-2048, in stores since 2002 |
| `chain-emsign.pem` | leaf → G3C → emSign Root TLS CA G3 → emSign ECC Root CA G3 | eMudhra **ECC** root, created 2018 |

`chain-aaa.pem` is the default. What the module is *known* to trust comes from
Ayla's own chain, which it validates today:

```
leaf CN=*.aylanetworks.com
  ← Sectigo Legacy Server Authentication CA OV R30
  ← Entrust Root Certification Authority - G2   (self-signed, in the served chain)
```

So the module's trust store contains a **2009-era Entrust root** and still accepts
it, which is the profile of an old, broad bundle — the kind that ships AAA but
not necessarily a 2018 eMudhra root. There is no Entrust cross-sign of G3C in the
published artifacts, so AAA is the closest thing to that anchor available; the
emSign path is the second attempt, not the first.

Both chains verify against a current public trust store, and both complete a
handshake with the module's exact offer locally. Which one the module accepts is
a question for the module.

## The upstream leg needs its own CA

mitmproxy validates the server it forwards to, and its bundled store (certifi) has
dropped Entrust. Against a perfectly valid Ayla chain that produces:

```
Server TLS handshake failed. Certificate verify failed: self-signed certificate in
certificate chain
```

followed by `502 Bad Gateway` — which reads like the module being wrong when it is
mitmproxy's trust store that is. `run.sh` therefore passes
`ssl_verify_upstream_trusted_ca=mitm/upstream-entrust-g2.pem`, which keeps upstream
verification on instead of disabling it.

**Still open: can the module verify ECDSA at all?** The leaf's signature is
`ecdsa-with-SHA384` regardless of which chain is served — only the hop above it
becomes RSA-signed under the AAA cross-sign. A TLS stack that offers no ECC key
exchange may have been built without ECC verification, in which case this fails
with a correct chain and a trusted root and there is no certificate-side fix.

`check-cert.sh` tests what can be tested off-device:

```
$ ./mitm/check-cert.sh aylanetworks.com.crt aylanetworks.com.key mitm/chain-aaa.pem
  key:       rsaEncryption (2048 bit)
  ok    RSA leaf key — one of the module's suites can use it
  warn  leaf is ECDSA-signed; the module must be able to verify ECDSA
  ok    SAN covers aylanetworks.com
  ok    link 1: EM OV TLS CA - G3C signs the certificate below it
  note  the module must trust the issuer at the top: ... CN=AAA Certificate Services
  ok    handshake with the module's exact offer completed: DHE-RSA-AES256-SHA
VERDICT: negotiable with the module's offer.
```

## Why a reverse proxy

The module sends **no SNI**, and the DNAT that puts it in path happens on the
Pi, so the proxy host receives a plain TCP connection to its own address with no
original destination and no hostname to recover. Transparent mode has nothing to
key on; reverse mode supplies the upstream explicitly:

```
module ──TLS, no SNI──▶ Pi (ARP redirect + DNAT tcp/443) ──▶ :8443 mitmproxy
                                                                 │
                                                    TLS, real chain
                                                                 ▼
                                        proxy-ingress-usfield.aylanetworks.com:443
```

The client-facing side is pinned with `--set ciphers_client` to exactly the
module's four suites, so nothing can be negotiated that the device could not do,
and `tls_version_client_max=TLS1_2` holds it at the version the module speaks.

## Quick start

```bash
# 1. gate check (exit 0 means negotiable)
./mitm/check-cert.sh aylanetworks.com.crt aylanetworks.com.key mitm/chain-aaa.pem

# 2. proxy on the development host
./mitm/run.sh                       # CHAIN=mitm/chain-emsign.pem for the other path
                                    # UI=web for the browser timeline

# 3. prove it before touching the device: same hello, no SNI, same suites,
#    and validate the chain against the root the module is believed to have
./mitm/module-sim.py --port 8443 --path /notifications/get --trust aaa-root.pem

# 4. put the module in path from the Pi (self-restoring, bounded)
PROXY_HOST=<dev-host> PROXY_PORT=8443 sudo ./mitm/pi-redirect.sh
```

`mitm/flows.jsonl` gets one JSON object per exchange — method, path, both header
lists in wire order, both bodies, negotiated cipher, duration. `flows.mitm` is
the replayable stream for `mitmproxy --rfile`.

## Scripts

| Script | What it answers |
|---|---|
| `check-cert.sh LEAF KEY CHAIN` | can this certificate even negotiate? exits non-zero if not |
| `run.sh` | the proxy alone, for interactive work (`UI=web` for the timeline) |
| `window-watch.sh [min] [out]` | *will it dial, and what does the rejection look like?* arms the redirect, captures, stops at the first dial |
| `session.sh [min] [setpoints…]` | *what does the cloud channel carry?* arms, **flushes conntrack to force a re-dial**, optionally captures the ops channel (`OPS=1`) or serves frames we built (`INJECT="148 150"`) |
| `summarise.py FLOWS` | one line per request, then setpoint / water / mode / watts / volts / air / RSSI per telemetry post, and how often it polled |
| `ops-block.sh arm\|clear` | drop the encrypted ops channel, to measure what the module does without Ayla's doorbell |
| `frame.py [F]` / `frame.py --decode HEX` | encode or decode a block-write frame (the write path, below) |
| `tls-window.py FILE.pcap` | decode a capture into TLS records, both directions |
| `module-sim.py` | the module's own ClientHello, for testing a server without the device |
| `udm-redirect.sh`, `pi-redirect.sh`, `mac-redirect.sh` | put the module in path, on the gateway or the host |

`window-watch.sh` and `session.sh` both clean up after themselves whatever
happens — gateway rules removed, `tcpdump` stopped, setpoint restored — and both
print the rule count on the way out.

## Writes only land on a poll, and polls are rare

`setBlockParameter` is delivered as the *reply* to the module's own
`GET /devices/<id>/commands.xml`, so an injected write cannot be pushed — it waits
for the next poll, and the module decides when that is. Measured today, with the
redirect armed and the device healthy and posting every 30–60 s:

| Window | Telemetry posts | Command polls |
|---|---|---|
| 9 min, rule loaded | 6 | **1** |
| 6 min, rule loaded | 5 | **0** |

During the earlier eight-minute observation every connection polled, so something
about the device's state changes the cadence — possibly that it only polls when it
believes a command may be waiting. Until that is understood, treat a write as
*queued*, not *applied*, and confirm it from the telemetry that follows.

That is also why `each` exists: a rule can hold a *ladder* of responses and serve
them one per poll, so 149 → 150 → 148 → 150 all happen inside a single window
without restarting the proxy. With polls this sparse the ladder needs a longer
window than the values it steps through, which is itself the honest measurement.

## Arming is not enough: the module keeps its connection

DNAT only steers *new* connections. The module holds an established one to real
Ayla, so arming the redirect can leave a session with no traffic at all — measured:
six armed minutes with a cloud write produced **zero requests**. What fixes it is
dropping the module's conntrack entries, which makes its next packet look like a
new flow, get DNAT'd mid-stream and break the connection:

```bash
conntrack -D -s 10.0.1.187 -p tcp --dport 443     # twice, a few seconds apart
```

`session.sh` now does this itself, right after it confirms the rules armed, and
reports how many entries it flushed.

## What actually makes the module poll

A write can only be delivered in reply to the module's own `commands.xml` request,
so this is the whole question for a local stand-in for the cloud. Measured, one
variable at a time, each window 5–9 minutes:

| What was done | Polls seen |
|---|---|
| A cloud-side setpoint write through Ayla (the real one) | **2 of 2 writes polled within seconds** |
| The first dial after arming the redirect | 1–3 polls in the first 10 s |
| Nothing — module left alone, ops channel blocked | 1 poll in 7 min; 1 in 2 min; 1 in 9 min |
| Held the poll open for 240 s, answering late | module went **silent** for the next 5 min |
| Hung up after every telemetry post (`close: true`) | **no polls** in 181 s |
| Poll-mode request `commands.xml?polling=1` | arrives, but Ayla answers it in ~100 ms, so it is not a true long-poll |

**Nothing in-band prompts a poll sooner.** The module dials the ingress on a
**60-second cadence** of its own (two dials at 19:51:16 and 19:52:16, neither
within 15 s of a cloud-side write) and polls `commands.xml` on a slower,
irregular timer of minutes. An earlier draft of this file called the ops channel
"the doorbell" on the strength of a poll that turned out to *precede* its write by
four seconds; that was correlation read backwards, and it is wrong.

Holding the poll open does **not** buy a push channel — it starves the module of
its telemetry connection instead. Hanging up to force a reconnect does **not**
provoke a poll either: the polls seen at arm time come from the redirect changing
under the module, not from the hangup.

So a local stand-in can be instant for *reads* and is gated to the module's own
timer for *writes* — minutes, not milliseconds. The only fast write paths are BLE
and CTA-2045, both already local.

## The ops channel, decoded: a rolling nonce, not a command path

Captured on the gateway — `tcpdump -i br0` is the interface that sees the module's
VLAN; `switch0` and `eth9` see nothing of it, which cost an empty capture to learn.

```
04 00 00 00 3f b8 0f 00 | <16 bytes> | <16 bytes>          40 bytes, fixed
```

Six packets in two minutes, all exactly 40 bytes, all to `34.70.101.46:55055`
(`ans-field.aylanetworks.com` rotates; the DNS answer at capture time was a
different host). Header `0x000fb83f` = 1030207 constant throughout. The two 16-byte
blocks chain **per direction**: each side sends back its own previous second block
as its next first block, with a fresh random block appended.

So it is **neither signed nor encrypted**, and there is nowhere for a command to
hide: 40 bytes of fixed size leaves no room for both a nonce and a MAC, let alone a
payload. No packet at all appeared within 15 s of a cloud-side write. It is a
liveness/rolling-token keepalive — decoding it further buys nothing, and it is not
the route to instant writes.

## The write path

The cloud's `setBlockParameter` property carries a block write in the module's own
framing, and it reproduces exactly:

```
BD  <cmd=0x40>  <total_len>  <block>  <offset>  <value...>  <crc8>
```

`total_len` counts the whole frame including the CRC; the CRC is the table
`ble/icomm_ble.py` already holds. The setpoint is block `0x0B`, offset `0x06`,
big-endian **1/256 °C**:

```bash
./mitm/frame.py 122        # BD 40 08 0B 06 32 00 56  — the byte string Ayla sends
./mitm/frame.py --decode 'BD 40 08 0B 06 30 E4 5E'    # 120.00 F, crc_ok True
```

Answering `commands.xml` with one of these makes the module apply it — no Ayla
issuance involved. `mitm/rules-set125.json` is a worked example; it set the tank
to 125 °F, which Ayla never sent, and the device's next three telemetry posts
reported `0x33AB` = 125.00 °F:

```bash
RULES=mitm/rules-set125.json ./mitm/session.sh 5 121
```

## Live result, 22 October 2026: the module rejects our certificate

In path at last, on the second attempt and not via ARP — see `udm-redirect.sh`
below. The module dialled 11 times in three minutes. Each time:

```
S     module -> proxy            SYN, mss 1152, win 4608 (small embedded stack)
S.    proxy  -> module
P     module -> proxy            82 bytes   ClientHello, no SNI, 4 suites
      proxy  -> module           3147 bytes  ServerHello + Certificate (our leaf + chain)
.     module -> proxy            ACK, twice
                                  24.9 s of silence
R     module -> proxy            RST      no ClientKeyExchange, no alert, no data
```

That is the Phase 4/5 behaviour reproduced with a certificate that negotiates,
terminates, and is chain-complete — and it happened identically for **both**
chains:

| Chain served | Root the module would need | Result |
|---|---|---|
| `chain-aaa.pem` | AAA Certificate Services (2002) | ACK cert, 24.9 s, RST, retry |
| `chain-emsign.pem` | emSign ECC Root CA G3 (2018) | ACK cert, silent, RST, retry |

So the certificate is rejected on verification, not on negotiation. Two causes
remain, and they are not separable without a certificate we cannot mint:

1. **The trust store holds neither root.** Plausible for the 2018 eMudhra root;
   less so for AAA, which is in nearly every bundle.
2. **The stack cannot verify ECDSA.** The leaf's signature is
   `ecdsa-with-SHA384` under either chain — AAA only makes the hop *above* it
   RSA-signed. A stack offering no ECC key exchange may well have been built
   without ECC verification, in which case no eMudhra-signed leaf of any kind
   will ever work.

What the module demonstrably *does* trust, because it validates it today:

```
leaf CN=*.aylanetworks.com
  <- Sectigo Legacy Server Authentication CA OV R30
  <- Entrust Root Certification Authority - G2      (2009, self-signed in the chain)
```

**The way forward is a certificate on that chain, not another eMudhra
cross-sign.** An RSA-2048 leaf issued under the Entrust-G2-rooted Sectigo
hierarchy fixes both hypotheses at once: a root it is known to trust, and a
signature chain that needs no ECC. Only the owner of `aylanetworks.com` can
issue one.

Operationally: while the redirect is armed the module's cloud channel is down by
design (it retries every ~25-30 s into a wall). Disarm and it re-establishes
within seconds — verified with `conntrack -L -s 10.0.1.187 -p tcp` showing
`ESTABLISHED ... dport=443` to the real ingress.

## mitmproxy's `--emudhra` flag does not help this device

mitmproxy **12.3** (not the 12.2.3 Homebrew build) ships `--emudhra` and
`--emudhra-old`, which sign with the recovered `EM OV TLS CA - G3C` key — the
key scalar is literally `d=4`. The chain it presents is the richest one
available, including both variants of the intermediate:

```
leaf  CN=<host>
  <- EM OV TLS CA - G3C                        (emSign-issued)
  <- emSign Root TLS CA - G3                   (cross-signed by emSign ECC Root CA - G3)
  <- EM OV TLS CA - G3C                        (AAA cross-sign, for pre-2021 stores)
```

`--emudhra-old` moves the AAA cross-sign to the front instead.

It cannot serve this module, and the reason is in `certs.py`: `get_cert()` builds
the leaf with `dummy_cert(self.default_privatekey, ...)` and hands back
`privatekey=self.default_privatekey` — so the leaf **carries the CA's own key**,
which is **EC P-384**:

```
leaf CN=proxy-ingress-usfield.aylanetworks.com
  sig = ecdsa-with-SHA256        key = id-ecPublicKey (384 bit)      SAN = none
```

An ECDSA-keyed leaf cannot be used with any of the module's four suites, so the
handshake now fails **earlier** than with the hand-minted certificate — a client
offering only legacy CBC suites gets EOF straight after its ClientHello (only
modern clients, which this device is not, complete). The hand-minted leaf is RSA,
so it negotiates and reaches verification, where the module ACKs the certificate,
waits 24.9 s and resets.

So the flag confirms where the wall is rather than moving it: **signing was never
the problem, verification is.** Patching the leaf to an RSA key would reproduce
exactly the hand-minted case, which both available roots already fail. `run.sh`
supports `EMUDHRA=1` / `EMUDHRA=old` for completeness and for any other device.

## Redirecting it on the gateway (the method that works)

`mitm/udm-redirect.sh` installs and removes a DNAT/SNAT pair on the UDM Pro:

```
iptables -t nat -I PREROUTING  1 -s 10.0.1.187 -p tcp --dport 443 -j DNAT --to-destination 10.0.1.195:8444
iptables -t nat -I POSTROUTING 1 -s 10.0.1.187 -d 10.0.1.195 -p tcp --dport 8444 -j SNAT --to-source 10.0.1.1
```

Both are needed: the proxy sits on the same LAN, so without the SNAT it would
answer the module directly, the gateway would never see the return path to undo
the translation, and the module would get a SYN-ACK from the wrong address.

The module's TLS channel is **event-driven, not polled** — it had no TCP/443 flow
at all for minutes at a time. A cloud-side change makes it dial at once; a
`water_heater.set_temperature` through Home Assistant worked in ~20 s.

## The ARP path, for the record

Everything above the link layer is ready and tested. The gap is that the module's
TLS packets do not transit the Pi, so DNAT never fires and the proxy never sees a
ClientHello — 0 packets on the listen port, DNAT counter 0, and no frames from
the module's MAC in four minutes of watching, while the cloud's datagrams *to*
the module were forwarded normally.

`icomm/arpspoof.py` sends only unsolicited replies (`op=2`), and on this retry
that did not displace the module's gateway entry. Adding gratuitous *requests*
(`op=1`) made it worse: 168 requests in 90 s, each answered within milliseconds and
each ignored, i.e. the cache was being invalidated rather than filled, and the
module's cloud link broke until the spoofer was stopped.

Two ways around it, neither of which needs ARP:

1. **Redirect on the gateway** — a policy route or port-forward from
   `10.0.1.187:443` to the proxy host. Deterministic, and it survives the module's
   cache behaviour.
2. **Move the endpoint instead of the traffic** — with Ayla-side control, point
   the module at a development ingress and skip interception entirely.

Read the result below the same way once one of those is in place.

## Reading the result

One HTTPS connection from the module is the whole experiment, and the pcap
answers it better than the proxy log does:

| What the module does | Meaning |
|---|---|
| sends `ClientKeyExchange`, then application data | chain accepted; flows are in `flows.jsonl` |
| ACKs the certificate, ~24 s silence, RST | chain rejected — wrong root, or no ECDSA verification |
| RST before any certificate is read | cipher/key problem, which Gate 1 says should not happen |

## Fault injection

For developing against the module rather than watching it, hand the addon a
rules file and the first matching rule answers instead of the upstream:

```json
[{"match": {"method": "POST", "path_regex": "/notifications"},
  "action": {"status": 500, "body": "{\"error\":\"injected\"}"}}]
```

```bash
RULES=mitm/rules.json ./mitm/run.sh
```

## What is verified, and how

| Check | Result |
|---|---|
| ECDSA leaf vs the module's 4-suite offer | **no shared cipher** (the first mint) |
| RSA-2048 leaf, same SAN, same chain | negotiates `DHE-RSA-AES256-SHA` |
| Leaf → G3C → AAA against a store with only AAA | verifies, `openssl verify` and through the proxy |
| Leaf → G3C → emSign ECC root, store with only that root | verifies, `openssl verify` |
| Proxy terminates a no-SNI, 4-suite, TLS1.2 client | works, flow logged in clear |
| Response injection by method + path regex | works, non-matching requests pass through |
| Module accepts the chain | **untested** — one ARP window away || Ops channel impact | none; UDP/55055 is not touched by any of this |

## Provenance of the chains

`chains.zip` from <https://emudhra.we-have-a-problem.com> holds the full chains
the twelve test hosts served on 20 October 2026, including the AAA and emSign
cross-signs of each impacted intermediate. `chain-aaa.pem` is its `ov-g3c-aaa`
chain's intermediate; `chain-emsign.pem` is the two intermediates from
`ov-g3c`. Only public certificates are committed here — the recovered
intermediate key is not, and must not be.

## Notes

- `pi-redirect.sh` matches on destination **port 443**, not a resolved Ayla IP,
  because the ingress pool rotates mid-session (Phase 2) and a DNAT bound to one
  address silently stops matching. The module has no other TCP/443 peer.
- It applies rules in its own `ICOMM_MITM_*` chains and removes them on exit,
  rather than flushing `nat` wholesale.
- `arpspoof.py` must be stopped with SIGTERM so its ARP restore runs; the script
  kills it by PID for the reason recorded in `icomm/README.md`.
- mitmproxy's `connection_strategy=eager` dials the upstream before the client
  handshake. If the upstream is unreachable the client just sees a closed
  connection — check the proxy log before blaming the certificate.
- Serving a *self-signed* stand-in is not a fallback: the module rejected
  mitmproxy's own CA in Phase 4, and would reject any such leaf identically.

## Layout

```
mitm/check-cert.sh     is this certificate servable to the module at all?
mitm/run.sh            mitmproxy in reverse mode, ciphers pinned to the module's offer
mitm/ayla-mitm.py      flow log (JSONL + console) and response injection rules
mitm/module-sim.py     the module's ClientHello without the module
mitm/pi-redirect.sh    Pi-side ARP redirect + DNAT/SNAT, self-restoring
mitm/chain-aaa.pem     G3C cross-signed by AAA (default chain)
mitm/chain-emsign.pem  G3C + emSign Root TLS CA G3 (alternative chain)
mitm/upstream-entrust-g2.pem  the root Ayla's real chain ends at, for the upstream leg
```
