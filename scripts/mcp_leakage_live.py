#!/usr/bin/env python3
"""P9 item #30 — cross-tenant LEAKAGE proof (canary planted in one org).

Isolation invariant (progress.md): one sandbox per org; NO cross-tenant tool /
data / credential / result visibility on ANY channel. This harness plants a
unique canary as persisted tenant data in ONE victim org, confirms the OWNER can
see it (positive control), then exhaustively proves NO OTHER org can observe it —
and cannot invoke the victim's servers — across every channel:

  A. server enumeration  — attacker GET /servers/ must not contain the canary
  B. object-level authz  — attacker GET /servers/{victim_id}/ → 403/404, no canary
  C. tool listing        — attacker GET /servers/{victim_id}/tools/ → 403/404
  D. gateway invocation  — attacker key vs victim gateway path → 401/403 (N×N matrix)
  E. audit isolation     — attacker GET /events/ must not contain the canary/slug
  F. data-plane bleed    — while the victim actively echoes the canary through ITS
                           servers, the attacker's concurrent echoes never carry it

PASS iff: owner sees the canary (positive control) AND every attacker channel is
canary-free with the right rejection AND the invocation matrix is correct (own=200,
foreign=rejected). The canary server is deleted at the end (restore 3×5 invariant).

Env: SCALE_MANIFEST, SCALE_PASSWORD, VICTIM (default org-b).
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx

HERE = os.path.dirname(__file__)
MANIFEST = os.environ.get("SCALE_MANIFEST", os.path.join(HERE, "ralph", ".mcp_scale_manifest.json"))
PASSWORD = os.environ.get("SCALE_PASSWORD", "Adm1n!Pass#2024")
VICTIM = os.environ.get("VICTIM", "org-b")

_mf = json.load(open(MANIFEST, encoding="utf-8"))
GATEWAY = _mf.get("gateway", "http://127.0.0.1:8300").rstrip("/")
CONTROL = _mf.get("base", "http://127.0.0.1:8180").rstrip("/")
ORGS = _mf["orgs"]


async def _login(client, email):
    # Retry: the DRF token endpoint throttles rapid logins — back off so a token
    # is never empty (empty Bearer -> httpx LocalProtocolError / spurious 401s).
    for attempt in range(6):
        r = await client.post(f"{CONTROL}/api/auth/token/", json={"email": email, "password": PASSWORD}, timeout=30)
        try:
            tok = r.json().get("access", "")
        except Exception:
            tok = ""
        if tok:
            return tok
        await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"login failed for {email}: status={r.status_code} body={r.text[:160]}")


def _jwt(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _gateway_call(client, org, server, key, msg):
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call",
               "params": {"name": "echo", "arguments": {"message": msg}}}
    r = await client.post(f"{GATEWAY}/gateway/{org}/mcp/{server}", json=payload, headers=_jwt(key), timeout=60)
    body = "" if r.status_code >= 500 else r.text
    return r.status_code, body


async def main() -> int:
    canary = f"CANARY-{uuid.uuid4().hex}"
    victim = next(o for o in ORGS if o["slug"] == VICTIM)
    attackers = [o for o in ORGS if o["slug"] != VICTIM]
    findings, failures = [], []

    def check(name, ok, detail=""):
        findings.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            failures.append(f"{name}: {detail}")

    async with httpx.AsyncClient() as client:
        tokens = {o["slug"]: await _login(client, o["email"]) for o in ORGS}
        vt = tokens[victim["slug"]]

        # ---- PLANT: register a canary server in the victim org (persisted tenant data)
        reg = await client.post(f"{CONTROL}/api/mcp-connector/servers/", headers=_jwt(vt), timeout=60, json={
            "name": f"canary-{canary}", "description": f"top-secret {canary}",
            "transport": "stdio", "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-everything"],
        })
        planted = reg.json() if reg.status_code < 300 else {}
        canary_id = planted.get("id") or planted.get("pk")
        canary_slug = planted.get("server_slug")
        check("plant.registered", bool(canary_id), f"status={reg.status_code} id={canary_id} slug={canary_slug} body={reg.text[:160]}")

        # ---- POSITIVE CONTROL: owner CAN see the canary (list + detail)
        vlist = await client.get(f"{CONTROL}/api/mcp-connector/servers/", headers=_jwt(vt), timeout=30)
        check("positive.owner_list_sees_canary", canary in vlist.text, f"status={vlist.status_code}")
        if canary_id:
            vdet = await client.get(f"{CONTROL}/api/mcp-connector/servers/{canary_id}/", headers=_jwt(vt), timeout=30)
            check("positive.owner_detail_sees_canary", canary in vdet.text and vdet.status_code == 200,
                  f"status={vdet.status_code}")

        # ---- ATTACKER CHANNELS (each other org must NEVER observe the canary)
        attacker_aggregate = []
        for atk in attackers:
            at = tokens[atk["slug"]]
            ak = atk["gateway_key"]
            # A. enumeration
            alist = await client.get(f"{CONTROL}/api/mcp-connector/servers/", headers=_jwt(at), timeout=30)
            attacker_aggregate.append(alist.text)
            check(f"A.enum[{atk['slug']}]", canary not in alist.text and (canary_slug or "\x00") not in alist.text,
                  f"status={alist.status_code}")
            # B. object-level authz (GET victim's server by id)
            if canary_id:
                adet = await client.get(f"{CONTROL}/api/mcp-connector/servers/{canary_id}/", headers=_jwt(at), timeout=30)
                attacker_aggregate.append(adet.text)
                check(f"B.detail_authz[{atk['slug']}]", adet.status_code in (403, 404) and canary not in adet.text,
                      f"status={adet.status_code} (want 403/404) leak={canary in adet.text}")
                # C. tools listing
                atools = await client.get(f"{CONTROL}/api/mcp-connector/servers/{canary_id}/tools/", headers=_jwt(at), timeout=30)
                attacker_aggregate.append(atools.text)
                check(f"C.tools_authz[{atk['slug']}]", atools.status_code in (403, 404) and canary not in atools.text,
                      f"status={atools.status_code} (want 403/404)")
            # D. gateway invocation of victim's canary server via attacker key + victim path
            if canary_slug:
                st, body = await _gateway_call(client, victim["slug"], canary_slug, ak, "probe")
                attacker_aggregate.append(body)
                check(f"D.gw_invoke_victim[{atk['slug']}]", st in (401, 403) and canary not in body,
                      f"status={st} (want 401/403)")
            # E. audit isolation
            aev = await client.get(f"{CONTROL}/api/mcp-connector/events/?hours=1", headers=_jwt(at), timeout=30)
            attacker_aggregate.append(aev.text)
            check(f"E.audit[{atk['slug']}]", canary not in aev.text and (canary_slug or "\x00") not in aev.text,
                  f"status={aev.status_code}")

        # ---- D2. full N×N invocation matrix: own key→own path = 200; foreign = rejected
        for ko in ORGS:
            for po in ORGS:
                srv = po["servers"][0]
                st, body = await _gateway_call(client, po["slug"], srv, ko["gateway_key"], "matrix-probe")
                if ko["slug"] == po["slug"]:
                    check(f"D2.own[{ko['slug']}]", st == 200, f"status={st} (want 200)")
                else:
                    check(f"D2.foreign[{ko['slug']}→{po['slug']}]", st in (401, 403),
                          f"status={st} (want 401/403)")

        # ---- F. data-plane: victim actively echoes canary through ALL its servers while
        # attackers concurrently echo their own nonces — attacker replies must be canary-free.
        async def victim_flux():
            tasks = []
            for _ in range(20):
                for s in victim["servers"]:
                    tasks.append(_gateway_call(client, victim["slug"], s, victim["gateway_key"], canary))
            return await asyncio.gather(*tasks)

        async def attacker_probe():
            out = []
            for _ in range(20):
                tasks = []
                for atk in attackers:
                    for s in atk["servers"]:
                        tasks.append(_gateway_call(client, atk["slug"], s, atk["gateway_key"],
                                                   f"benign|{atk['slug']}|{uuid.uuid4().hex[:6]}"))
                out.extend(await asyncio.gather(*tasks))
            return out

        vflux, aprobe = await asyncio.gather(victim_flux(), attacker_probe())
        victim_ok = sum(1 for st, b in vflux if st == 200 and canary in b)
        attacker_bleed = sum(1 for st, b in aprobe if canary in b)
        for _, b in aprobe:
            attacker_aggregate.append(b)
        check("F.dataplane.victim_flux_carried_canary", victim_ok > 0, f"victim echoed canary {victim_ok}x")
        check("F.dataplane.no_attacker_bleed", attacker_bleed == 0, f"attacker replies carrying canary={attacker_bleed}")

        # ---- independent oracle: the canary must be wholly absent from the attacker aggregate
        agg = "\n".join(attacker_aggregate)
        check("oracle.canary_absent_in_attacker_aggregate", canary not in agg,
              f"attacker-visible bytes={len(agg)}")

        # ---- CLEANUP: delete the canary server (restore 3×5=15 invariant)
        deleted = None
        if canary_id:
            d = await client.delete(f"{CONTROL}/api/mcp-connector/servers/{canary_id}/", headers=_jwt(vt), timeout=30)
            deleted = d.status_code
            vlist2 = await client.get(f"{CONTROL}/api/mcp-connector/servers/", headers=_jwt(vt), timeout=30)
            check("cleanup.canary_deleted", d.status_code in (200, 202, 204) and canary not in vlist2.text,
                  f"delete_status={d.status_code}")

    passed = sum(1 for f in findings if f["ok"])
    print(json.dumps({"canary": canary, "victim": victim["slug"],
                      "checks": len(findings), "passed": passed,
                      "failures": failures, "detail": findings}, indent=2))
    ok = not failures
    print(f"LEAKAGE: {'PASS' if ok else 'FAIL'} ({passed}/{len(findings)} checks)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
