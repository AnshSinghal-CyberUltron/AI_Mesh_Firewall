"""F1a-3 plan revision 3, §4.2 (answers A-S5): decode RFC 6052 NAT64 prefixes of every legal length and
re-check the embedded IPv4, for the well-known prefix and for operator-declared network-specific prefixes.

Reviewer A's probe (review 5871181186, test_nat64_network_specific_prefix_passes_the_address_policy) showed
that a network-specific prefix (NSP) synthesised by DNS64 on an IPv6-only network embeds a private IPv4 in
a global-unicast address that the revision-2 policy passes. This prototype is the revision-3 rule:
AMF_NAT64_PREFIXES declares the NSPs; every declared prefix (and 64:ff9b::/96, 64:ff9b:1::/48) is decoded
per RFC 6052 §2.2 (bits 64-71 are the reserved u-octet and are skipped) and the IPv4 is re-checked.

  python -I nat64_nsp_decode.py        (prints the table; exit 0 when every expectation holds)
"""
from __future__ import annotations

import ipaddress
import json
import sys

WKP = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def embed(prefix: ipaddress.IPv6Network, v4: str) -> ipaddress.IPv6Address:
    """RFC 6052 §2.2 synthesis (the inverse of decode), used to build the test rows."""
    p, a = prefix.prefixlen, int(ipaddress.IPv4Address(v4)).to_bytes(4, "big")
    b = bytearray(prefix.network_address.packed)
    pos = p // 8
    for byte in a:
        if pos == 8:  # skip the u-octet
            pos = 9
        b[pos] = byte
        pos += 1
    return ipaddress.IPv6Address(bytes(b))


def decode(addr: ipaddress.IPv6Address, prefix: ipaddress.IPv6Network) -> ipaddress.IPv4Address:
    raw, pos, out = addr.packed, prefix.prefixlen // 8, bytearray()
    while len(out) < 4:
        if pos == 8:
            pos = 9
        out.append(raw[pos])
        pos += 1
    return ipaddress.IPv4Address(bytes(out))


def refused_v4(v4: ipaddress.IPv4Address) -> bool:
    return not v4.is_global or v4.is_private or v4.is_loopback or v4.is_link_local or v4.is_reserved \
        or v4.is_multicast or v4.is_unspecified or v4 in ipaddress.ip_network("100.64.0.0/10")


def policy_refuses(addr: str, declared: tuple[ipaddress.IPv6Network, ...]) -> bool:
    ip = ipaddress.ip_address(addr)
    if ip.version == 4:
        return refused_v4(ip)
    if not ip.is_global:
        return True
    for net in WKP + declared:
        if ip in net:
            return refused_v4(decode(ip, net))
    return False


def main() -> int:
    rows, ok = [], True
    nsps = [ipaddress.ip_network(n) for n in ("2600:1f14:abc:64::/96", "2600:1f14:a00::/40", "2600:1f14::/32",
                                              "2600:1f14:abc::/48", "2600:1f14:abc:6400::/56",
                                              "2600:1f14:abc:64::/64")]
    for nsp in nsps:
        for v4 in ("10.0.0.5", "169.254.169.254", "127.0.0.1", "100.64.0.1", "93.184.216.34"):
            synth = embed(nsp, v4)
            assert decode(synth, nsp) == ipaddress.IPv4Address(v4)
            undeclared = policy_refuses(str(synth), ())
            declared = policy_refuses(str(synth), (nsp,))
            expect = refused_v4(ipaddress.IPv4Address(v4))
            good = declared == expect
            ok &= good
            rows.append({"prefix": str(nsp), "ipv4": v4, "synthesised": str(synth),
                         "is_global": ipaddress.ip_address(str(synth)).is_global,
                         "refused_if_undeclared": undeclared, "refused_if_declared": declared,
                         "expected": expect, "ok": good})
    for v4 in ("10.0.0.5", "169.254.169.254"):  # the well-known prefix needs no declaration
        synth = embed(WKP[0], v4)
        good = policy_refuses(str(synth), ())
        ok &= good
        rows.append({"prefix": str(WKP[0]), "ipv4": v4, "synthesised": str(synth), "refused_if_undeclared": good,
                     "ok": good})
    for r in rows:
        print(json.dumps(r))
    print(json.dumps({"all_expectations_hold": ok}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
