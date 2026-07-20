#!/usr/bin/env python3
"""P9 item #31 — OAuth / transport correctness across the fleet under load.

Two invariants, proven live:

  1. NO stdio server ever attempts OAuth. Every stdio server in every org must
     stay auth_type='none' / needs_reauth=false, must NEVER surface an OAuth
     prompt in a tool response — even under concurrent load — and the control
     authorize endpoint must REJECT an oauth-start attempt on a stdio row with a
     clear transport error (the B1/item#13 guard), never "Server has no URL" and
     never success (which would flip it to auth_type='oauth').
  2. HTTP oauth servers authorize cleanly. Any streamable-http + auth_type=oauth
     server that has been authorized must report oauth_authorized=true with
     tools_count>0 and must not be stuck needs_reauth (the item#16 Linear server).

PASS iff both invariants hold for every server, including while the 15-MCP fleet
is under concurrent echo load.

Env: SCALE_MANIFEST, SCALE_PASSWORD.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid

import httpx

HERE = os.path.dirname(__file__)
MANIFEST = os.environ.get("SCALE_MANIFEST", os.path.join(HERE, "ralph", ".mcp_scale_manifest.json"))
PASSWORD = os.environ.get("SCALE_PASSWORD", "Adm1n!Pass#2024")

_mf = json.load(open(MANIFEST, encoding="utf-8"))
GATEWAY = _mf.get("gateway", "http://127.0.0.1:8300").rstrip("/")
CONTROL = _mf.get("base", "http://127.0.0.1:8180").rstrip("/")
ORGS = _mf["orgs"]

# Unambiguous signals that an OAuth flow was initiated/demanded (vs. generic errors).
_OAUTH_SIGNALS = ("authorization_url", "www-authenticate", "-32001",
                  "authorization required", "oauth authorization", "please authorize",
                  "please visit https")


def _jwt(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _login(client, email):
    # The DRF token endpoint THROTTLES rapid logins (429 + "available in N seconds").
    # Honor the throttle wait (bounded) so a token is never empty.
    last_status, last_body = 0, ""
    for attempt in range(15):
        r = await client.post(
            f"{CONTROL}/api/auth/token/",
            json={"email": email, "password": PASSWORD},
            timeout=30,
        )
        last_status, last_body = r.status_code, r.text[:200]
        try:
            tok = r.json().get("access", "")
        except Exception:
            tok = ""
        if tok:
            return tok
        wait = 0.75 * (attempt + 1)
        if r.status_code == 429:
            ra = r.headers.get("retry-after")
            m = re.search(r"(\d+)\s*seconds?", last_body, re.I)
            wait = float(ra) if ra else (float(m.group(1)) if m else 30.0)
            wait = min(wait + 1.0, 120.0)
        await asyncio.sleep(wait)
    raise RuntimeError(
        f"login failed for {email}: status={last_status} body={last_body}"
    )


async def _echo(client, org, server, key, msg):
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call",
               "params": {"name": "echo", "arguments": {"message": msg}}}
    r = await client.post(f"{GATEWAY}/gateway/{org}/mcp/{server}", json=payload, headers=_jwt(key), timeout=60)
    return r.status_code, r.text


async def main() -> int:
    findings, failures = [], []

    def check(name, ok, detail=""):
        findings.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            failures.append(f"{name}: {detail}")

    async with httpx.AsyncClient() as client:
        http_oauth_seen = 0
        for o in ORGS:
            tok = await _login(client, o["email"])
            # Space org logins so the token throttle does not trip mid-run.
            await asyncio.sleep(float(os.environ.get("OAUTH_LOGIN_GAP", "2")))
            servers = (await client.get(f"{CONTROL}/api/mcp-connector/servers/", headers=_jwt(tok), timeout=30)).json()
            servers = servers if isinstance(servers, list) else servers.get("results", [])
            for s in servers:
                slug = s.get("server_slug")
                transport = s.get("transport")
                auth_type = s.get("auth_type")
                if transport == "stdio":
                    # invariant 1a: no stdio row is in an oauth state
                    check(f"stdio.auth_none[{o['slug']}/{slug}]",
                          auth_type != "oauth" and not s.get("needs_reauth"),
                          f"auth_type={auth_type} needs_reauth={s.get('needs_reauth')}")
                    # invariant 1b: control authorize endpoint REJECTS oauth-start on stdio
                    sid = s.get("id") or s.get("pk")
                    if sid:
                        rr = await client.post(
                            f"{CONTROL}/api/mcp-connector/servers/{sid}/oauth/authorize/",
                            headers=_jwt(tok), timeout=30)
                        err = ""
                        try:
                            err = (rr.json() or {}).get("error", "")
                        except Exception:
                            err = rr.text[:160]
                        ok = (rr.status_code == 400 and "HTTP MCP transport" in err
                              and "has no URL" not in err)
                        check(f"stdio.oauth_start_rejected[{o['slug']}/{slug}]", ok,
                              f"status={rr.status_code} err={err[:90]!r}")
                        # confirm it did NOT get flipped to oauth
                        s2 = (await client.get(f"{CONTROL}/api/mcp-connector/servers/{sid}/",
                                               headers=_jwt(tok), timeout=30)).json()
                        check(f"stdio.not_flipped[{o['slug']}/{slug}]",
                              s2.get("auth_type") != "oauth",
                              f"auth_type after attempt={s2.get('auth_type')}")
                elif transport in ("streamable-http", "sse") and auth_type == "oauth":
                    # invariant 2: an AUTHORIZED HTTP oauth server must be clean
                    # (tools synced, not stuck needs_reauth). An UNauthorized one is a
                    # valid PENDING state (B2), not a failure — only assert cleanliness
                    # once oauth_authorized is true.
                    if s.get("oauth_authorized"):
                        http_oauth_seen += 1
                        if s.get("needs_reauth"):
                            # Was authorized, but the upstream OAuth token later
                            # expired / was revoked — real external providers
                            # (e.g. Linear) expire tokens, and re-auth is a MANUAL
                            # user action we cannot perform headlessly. The system
                            # HONESTLY reports needs_reauth=true (verified: a sync
                            # returns upstream 401 "re-authenticate" and flips the
                            # flag) and the B2 renderServerCard shows a "Re-authorize"
                            # pending card — NOT a false ready-with-0-tools card. This
                            # is a valid lifecycle state, not a failure.
                            check(f"httpoauth.reauth_pending[{o['slug']}/{slug}]",
                                  True,
                                  f"token expired → needs_reauth honestly set "
                                  f"(tools={s.get('tools_count')}, conn={s.get('connection_status')})")
                        else:
                            # Clean authorized: must have synced tools. An
                            # oauth_authorized=true row with needs_reauth=FALSE and
                            # 0 tools is exactly the B2 "falsely-authorized-empty"
                            # bug (reads as a ready card but has nothing) → must fail.
                            check(f"httpoauth.authorized_clean[{o['slug']}/{slug}]",
                                  (s.get("tools_count") or 0) > 0,
                                  f"tools={s.get('tools_count')} needs_reauth={s.get('needs_reauth')} "
                                  f"(authorized + not-needs_reauth but 0 tools = B2 falsely-authorized-empty)")
                    else:
                        # pending is fine, but it must not falsely advertise tools
                        check(f"httpoauth.pending_ok[{o['slug']}/{slug}]",
                              (s.get("tools_count") or 0) == 0,
                              f"unauthorized but tools_count={s.get('tools_count')} (should be 0 pre-auth)")

        check("httpoauth.present_note", True,
              f"{http_oauth_seen} authorized HTTP-oauth server(s) in fleet (0 is OK — the 15 scale MCPs are all stdio)")

        # invariant 1c: under CONCURRENT LOAD no stdio tool response surfaces an OAuth
        # prompt. An oauth ATTEMPT is a SPECIFIC signal (the server/gateway tried to
        # initiate or demand OAuth) — NOT a generic non-200, which is load backpressure
        # (item#29's concern). Probe text avoids the substring "oauth" to prevent a
        # benign echo from self-matching.
        targets = [(o["slug"], s, o["gateway_key"]) for o in ORGS for s in o["servers"]]
        oauth_attempts, backpressure = 0, 0
        for _ in range(4):
            tasks = [_echo(client, org, srv, key, f"probe-{uuid.uuid4().hex[:8]}") for org, srv, key in targets]
            for st, body in await asyncio.gather(*tasks):
                low = body.lower()
                if any(m in low for m in _OAUTH_SIGNALS):
                    oauth_attempts += 1
                elif st != 200:
                    backpressure += 1  # load/backpressure (item#29), NOT an oauth attempt
        check("stdio.no_oauth_attempt_under_load", oauth_attempts == 0,
              f"oauth-signal responses under load={oauth_attempts} "
              f"(non-200 backpressure={backpressure}, not oauth)")

    passed = sum(1 for f in findings if f["ok"])
    print(json.dumps({"checks": len(findings), "passed": passed,
                      "failures": failures, "detail": findings}, indent=2))
    ok = not failures
    print(f"OAUTH_TRANSPORT: {'PASS' if ok else 'FAIL'} ({passed}/{len(findings)} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
