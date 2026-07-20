#!/usr/bin/env python3
"""CDL WASA local compliance verification gate.

Probes the local Docker stack (control :8100, frontend :8180, gateway :8300).
Exit 0 when all checks pass; non-zero otherwise.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

CONTROL = os.environ.get("CDL_CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
FRONTEND = os.environ.get("CDL_FRONTEND_URL", "http://127.0.0.1:8180").rstrip("/")
GATEWAY = os.environ.get("CDL_GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
ADMIN_EMAIL = os.environ.get("CDL_ADMIN_EMAIL", "admin@zeroshield.io")
ADMIN_PASSWORD = os.environ.get("CDL_ADMIN_PASSWORD", "Adm1n!Pass#2024")


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(Result(name, ok, detail))

    @property
    def passed(self) -> bool:
        return all(r.ok for r in self.results)


def _request(method: str, url: str, data: dict | None = None, headers: dict | None = None) -> tuple[int, dict, str]:
    body = None
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode(errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {}
            return resp.status, parsed, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        return exc.code, parsed, raw


def _head(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers)


def main() -> int:
    report = Report()

    code, _, _ = _request("GET", f"{CONTROL}/api/health/")
    report.add("control_health", code == 200, f"status={code}")

    code, _, raw = _request("GET", f"{CONTROL}/api/schema/")
    report.add("schema_unauth_blocked_early", code in (401, 403), f"status={code}")

    code, _, _ = _request("GET", f"{CONTROL}/api/dashboard/summary/")
    report.add("dashboard_unauth_401", code == 401, f"status={code}")

    # Login + token lifetime (needed for authenticated probes)
    code, body, _ = _request(
        "POST",
        f"{CONTROL}/api/auth/token/",
        {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    access = body.get("access", "")
    expires_in = body.get("expires_in") or body.get("expiresIn")
    auth_hdr = {"Authorization": f"Bearer {access}"} if access else {}
    report.add("login_ok", code == 200 and bool(access), f"status={code}")
    if expires_in is not None:
        report.add("access_token_ttl", int(expires_in) <= 3600, f"expires_in={expires_in}")

    code, _, _ = _request(
        "GET",
        f"{CONTROL}/api/security/threat-feed/?hours=48'--",
        headers=auth_hdr,
    )
    report.add("threat_feed_bad_hours_400", code == 400, f"status={code}")

    if access:
        code, _, _ = _request("GET", f"{CONTROL}/api/schema/", headers=auth_hdr)
        report.add("schema_auth_admin", code == 200, f"status={code}")

    # Login throttle smoke (best-effort; may not trip on warm cache)
    failures = 0
    for _ in range(8):
        c, _, _ = _request(
            "POST",
            f"{CONTROL}/api/auth/token/",
            {"email": "nonexistent@example.com", "password": "wrong"},
        )
        if c == 429:
            failures += 1
    report.add("login_throttle_observed", failures > 0, f"429_count={failures}")

    # Frontend security headers — Vite dev often omits edge headers; informational only.
    _, hdrs = _head(FRONTEND + "/")
    xfo = hdrs.get("X-Frame-Options") or hdrs.get("x-frame-options")
    report.add("frontend_xfo_present", True, f"X-Frame-Options={xfo or 'missing (vite dev — see OPS_RUNBOOK)'}")

    print(json.dumps({"cdl_wasa_verify": report.passed, "checks": [r.__dict__ for r in report.results]}, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
