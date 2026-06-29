#!/usr/bin/env python3
"""
Live-stack PII_PKG verification across domains and input/output/both.
Requires: control :8100, gateway :8300, redis, seeded PII_PKG on zeroshield.

  docker compose build control gateway && docker compose up -d control gateway redis
  python3 scripts/verify_pii_live_domains_matrix.py

If localhost :8100/:8300 is intercepted (e.g. IDE port forward), the script
re-runs inside the control container against the real stack automatically.

  docker compose exec control python manage.py seed_pii_policy_package --org-slug zeroshield
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
ORG = os.environ.get("ORG_SLUG", "zeroshield")

PII_EMAIL = "live-pii-probe@example.com"
PII_SSN = "123-45-6789"
CLEAN = "What is the capital of France?"

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
SKIP: list[tuple[str, str]] = []


def ok(name: str) -> None:
    PASS.append(name)


def bad(name: str, detail: str) -> None:
    FAIL.append((name, detail))


def skip(name: str, detail: str) -> None:
    SKIP.append((name, detail))


def http_json(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    data = None
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw) if raw else {"detail": raw}
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return e.code, payload



def _pipeline_policy_count(jwt: str) -> int:
    st, data = http_json(
        "GET",
        f"{CONTROL}/api/policies/?policy_domain=pipeline",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    if st != 200:
        return -1
    if isinstance(data, list):
        return len(data)
    return int(data.get("count") or 0)


def _docker_internal_policy_count() -> int | None:
    """Policy count via control container (bypasses localhost port hijacks on the host)."""
    probe = """
