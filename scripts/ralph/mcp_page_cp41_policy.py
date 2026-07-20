#!/usr/bin/env python3
"""MCP-page Ralph — CP41: MCP Security Policies have a REAL EFFECT on enforcement.
Creates an mcp-domain Policy + keyword Rule (action=block) via the control shell,
then executes a MATCHING vs NON-MATCHING MCP tool call through the real tool-call
path (control MCPToolCallView → policy_evaluate(domain='mcp')). Proves: a matching
call is BLOCKED by policy; a non-matching call is allowed; disabling the policy
un-blocks the matching call (enable/disable control has a real effect).
"""
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8100"
EMAIL, PASS = "admin@zeroshield.io", "Adm1n!Pass#2024"
KW = "cp41blockword777"
CTL = "ai_mesh_firewall-control-1"


def _req(method, path, tok=None, body=None, timeout=90, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    if headers:
        h.update(headers)
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, (json.loads(resp.read().decode() or "{}"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw[:150]}
    except Exception as e:
        return "ERR", {"_raw": str(e)[:120]}


def shell(code):
    return subprocess.run(
        ["docker", "exec", CTL, "python", "/app/control/manage.py", "shell", "-c", code],
        capture_output=True, text=True, timeout=60).stdout


def login():
    st, b = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login {st}: {b}")
    return b["access"]


def exec_echo(tok, slug, msg):
    st, r = _req("POST", "/api/mcp-connector/tools/call/", tok=tok,
                 body={"name": "echo", "arguments": {"message": msg}, "server_slug": slug},
                 headers={"X-Server-Slug": slug}, timeout=60)
    txt = json.dumps(r)
    blocked = (st == 403 and "polic" in txt.lower()) or r.get("blocked") is True
    allowed = st == 200 and f"Echo: {msg}" in txt
    return {"status": st, "blocked": blocked, "allowed": allowed, "snip": txt[:100]}


def set_policy(create=True, enabled=True):
    if create:
        code = (
            "from auth.models import Organization;"
            "from policy.models import Policy, Rule;"
            "o=Organization.objects.filter(slug='zeroshield').first();"
            "Policy.objects.filter(organization=o, code='cp41-block').delete();"
            "p=Policy.objects.create(organization=o, name='cp41 block', code='cp41-block', policy_domain='mcp', enabled=True);"
            f"Rule.objects.create(policy=p, name='kw', rule_type='keywords', condition={{'keywords':['{KW}'],'field':'prompt'}}, action='block', enabled=True);"
            "print('POLICY_OK')"
        )
    else:
        code = (
            "from auth.models import Organization;from policy.models import Policy;"
            "o=Organization.objects.filter(slug='zeroshield').first();"
            f"Policy.objects.filter(organization=o, code='cp41-block').update(enabled={enabled});"
            "print('POLICY_TOGGLED')"
        )
    out = shell(code)
    return "POLICY_OK" in out or "POLICY_TOGGLED" in out


def cleanup_policy():
    shell("from auth.models import Organization;from policy.models import Policy;"
          "o=Organization.objects.filter(slug='zeroshield').first();"
          "Policy.objects.filter(organization=o, code='cp41-block').delete();print('CLEAN')")


def main():
    tok = login()
    _, lst = _req("GET", "/api/mcp-connector/servers/", tok=tok)
    for row in (lst if isinstance(lst, list) else []):
        if str(row.get("name", "")).startswith("cp41-"):
            _req("DELETE", f"/api/mcp-connector/servers/{row['id']}/", tok=tok)
    st, c = _req("POST", "/api/mcp-connector/servers/", tok=tok, body={
        "name": "cp41-everything", "transport": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"], "auth_type": "none"})
    sid, slug = c["id"], c["server_slug"]
    for _ in range(3):
        _, sync = _req("POST", f"/api/mcp-connector/servers/{sid}/tools/", tok=tok, timeout=120)
        if (sync.get("tools") or []):
            break
        time.sleep(3)

    report = {}
    # baseline: no policy → matching keyword allowed
    cleanup_policy()
    report["baseline_match_allowed"] = exec_echo(tok, slug, KW)
    # create block policy → matching keyword BLOCKED, non-matching allowed
    set_policy(create=True)
    time.sleep(1)
    report["with_policy_match"] = exec_echo(tok, slug, KW)
    report["with_policy_nonmatch"] = exec_echo(tok, slug, "hello-benign")
    # disable policy → matching keyword allowed again (enable/disable real effect)
    set_policy(create=False, enabled=False)
    time.sleep(1)
    report["policy_disabled_match"] = exec_echo(tok, slug, KW)

    cleanup_policy()
    _req("DELETE", f"/api/mcp-connector/servers/{sid}/", tok=tok)

    checks = {
        "baseline_allowed": report["baseline_match_allowed"]["allowed"],
        "policy_blocks_match": report["with_policy_match"]["blocked"],
        "policy_allows_nonmatch": report["with_policy_nonmatch"]["allowed"],
        "disable_unblocks": report["policy_disabled_match"]["allowed"],
    }
    cp41 = all(checks.values())
    print(json.dumps({"checkpoint": "41", "cp41Pass": cp41, "checks": checks, "detail": report}, indent=2))
    print(f"CP41: {'PASS' if cp41 else 'FAIL'} — MCP Security Policy blocks a matching tool call, "
          f"allows non-matching, and disable un-blocks (real enforcement effect)")
    sys.exit(0 if cp41 else 1)


if __name__ == "__main__":
    main()
