# CHG-0073 — internal IPv6 + cloud-metadata/CGNAT IPv4 egressed RAW in tool results

**Change-id:** CHG-0073
**Date:** 2026-07-02
**Severity:** MEDIUM (infrastructure-topology / cloud-env disclosure; fail-open under a redact policy)
**Area:** HARDEN 1.4 — field-level redaction of tool RESULTS + PII/IP/regulated compliance tagging
**File:** `gateway/ai_mesh_gateway/patterns.py` (+ `tests/test_internal_ip_leakage_v6_metadata.py`)
**Whose work it touches:** the shared detection/redaction core (owning-session file); same surface as
CHG-0071/0072 (provider-secret sweeps). No behavioural change to any other module.

## Gap (a third adversarial `redact_all` sweep — this time the IP-leakage surface)

`IP_LEAKAGE_PATTERNS` was **IPv4-RFC1918-only**. Empirically probed via the gateway venv — all of the
following egressed **completely unmasked** (`redact_all` no-op, `detect_ip_leakage` empty):

| input | why it's a leak |
|---|---|
| `fd12:…:0001`, `fc00::1234:5678` | ULA IPv6 (fc00::/7) — internal topology |
| `fe80::1ff:fe23:4567:890a` | link-local IPv6 (fe80::/10) — interface disclosure |
| `169.254.169.254` | **cloud IMDS endpoint** — hands out IAM creds; the exact SSRF target the dial-time guards (CHG-0065/0067) block, yet it egressed raw in a RESULT |
| `100.64.12.34`, `100.127.255.1` | CGNAT (100.64.0.0/10) — internal edge topology |

Control `10.0.5.7` masked correctly (RFC1918 already covered).

### The subtle fail-open this closes

`_redact_all_raw` masks infra via a **hardcoded key tuple** `("internal_ipv4","internal_hostname",
"internal_url")` — NOT by iterating `IP_LEAKAGE_PATTERNS`. So naively adding a key to the dict makes
`detect_ip_leakage` **flag** the leak (tier-1 decides block/redact) while `redact_all` leaves it **raw** →
under a *redact* policy the gateway would report "redacted" while forwarding the raw address: the cardinal
"never report redact while forwarding raw" violation. The fix therefore touches **four** integration
points, not one.

## Fix (all four points, so detect == redact == tag == encoded-parity)

1. `IP_LEAKAGE_PATTERNS`: `+ internal_ipv6`, `+ link_local_ipv4` (→ `detect_ip_leakage` auto-picks them
   up for the enforcement DECISION).
2. `_redact_all_raw` infra tuple: `+ internal_ipv6, link_local_ipv4` (→ `redact_all` ACTUALLY masks them;
   closes the divergence above).
3. `_INFRA_NETWORK_KEYS`: `+ internal_ipv6, link_local_ipv4` (→ the encoded/base64-obfuscated-infra
   bypass pass covers them too).
4. `COMPLIANCE_TAG_MAP`: both `→ ["INFRA"]`.

### Pattern design (linear-time, low-FP)
- `internal_ipv6`: anchored on the distinctive internal FIRST HEXTET (`f[cd]hh` ULA / `fe[89ab]h`
  link-local) — a full 4-hex hextet, so a 2-hex-group MAC, a bare contiguous hex blob, and an `HH:MM:SS`
  timestamp never match — then `>=1` `:`/`::` hextet group or a bare `::`. Loopback `::1` **intentionally
  NOT flagged** (as benign as localhost).
- `link_local_ipv4`: `169.254.0.0/16` (incl. IMDS) + `100.64.0.0/10` (2nd octet 64–127 via
  `6[4-9]|[7-9]\d|1[01]\d|12[0-7]`). No `127/8` loopback (FP-prone in dev output, low value).
- All quantifiers fixed/bounded, every repeat consumes `>=1` char ⇒ no ReDoS.

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_internal_ip_leakage_v6_metadata.py -q   # 21 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                            # 1305 passed, 0 failed
```

Byte-level truth (authoritative): for every case the raw address is **absent** from `redact_all(text)`
(e.g. `http://[LINK_LOCAL_IPV4_REDACTED]/latest/meta-data/iam/…`). FP battery (MAC / hex blob / timestamp
/ `fc00:` / version `100.5.2.1` / `169.1.254.9` / UUID / bare `fd00`) all unchanged. Linear-time smoke on
10k-char pathological inputs < 80 ms.

**Independent oracle (aidefence_scan):** reports `piiFound:false` on `http://169.254.169.254/…metadata…`
+ ULA IPv6 — a representative generic PII scanner is **blind** to internal-IP/IPv6/metadata leakage,
confirming (a) the gap is real and (b) the purpose-built `detect_ip_leakage` catches what a generic
scanner cannot. The gateway must not rely on a generic scanner for infra-leak prevention.

## Residual / follow-ups
- Loopback (`127.0.0.0/8`, `::1`) deliberately unflagged (documented trade-off).
- Public IPv6 not flagged (only INTERNAL ranges — consistent with `internal_ipv4` being RFC1918-only).
