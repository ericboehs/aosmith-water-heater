# iCOMM cloud and LAN channels — what the module actually talks to

The iCOMM module has two cloud channels and one LAN channel. All of them were
put under a man-in-the-middle harness from the tank Pi. The result, in one line:
**the TLS channel validates certificates and rejects a locally-minted CA, and the
ops channel is a custom encrypted UDP protocol on port 55055 that has no TCP
fallback.** Neither is a route to local control.

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

**Verdict: certificate chain validation is enforced.** The module does not
complete a handshake with a CA it does not trust, and it does not send a TLS
`bad_certificate` alert — it simply resets after about 24 seconds.

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

What that correction does not settle is the part only the module can answer:
the leaf's own signature is `ecdsa-with-SHA384`, so the module must be able to
verify **one** ECDSA signature even under the AAA cross-sign. A stack that
offers no ECC key exchange may have been built without ECC verification, in
which case a correct chain to a trusted root still fails and there is no
certificate-side fix.

## Artifacts

Captures on the tank Pi, `~` = `/home/ericboehs`:

| File | Size | Contents |
|---|---|---|
| `icomm_phase1_final.pcap` | 133 KB | architecture discovery, both channels, both directions |
| `icomm_phase2_fallback.pcap` | 253 KB | UDP blackout: retry acceleration, DNS endpoint rotation, recovery |
| `icomm_phase3_dns.pcap` | 427 KB | DNS interception attempt (race lost), full TLS handshake + server chain |
| `icomm_phase4_mitm.pcap` | 329 KB | mitmproxy served its own CA — module resets |
| `icomm_phase5_pinning.pcap` | 63 KB | correct-name cert, same rejection |

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
