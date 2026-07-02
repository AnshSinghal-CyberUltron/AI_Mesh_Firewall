#!/usr/bin/env python3
"""P4.13 / P6.18 — four-transport sandbox verification + network assertion prep.

Runs a **wiring gate** first (broker unified route + gateway ``broker_send_rpc``).
If the gate fails the harness exits **BLOCKED** — do not mark P4.13/P6.18 ``[x]``.

When wiring is landed (Claude checklist §1–3), the same script exercises stdio,
streamable-http, sse, and websocket through the **gateway** (never direct upstream)
and documents how to prove the gateway opens no external MCP connections.

Env:
  GATEWAY_URL          default http://127.0.0.1:8300
  BROKER_URL           default http://127.0.0.1:8311
  BROKER_ORG           org slug for broker probes (default zeroshield)
  TRANSPORT_MANIFEST   JSON: org slug, gateway key, per-transport server slugs
                       (see mcp-parallel/findings/p4-13/TRANSPORT_MANIFEST.example.json)
  SCALE_MANIFEST       fallback: stdio-only from mcp_scale_provision (partial check)
  ROUNDS               consecutive all-green rounds (default 1)
  SKIP_NETWORK_DOC     1 to skip printing network-assertion instructions
  FORCE_E2E            1 to run stdio e2e even when wiring gate fails (debug only)

Exit codes:
  0  SANDBOX_TRANSPORT: PASS (wiring + all transports green)
  1  SANDBOX_TRANSPORT: FAIL (wiring ok but transport call failed)
  2  SANDBOX_TRANSPORT: BLOCKED (gateway/broker wiring not landed)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
BROKER_URL = os.environ.get("BROKER_URL", "http://127.0.0.1:8311").rstrip("/")
BROKER_ORG = os.environ.get("BROKER_ORG", "zeroshield")
ROUNDS = int(os.environ.get("ROUNDS", "1"))
TIMEOUT = float(os.environ.get("HARNESS_TIMEOUT", "90"))
FORCE_E2E = os.environ.get("FORCE_E2E", "0") == "1"
SKIP_NETWORK_DOC = os.environ.get("SKIP_NETWORK_DOC", "0") == "1"
TRANSPORT_MANIFEST = os.environ.get(
    "TRANSPORT_MANIFEST",
    str(REPO / "mcp-parallel" / "findings" / "p4-13" / "TRANSPORT_MANIFEST.example.json"),
)
SCALE_MANIFEST = os.environ.get(
    "SCALE_MANIFEST",
    str(HERE / "ralph" / ".mcp_scale_manifest.json"),
)
FINDINGS = REPO / "mcp-parallel" / "findings" / "p4-13"
REPORT_PATH = os.environ.get(
    "REPORT_PATH",
    str(FINDINGS / "sandbox_transport_verify_report.json"),
)

ALL_TRANSPORTS = ("stdio", "streamable-http", "sse", "websocket")
GATEWAY_CLIENT = REPO / "gateway" / "ai_mesh_gateway" / "mcp_sandbox_client.py"
MCP_PROXY = REPO / "gateway" / "ai_mesh_gateway" / "mcp_proxy.py"
BROKER_ROUTES = REPO / "services" / "mcp-broker" / "src" / "sandbox" / "routes.py"


def _count_proxy_httpx_sites(proxy_src: str) -> int:
    return proxy_src.count("httpx.AsyncClient")


@dataclass
class WiringCheck:
    broker_send_rpc_present: bool = False
    broker_unified_rpc_route: bool = False
    broker_stdio_rpc_exists: bool = False
    broker_unified_rpc_http: int = 0
    broker_stdio_rpc_http: int = 0
    mcp_proxy_direct_httpx: bool = True
    mcp_proxy_httpx_sites: int = 0
    mcp_proxy_broker_send_rpc_refs: int = 0

    @property
    def landed(self) -> bool:
        return (
            self.broker_send_rpc_present
            and self.broker_unified_rpc_route
            and self.broker_stdio_rpc_exists
            and self.broker_unified_rpc_http != 404
            and not self.mcp_proxy_direct_httpx
        )


@dataclass
class TransportCall:
    transport: str
    server: str
    method: str
    ok: bool
    http_status: int
    detail: str = ""
    latency_ms: float = 0.0


@dataclass
class RoundReport:
    round: int
    wiring: WiringCheck
    calls: list[TransportCall] = field(default_factory=list)

    def failures(self) -> list[TransportCall]:
        return [c for c in self.calls if not c.ok]


def _http_probe(url: str, payload: dict | None = None) -> int:
    data = json.dumps(payload or {}).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(url, method="POST", data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def check_wiring() -> WiringCheck:
    client_src = _read_text(GATEWAY_CLIENT)
    routes_src = _read_text(BROKER_ROUTES)
    proxy_src = _read_text(MCP_PROXY)

    broker_refs = proxy_src.count("broker_send_rpc")
    httpx_sites = _count_proxy_httpx_sites(proxy_src)
    wc = WiringCheck(
        broker_send_rpc_present="async def broker_send_rpc" in client_src
        or "def broker_send_rpc" in client_src,
        broker_unified_rpc_route='"/{org_slug}/rpc"' in routes_src
        or "@router.post(\"/{org_slug}/rpc\")" in routes_src,
        broker_stdio_rpc_exists="/stdio/rpc" in routes_src,
        mcp_proxy_direct_httpx=(
            httpx_sites > 0
            and "server_config" in proxy_src
            and broker_refs == 0
        ),
        mcp_proxy_httpx_sites=httpx_sites,
        mcp_proxy_broker_send_rpc_refs=broker_refs,
    )
    probe_body = {
        "server_slug": "wiring-probe",
        "method": "tools/list",
        "params": {},
    }
    wc.broker_unified_rpc_http = _http_probe(
        f"{BROKER_URL}/v1/sandbox/{BROKER_ORG}/rpc", probe_body
    )
    wc.broker_stdio_rpc_http = _http_probe(
        f"{BROKER_URL}/v1/sandbox/{BROKER_ORG}/stdio/rpc", probe_body
    )
    return wc


def _load_transport_targets() -> dict | None:
    path = Path(TRANSPORT_MANIFEST)
    if path.is_file():
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("org") and data.get("gateway_key") and data.get("servers"):
            return data
    scale = Path(SCALE_MANIFEST)
    if not scale.is_file():
        return None
    with scale.open(encoding="utf-8") as fh:
        manifest = json.load(fh)
    for org in manifest.get("orgs") or []:
        if org.get("slug") == BROKER_ORG and org.get("gateway_key") and org.get("servers"):
            return {
                "org": org["slug"],
                "gateway_key": org["gateway_key"],
                "servers": {"stdio": org["servers"][0]},
                "_partial": True,
            }
    return None


def _gateway_rpc(org: str, server: str, key: str, method: str, params: dict) -> tuple[int, dict | None, float]:
    rid = str(uuid.uuid4())
    url = f"{GATEWAY_URL}/gateway/{org}/mcp/{server}"
    payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    req = urllib.request.Request(url, method="POST", data=data, headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = json.load(r)
            return r.status, body, (time.perf_counter() - t0) * 1000
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except Exception:
            body = {"error": e.read().decode()[:400]}
        return e.code, body, (time.perf_counter() - t0) * 1000
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}, (time.perf_counter() - t0) * 1000


def _echo_text(body: dict | None) -> str:
    if not body:
        return ""
    result = body.get("result") or {}
    for item in result.get("content") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            return item["text"]
    return ""


def run_transport_round(targets: dict, rnd: int) -> list[TransportCall]:
    org = targets["org"]
    key = targets["gateway_key"]
    servers: dict = targets.get("servers") or {}
    out: list[TransportCall] = []

    for transport in ALL_TRANSPORTS:
        server = servers.get(transport)
        if not server:
            out.append(TransportCall(transport, "", "tools/list", False, 0, "server slug missing"))
            out.append(TransportCall(transport, "", "tools/call", False, 0, "server slug missing"))
            continue

        status, body, ms = _gateway_rpc(org, server, key, "tools/list", {})
        tools = ((body or {}).get("result") or {}).get("tools") or []
        list_ok = status == 200 and len(tools) > 0 and (body or {}).get("error") is None
        out.append(
            TransportCall(
                transport, server, "tools/list", list_ok, status,
                detail=f"tools={len(tools)}", latency_ms=ms,
            )
        )

        canary = f"sandbox-{transport}-r{rnd}-{uuid.uuid4().hex[:8]}"
        status, body, ms = _gateway_rpc(
            org, server, key, "tools/call",
            {"name": "echo", "arguments": {"message": canary}},
        )
        txt = _echo_text(body)
        call_ok = status == 200 and txt == f"Echo: {canary}"
        out.append(
            TransportCall(
                transport, server, "tools/call", call_ok, status,
                detail=f"echo_ok={call_ok}", latency_ms=ms,
            )
        )
    return out


def print_registration_guide() -> None:
    if SKIP_NETWORK_DOC:
        return
    reg_doc = FINDINGS / "TRANSPORT_REGISTRATION.md"
    manifest = Path(TRANSPORT_MANIFEST)
    print("\n--- Post-§3 transport registration ---")
    print(
        "When Claude lands mcp_proxy §3, register one server per transport in zeroshield,\n"
        "fill TRANSPORT_MANIFEST (see example + hints), then re-run this script with ROUNDS=3."
    )
    print(f"  Guide:    {reg_doc.relative_to(REPO)}")
    print(f"  Example:  {manifest.relative_to(REPO)}")
    print("  Slugs:    stdio=everything-1 | streamable-http=http-everything-stub")
    print("            sse=sse-everything-stub | websocket=ws-everything-stub")
    print("  gateway_key: scripts/ralph/.mcp_scale_manifest.json → orgs[0].gateway_key")
    print("--- end registration guide ---\n")


def print_network_assertion_guide() -> None:
    if SKIP_NETWORK_DOC:
        return
    print("\n--- Network assertion (run when wiring is landed) ---")
    print(
        "Goal: during this harness, the gateway process must talk ONLY to "
        "mcp-broker:8311 (and control/redis as usual), NEVER to registered "
        "upstream MCP host:443."
    )
    print("\nOption A — tcpdump on gateway container (preferred):")
    print(
        "  GW=$(docker ps --filter name=gateway -q | head -1)\n"
        "  docker exec $GW sh -c 'apk add tcpdump 2>/dev/null || apt-get install -y tcpdump'\n"
        "  docker exec $GW tcpdump -i any -n 'tcp port 443' -w /tmp/gw-egress.pcap &\n"
        "  ROUNDS=3 python scripts/mcp_sandbox_transport_verify.py\n"
        "  docker exec $GW tcpdump -r /tmp/gw-egress.pcap -n | grep -v ':8311\\|:8100\\|:6379\\|:5432'\n"
        "  # Expect ZERO lines to external MCP hosts (e.g. mcp.linear.app)."
    )
    print("\nOption B — ss snapshot (weaker, no payload proof):")
    print(
        "  GW=$(docker ps --filter name=gateway -q | head -1)\n"
        "  docker exec $GW ss -tnp | grep -E ':443\\s' | grep -v ':8311'\n"
        "  # Run before + during harness; new :443 ESTAB from gateway = FAIL."
    )
    print("\nOption C — gateway test hook (when Claude adds MCP_EGRESS_AUDIT=1):")
    print(
        "  Set MCP_EGRESS_AUDIT=1 on the gateway; it should increment a metric "
        "or log a FATAL if any httpx client targets a registered MCP upstream URL. "
        "See docs/mcp/gateway-integration-checklist.md §5.2."
    )
    print("--- end network guide ---\n")


def _docker_gateway_name() -> str | None:
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", "name=gateway", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        names = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
        return names[0] if names else None
    except Exception:
        return None


def snapshot_gateway_443() -> list[str]:
    """Best-effort ss lines for :443 from gateway (informational only)."""
    gw = _docker_gateway_name()
    if not gw:
        return []
    try:
        out = subprocess.run(
            ["docker", "exec", gw, "ss", "-tnp"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        lines = []
        for ln in (out.stdout or "").splitlines():
            if ":443" in ln and "8311" not in ln:
                lines.append(ln.strip())
        return lines
    except Exception:
        return []


def main() -> int:
    FINDINGS.mkdir(parents=True, exist_ok=True)
    reports: list[RoundReport] = []

    wiring = check_wiring()
    print("WIRING GATE:")
    print(f"  broker_send_rpc in gateway: {'YES' if wiring.broker_send_rpc_present else 'NO'}")
    print(f"  broker /{{org}}/rpc route in source: {'YES' if wiring.broker_unified_rpc_route else 'NO'}")
    print(f"  broker POST /rpc live HTTP: {wiring.broker_unified_rpc_http}")
    print(f"  broker POST /stdio/rpc live HTTP: {wiring.broker_stdio_rpc_http}")
    print(
        f"  mcp_proxy broker_send_rpc refs: {wiring.mcp_proxy_broker_send_rpc_refs} "
        f"(httpx.AsyncClient sites: {wiring.mcp_proxy_httpx_sites})"
    )
    print(f"  mcp_proxy still direct httpx (no broker_send_rpc): {'YES' if wiring.mcp_proxy_direct_httpx else 'NO'}")
    print(f"  wiring landed: {'YES' if wiring.landed else 'NO'}")

    if not wiring.landed and not FORCE_E2E:
        print("\nSANDBOX_TRANSPORT: BLOCKED — Claude must land gateway-integration-checklist §1–3.")
        print_registration_guide()
        print_network_assertion_guide()
        report = {
            "verdict": "BLOCKED",
            "wiring": asdict(wiring),
            "rounds": [],
            "note": "Re-run after broker_send_rpc + POST /{org}/rpc + mcp_proxy sandbox routing.",
        }
        with open(REPORT_PATH, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        return 2

    targets = _load_transport_targets()
    if not targets:
        print("SANDBOX_TRANSPORT: FAIL — no TRANSPORT_MANIFEST or SCALE_MANIFEST")
        return 1
    if targets.get("_partial"):
        missing = [t for t in ALL_TRANSPORTS if t not in (targets.get("servers") or {})]
        print(f"WARN: partial manifest — only stdio configured; missing: {missing}")

    pre_443 = snapshot_gateway_443()
    if pre_443:
        print(f"INFO: gateway :443 connections before run: {len(pre_443)}")

    all_ok = True
    for rnd in range(1, ROUNDS + 1):
        calls = run_transport_round(targets, rnd)
        rr = RoundReport(round=rnd, wiring=wiring, calls=calls)
        reports.append(rr)
        fails = rr.failures()
        if fails:
            all_ok = False
            print(f"Round {rnd}: FAIL ({len(fails)} transport calls)")
            for f in fails:
                print(f"  {f.transport}/{f.method}: {f.detail} http={f.http_status}")
        else:
            print(f"Round {rnd}: all transport calls PASS")

    post_443 = snapshot_gateway_443()
    new_443 = [ln for ln in post_443 if ln not in pre_443]
    if new_443:
        print(f"WARN: new gateway :443 connections after harness ({len(new_443)}) — manual tcpdump advised")
        for ln in new_443[:5]:
            print(f"  {ln}")

    print_network_assertion_guide()

    verdict = "PASS" if all_ok and wiring.landed else "FAIL"
    report = {
        "verdict": verdict,
        "wiring": asdict(wiring),
        "rounds": [
            {"round": r.round, "calls": [asdict(c) for c in r.calls], "failures": len(r.failures())}
            for r in reports
        ],
        "gateway_443_snapshot": {"before": pre_443, "after": post_443, "new": new_443},
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    if verdict == "PASS":
        print(f"\nSANDBOX_TRANSPORT: PASS ({ROUNDS} round(s)) — report {REPORT_PATH}")
        return 0
    print(f"\nSANDBOX_TRANSPORT: FAIL — report {REPORT_PATH}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
