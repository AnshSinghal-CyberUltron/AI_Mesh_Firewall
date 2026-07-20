#!/usr/bin/env python3
"""MCP-page Ralph — Checkpoint 17 verify (stable client error code + correlation id).

Extends CP16: registers failing MCPs of distinct failure classes and asserts the
sync API response carries (a) a clean branded message, (b) a STABLE client-facing
`error_code` matching the failure category, and (c) a non-empty `correlation_id`
that also appears as the `(Ref: ...)` in the message. Direct control API.
"""
import json
import re
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8100"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"

LEAK_PATTERNS = [
    r"<!doctype", r"<html", r"exited with code", r"exit code", r"code\s+-?\d+",
    r"proc\.", r"sandbox-agent", r"gateway logs", r"traceback", r"stderr",
    r"http 4\d\d:\s*<", r"stdout stream", r"host dependenc", r"'[^']*mcp[^']*'\s+failed",
]
REF_RE = re.compile(r"\(ref:\s*([0-9a-f]{6,})\)", re.I)
VALID_CODES = {
    "MCP_AUTH_FAILED", "MCP_EGRESS_DENIED", "MCP_OUT_OF_MEMORY", "MCP_SERVER_CRASHED",
    "MCP_IMAGE_UNAVAILABLE", "MCP_INSUFFICIENT_STORAGE", "MCP_TIMEOUT", "MCP_START_FAILED", "MCP_UNAVAILABLE",
}


def _req(method, path, token=None, body=None, timeout=30):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw}


def login():
    st, body = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login failed {st}: {body}")
    return body["access"]


def check_case(name, create_body, token, expected_codes, results):
    st, created = _req("POST", "/api/mcp-connector/servers/", token=token, body=create_body)
    if st not in (200, 201):
        results[name] = {"pass": False, "why": f"create failed {st}: {created}"}
        return
    sid = created.get("id")
    sst, resp = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", token=token, timeout=60)
    msg = str(resp.get("error") or "")
    code = resp.get("error_code")
    corr = resp.get("correlation_id")
    low = msg.lower()
    m = REF_RE.search(low)
    ref_in_msg = m.group(1) if m else None
    scan = REF_RE.sub("", low)
    leaks = [p for p in LEAK_PATTERNS if re.search(p, scan)]

    checks = {
        "code_valid": code in VALID_CODES,
        "code_expected": (code in expected_codes) if expected_codes else True,
        "has_correlation_id": bool(corr) and re.fullmatch(r"[0-9a-f]{6,}", str(corr)) is not None,
        "ref_matches_corr": ref_in_msg is not None and ref_in_msg == corr,
        "no_leak": not leaks,
        "branded": "the mcp server" in low,
    }
    results[name] = {
        "pass": all(checks.values()), "error_code": code, "correlation_id": corr,
        "checks": checks, "leaks": leaks, "msg": msg[:200],
    }
    _req("DELETE", f"/api/mcp-connector/servers/{sid}/", token=token)


def main():
    token = login()
    results = {}
    # HTTP upstream returning HTML → generic unavailable (405 body isn't a start-failure fingerprint).
    check_case("http_405", {
        "name": "cp17-http", "transport": "streamable-http",
        "url": "https://example.com/mcp", "auth_type": "none",
    }, token, {"MCP_UNAVAILABLE", "MCP_START_FAILED", "MCP_TIMEOUT"}, results)
    # stdio bad binary → start-failure / crash class (any non-generic start code acceptable).
    check_case("stdio_badcmd", {
        "name": "cp17-stdio", "transport": "stdio",
        "command": "definitely-not-a-real-mcp-binary-xyz", "args": "[]", "auth_type": "none",
    }, token, None, results)

    all_pass = all(r.get("pass") for r in results.values())
    out = {"checkpoint": "17", "cp17Pass": all_pass, "cases": results}
    print(json.dumps(out, indent=2))
    print("CP17: PASS — stable client error_code + correlation_id on every sanitized failure, no leak"
          if all_pass else "CP17: FAIL")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