import json, os, urllib.request
email = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
password = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
base = "http://127.0.0.1:8000"
body = json.dumps({"email": email, "password": password}).encode()
req = urllib.request.Request(
    base + "/api/auth/token/",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
jwt = json.load(urllib.request.urlopen(req, timeout=30))["access"]
req2 = urllib.request.Request(
    base + "/api/policies/?policy_domain=pipeline",
    headers={"Authorization": "Bearer " + jwt},
    method="GET",
)
data = json.load(urllib.request.urlopen(req2, timeout=30))
print(data.get("count", 0) if isinstance(data, dict) else len(data))
"""
    try:
        out = subprocess.check_output(
            ["docker", "compose", "exec", "-T", "control", "python", "-c", probe],
            cwd=_REPO_ROOT,
            text=True,
            timeout=90,
            env={
                **os.environ,
                "TEST_EMAIL": EMAIL,
                "TEST_PASSWORD": PASSWORD,
            },
        )
        return int(out.strip().splitlines()[-1])
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def _delegate_to_control_container() -> int:
    script = Path(__file__).resolve()
    dest = "/tmp/verify_pii_live_domains_matrix.py"
    container = "ai_mesh_firewall-control-1"
    subprocess.check_call(["docker", "cp", str(script), f"{container}:{dest}"])
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "-e",
        "CONTROL_URL=http://127.0.0.1:8000",
        "-e",
        "GATEWAY_URL=http://gateway:8300",
        "-e",
        "AI_MESH_VERIFY_IN_CONTAINER=1",
        "-e",
        f"TEST_EMAIL={EMAIL}",
        "-e",
        f"TEST_PASSWORD={PASSWORD}",
        "-e",
        f"ORG_SLUG={ORG}",
        "control",
        "python",
        dest,
    ]
    print(
        "NOTE: localhost control/gateway ports returned empty policy data; "
        "re-running inside control container (real Docker stack).\n",
        file=sys.stderr,
    )
    return subprocess.call(cmd, cwd=_REPO_ROOT)


def _maybe_delegate_for_localhost_port_conflict(jwt: str) -> int | None:
    if os.environ.get("AI_MESH_VERIFY_IN_CONTAINER"):
        return None
    if os.environ.get("CONTROL_URL") or os.environ.get("GATEWAY_URL"):
        return None
    if _pipeline_policy_count(jwt) > 0:
        return None
    internal = _docker_internal_policy_count()
    if internal is None or internal <= 0:
        return None
    return _delegate_to_control_container()

def login_jwt() -> str:
    st, data = http_json("POST", f"{CONTROL}/api/auth/token/", {"email": EMAIL, "password": PASSWORD})
    if st != 200 or "access" not in data:
        raise RuntimeError(f"login failed {st} {data}")
    return data["access"]


def get_gateway_key(jwt: str) -> str:
    st, data = http_json(
        "POST",
        f"{CONTROL}/api/gateways/keys/",
        {"name": "pii-live-matrix", "project_id": "pii-live-matrix"},
        {"Authorization": f"Bearer {jwt}"},
    )
    if st in (200, 201) and data.get("key"):
        return data["key"]
    # list existing
    st, data = http_json("GET", f"{CONTROL}/api/gateways/keys/", headers={"Authorization": f"Bearer {jwt}"})
    if st == 200:
        for row in data if isinstance(data, list) else data.get("results", []):
            if row.get("key"):
                return row["key"]
    raise RuntimeError(f"no gateway key {st} {data}")


def pii_in_match(data: dict, code_prefix: str = "PII_PKG") -> bool:
    pols = data.get("matched_policies") or data.get("matched_policy_codes") or []
    return any(str(p).startswith(code_prefix) for p in pols)


def control_dry_run(jwt: str, domain: str, surface: str) -> tuple[int, dict]:
    """surface: input | output | both"""
    body: dict = {"policy_domain": domain, "metadata": {"policy_domain": domain}}
    if surface == "input":
        body["prompt"] = f"Contact {PII_EMAIL} and SSN {PII_SSN}"
        body["response"] = CLEAN
    elif surface == "output":
        body["prompt"] = CLEAN
        body["response"] = f"Reply to {PII_EMAIL} SSN {PII_SSN}"
    else:
        text = f"Both {PII_EMAIL} {PII_SSN}"
        body["prompt"] = text
        body["response"] = text
    return http_json(
        "POST",
        f"{CONTROL}/api/policies/test/",
        body,
        {"Authorization": f"Bearer {jwt}"},
    )


def gateway_policy_check(gw_key: str, surface: str) -> tuple[int, dict]:
    body: dict = {}
    if surface == "input":
        body["prompt"] = f"GW {PII_EMAIL} {PII_SSN}"
        body["response"] = ""
    elif surface == "output":
        body["prompt"] = CLEAN
        body["response"] = f"GW out {PII_EMAIL}"
    else:
        t = f"GW both {PII_EMAIL}"
        body["prompt"] = t
        body["response"] = t
    return http_json(
        "POST",
        f"{GATEWAY}/v1/policy/check",
        body,
        {"Authorization": f"Bearer {gw_key}"},
    )


def mcp_dry_run(jwt: str, surface: str) -> tuple[int, dict]:
    body: dict = {
        "policy_domain": "mcp",
        "metadata": {"policy_domain": "mcp"},
        "prompt": "",
        "response": "",
    }
    if surface == "input":
        body["input_args"] = {"note": f"MCP {PII_EMAIL} {PII_SSN}"}
    elif surface == "output":
        body["output_data"] = {"result": f"MCP out {PII_EMAIL}"}
    else:
        body["input_args"] = {"x": PII_EMAIL}
        body["output_data"] = {"y": PII_SSN}
    return http_json(
        "POST",
        f"{CONTROL}/api/policies/test/",
        body,
        {"Authorization": f"Bearer {jwt}"},
    )


def triage_control_domains(jwt: str) -> None:
    for domain in ("pipeline", "rag", "mcp"):
        for surface in ("input", "output", "both"):
            name = f"control_test_{domain}_{surface}"
            st, data = control_dry_run(jwt, domain, surface)
            if st != 200:
                bad(name, f"http {st} {data}")
                continue
            if data.get("action") == "redact" and pii_in_match(data):
                ok(name)
            elif data.get("action") == "redact":
                bad(name, f"redact but no PII_PKG in {data.get('matched_policies')}")
            else:
                bad(name, f"action={data.get('action')} rules={data.get('matched_rules')}")


def triage_gateway_pipeline(gw_key: str) -> None:
    for surface in ("input", "output", "both"):
        name = f"gateway_pipeline_{surface}"
        st, data = gateway_policy_check(gw_key, surface)
        if st not in (200, 403):
            bad(name, f"http {st} {data}")
            continue
        action = data.get("action") or data.get("decision")
        if action in ("redact", "block") or st == 403:
            ok(name)
        else:
            bad(name, str(data)[:300])


def triage_mcp(jwt: str) -> None:
    for surface in ("input", "output", "both"):
        name = f"mcp_simulator_{surface}"
        st, data = mcp_dry_run(jwt, surface)
        if st != 200:
            bad(name, f"http {st} {data}")
            continue
        if data.get("action") == "redact":
            ok(name)
        else:
            bad(name, f"action={data.get('action')}")


def triage_vector_analytics(jwt: str) -> None:
    st, data = http_json(
        "GET",
        f"{CONTROL}/api/policies/?policy_domain=pipeline",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    if st == 200:
        results = data if isinstance(data, list) else data.get("results", [])
        pii = [p for p in results if str(p.get("code", "")).startswith("PII_PKG")]
        if pii:
            ok("vector_tab_lists_pipeline_pii_policy")
        else:
            bad("vector_tab_lists_pipeline_pii_policy", "PII_PKG not in pipeline list")
    else:
        bad("vector_tab_lists_pipeline_pii_policy", f"http {st}")

    # Vector collection policies are separate
    st2, _ = http_json(
        "GET",
        f"{CONTROL}/api/vector-policies/",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    if st2 in (200, 404):
        skip(
            "vector_collection_uses_separate_model",
            "VectorCollectionPolicy is not Policy.policy_domain; PII_PKG does not auto-apply to vector ACL",
        )
    else:
        skip("vector_collection_uses_separate_model", f"http {st2}")

    st3, data3 = http_json(
        "GET",
        f"{CONTROL}/api/security/soc-kpis/?period=24h",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    if st3 == 200:
        skip("analytics_read_only", "SOC KPIs returned; Analytics does not enforce Policy rules")
    else:
        skip("analytics_read_only", f"soc-kpis http {st3} (endpoint may need data)")


def triage_org_isolation(jwt: str) -> None:
    st, data = http_json(
        "GET",
        f"{CONTROL}/api/policies/?policy_domain=pipeline",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    if st != 200:
        bad("org_jwt_scoped_list", f"http {st}")
        return
    results = data if isinstance(data, list) else data.get("results", [])
    codes = [p.get("code") for p in results]
    if any(c and c.startswith("PII_PKG") for c in codes):
        ok("org_jwt_sees_own_pii_pkg")
    else:
        bad("org_jwt_sees_own_pii_pkg", f"codes={codes[:5]}")


def main() -> int:
    print(f"Control={CONTROL} Gateway={GATEWAY} org={ORG}\n")
    try:
        jwt = login_jwt()
    except Exception as exc:
        print(f"SETUP FAILED: {exc}")
        return 1
    delegated = _maybe_delegate_for_localhost_port_conflict(jwt)
    if delegated is not None:
        return delegated
    try:
        gw = get_gateway_key(jwt)
    except Exception as exc:
        print(f"SETUP FAILED: {exc}")
        return 1

    http_json("POST", f"{CONTROL}/api/policies/compile/", {}, {"Authorization": f"Bearer {jwt}"})

    triage_control_domains(jwt)
    triage_gateway_pipeline(gw)
    triage_mcp(jwt)
    triage_vector_analytics(jwt)
    triage_org_isolation(jwt)

    print("\n=== LIVE PII DOMAIN MATRIX ===\n")
    for n in PASS:
        print(f"  PASS  {n}")
    for n, d in SKIP:
        print(f"  SKIP  {n}: {d}")
    for n, d in FAIL:
        print(f"  FAIL  {n}: {d}")
    print(f"\nTotals: {len(PASS)} pass, {len(SKIP)} skip, {len(FAIL)} fail\n")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
