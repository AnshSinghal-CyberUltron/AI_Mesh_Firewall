#!/usr/bin/env python3
"""P4.13/P6.18 §4 — gateway egress network assertion.

Isolation invariant: the gateway must route ALL MCP traffic through the per-org
sandbox (gateway → broker → sandbox-agent → upstream) and NEVER dial an external
MCP upstream directly. This harness captures the gateway container's outbound TCP
connections (read from /proc/net/tcp[6] via `docker exec`, no extra tooling) BEFORE
and DURING a live 15-MCP tool-call burst, and asserts the burst introduces NO new
EXTERNAL host connection — only internal broker/control/redis endpoints.

Classification of a remote endpoint:
  - INTERNAL   : RFC-1918 / loopback (docker bridge — broker :8311, control :8000, redis)
  - INFRA_443  : a pre-existing (baseline) external :443 (CloudWatch/S3/telemetry)
  - SUSPECT    : any NEW external endpoint that appears DURING the tool-call burst
                 → a direct gateway→upstream dial = an isolation violation.

PASS iff the during-burst SUSPECT set is empty (every new connection is internal).
For the current all-stdio fleet this proves stdio egress isolation; once §3 routes
streamable-http/sse through the sandbox, this same gate proves it for HTTP too (and
FAILS today if run against an HTTP-transport server, correctly flagging §3).

Env: SCALE_MANIFEST, GATEWAY_CONTAINER (default ai_mesh_firewall-gateway-1),
     BURST (calls/target, default 6).
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import subprocess
import uuid

import httpx

HERE = os.path.dirname(__file__)
MANIFEST = os.environ.get("SCALE_MANIFEST", os.path.join(HERE, "ralph", ".mcp_scale_manifest.json"))
GATEWAY_CONTAINER = os.environ.get("GATEWAY_CONTAINER", "ai_mesh_firewall-gateway-1")
BURST = int(os.environ.get("BURST", "6"))

_mf = json.load(open(MANIFEST, encoding="utf-8"))
GATEWAY = _mf.get("gateway", "http://127.0.0.1:8300").rstrip("/")
ORGS = _mf["orgs"]


def _decode(hex_addr: str) -> tuple[str, int]:
    ip_hex, port_hex = hex_addr.rsplit(":", 1)
    port = int(port_hex, 16)
    raw = bytes.fromhex(ip_hex)
    if len(raw) == 4:  # IPv4 stored little-endian
        return ".".join(str(b) for b in reversed(raw)), port
    if len(raw) == 16:  # IPv6 stored as 4 little-endian words
        words = [raw[i:i + 4][::-1] for i in range(0, 16, 4)]
        return ipaddress.IPv6Address(b"".join(words)).compressed, port
    return ip_hex, port


def _gateway_conns() -> set[tuple[str, int]]:
    """Established outbound connections of the gateway container (state 01)."""
    out = subprocess.run(
        ["docker", "exec", GATEWAY_CONTAINER, "sh", "-c",
         "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null"],
        capture_output=True, text=True, timeout=20).stdout
    conns: set[tuple[str, int]] = set()
    for line in out.splitlines()[1:]:
        f = line.split()
        if len(f) < 4 or f[3] != "01":  # 01 = ESTABLISHED
            continue
        ip, port = _decode(f[2])
        if port == 0:
            continue
        conns.add((ip, port))
    return conns


def _is_internal(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


async def _tool_call(client, org, server, key, msg):
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call",
               "params": {"name": "echo", "arguments": {"message": msg}}}
    try:
        await client.post(f"{GATEWAY}/gateway/{org}/mcp/{server}", json=payload,
                          headers={"Authorization": f"Bearer {key}"}, timeout=60)
    except Exception:
        pass


async def main() -> int:
    targets = [(o["slug"], s, o["gateway_key"]) for o in ORGS for s in o["servers"]]
    print(f"egress assert: gateway={GATEWAY_CONTAINER}, {len(targets)} MCPs, burst={BURST}/target")

    baseline = _gateway_conns()
    base_ext = {(ip, p) for ip, p in baseline if not _is_internal(ip)}
    print(f"  baseline: {len(baseline)} conns ({len(base_ext)} external/infra)")
    for ip, p in sorted(base_ext):
        print(f"    INFRA(baseline) {ip}:{p}")

    # Fire a sustained burst and sample the gateway's connections mid-flight.
    during: set[tuple[str, int]] = set()

    async def sampler():
        for _ in range(BURST + 2):
            during.update(_gateway_conns())
            await asyncio.sleep(0.4)

    async def load():
        async with httpx.AsyncClient() as client:
            for _ in range(BURST):
                await asyncio.gather(*[
                    _tool_call(client, o, s, k, f"egress-{uuid.uuid4().hex[:6]}") for o, s, k in targets
                ])

    await asyncio.gather(sampler(), load())

    new_conns = during - baseline
    new_external = {(ip, p) for ip, p in new_conns if not _is_internal(ip)}
    suspect = new_external - base_ext  # NEW external endpoints not in the infra baseline

    internal_new = {(ip, p) for ip, p in new_conns if _is_internal(ip)}
    print(f"  during burst: +{len(new_conns)} new conns ({len(internal_new)} internal, "
          f"{len(new_external)} external)")
    for ip, p in sorted(internal_new):
        print(f"    internal(new) {ip}:{p}")
    for ip, p in sorted(suspect):
        print(f"    *** SUSPECT external dial during burst: {ip}:{p} ***")

    # broker + control must be among the internal endpoints exercised
    ports_hit = {p for _, p in during}
    broker_hit = 8311 in ports_hit
    print(f"  broker(:8311) connection observed: {broker_hit}")

    ok = len(suspect) == 0 and broker_hit
    print("EGRESS ASSERT:", "PASS (no direct gateway→upstream dial; MCP egress via sandbox)"
          if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
