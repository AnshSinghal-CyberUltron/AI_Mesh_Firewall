"""Probe the SHIPPED SSRF check (`dispatch/url_guard.py`) and the shipped upstream decoder.

Prototype evidence for the F1a-3 plan (not product code). Prints, for each address, whether
`resolve_checked` approves it today with an empty allowlist (the default), next to Python's own
`is_global`, and the decoded embedded IPv4 for the transition prefixes the design re-checks.
Then feeds the shipped `SSEDecoder` the two framings providers use that it does not accept today.

Run with the production interpreter (3.12):  python ssrf_probe.py
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "gateway_v2"))

from gateway_v2.dispatch.url_guard import Refusal, resolve_checked  # noqa: E402
from gateway_v2.edge.wire.sse_decode import SSEDecoder  # noqa: E402

ADDRESSES = (
    "8.8.8.8",  # ordinary public
    "127.0.0.1",
    "10.0.0.5",
    "169.254.169.254",  # cloud metadata
    "100.64.0.1",  # RFC 6598 shared address space (carrier NAT)
    "192.0.0.10",  # IETF protocol assignments
    "198.18.0.1",  # benchmarking
    "192.88.99.1",  # 6to4 relay anycast (deprecated)
    "233.252.0.1",  # multicast (MCAST-TEST-NET)
    "::ffff:127.0.0.1",  # IPv4-mapped
    "64:ff9b::a9fe:a9fe",  # NAT64 well-known prefix embedding 169.254.169.254
    "64:ff9b::7f00:1",  # NAT64 embedding 127.0.0.1
    "64:ff9b:1::a00:5",  # local-use NAT64 embedding 10.0.0.5
    "2002:7f00:1::1",  # 6to4 embedding 127.0.0.1
    "2002:a9fe:a9fe::1",  # 6to4 embedding 169.254.169.254
    "2001:0:4136:e378:8000:63bf:80ff:fefe",  # Teredo (client 127.0.0.1 obfuscated)
    "::7f00:1",  # IPv4-compatible (deprecated) embedding 127.0.0.1
    "fd00:ec2::254",  # AWS IMDS IPv6
    "fe80::1",
)


def embedded_v4(ip: ipaddress.IPv6Address) -> str | None:
    packed = ip.packed
    if ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network("::/96"):
        return str(ipaddress.IPv4Address(packed[12:]))
    if ip in ipaddress.ip_network("64:ff9b:1::/48"):
        return str(ipaddress.IPv4Address(packed[12:]))  # RFC 8215: /96 position used here
    if ip.sixtofour is not None:
        return str(ip.sixtofour)
    if ip.teredo is not None:
        return str(ip.teredo[1])
    if ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return None


async def no_dns(host: str, port: int | None) -> tuple[str, ...]:
    raise OSError("no DNS in the probe")


async def main() -> None:
    rows = []
    for address in ADDRESSES:
        ip = ipaddress.ip_address(address)
        host = f"[{address}]" if ip.version == 6 else address
        verdict = await resolve_checked(f"https://{host}/v1", resolver=no_dns)
        rows.append(
            {
                "address": address,
                "shipped_url_guard": "refused" if isinstance(verdict, Refusal) else "ALLOWED",
                "is_global": ip.is_global,
                "embedded_ipv4": embedded_v4(ip) if ip.version == 6 else None,
            }
        )
    decoder_rows = []
    event = b'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}'
    for name, stream in (
        ("lf_framing", event + b"\n\ndata: [DONE]\n\n"),
        ("crlf_framing", event + b"\r\n\r\ndata: [DONE]\r\n\r\n"),
        ("comment_keepalive", b": ping\n\n" + event + b"\n\ndata: [DONE]\n\n"),
    ):
        decoder = SSEDecoder()
        frames = decoder.feed(stream)
        decoder_rows.append(
            {
                "input": name,
                "frames": len(frames),
                "malformed": decoder.malformed(),
                "finished": decoder.finished(),
            }
        )
    print(
        json.dumps(
            {"python": sys.version.split()[0], "url_guard": rows, "sse_decoder": decoder_rows},
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
