#!/usr/bin/env python3
"""Task 1(d) final: classify every FirewallConfig serializer field by whether the gateway
actually consumes it per-org. Inputs: 01_*.json (serializer fields, payload map) and
02_*.json (read-context hits with receivers). Receiver rules (verified manually, see
06_cited_lines.out.txt / 07_verify_citations.out.txt):
  * config_sync.py:558 `data.get("log_level")` sits inside ConfigSync._apply(), which is only
    invoked for the retired GLOBAL key `firewall:config` (config_sync.py:487, :689); control
    only writes `firewall:config:{slug}` (core/models.py:1373)  -> DEAD PATH for per-org writes.
  * receivers `CONFIG` / `if CONFIG` / `(CONFIG or {})` = global env-derived CONFIG, never
    overwritten by per-org payloads (config_sync.py:500, :687 store per-org separately).
  * config_sync.py self._config/data log lines (:352/:353/:576-578/:695/:696) = logging only.
  * mcp_proxy.py:967-977 `data` = HTTP JSON from control /api/mcp-connector/internal/enabled-tools/
    (mcp_proxy.py:927-947); mcp_scan_orchestrator `enabled_info` = that dict -> HTTP channel.
  * middleware.py `payload` / main.py `auth_ctx` = API-key payload, not FirewallConfig.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
d1 = json.load(open(os.path.join(HERE, "01_firewallconfig_fields.json")))
d2 = json.load(open(os.path.join(HERE, "02_payload_key_read_contexts.json")))
ser = d1["serializer_fields"]
pmap = d1["payload_map"]  # payload_key -> [[fields], lineno]
f2p = {}
for pk, (fields, _ln) in pmap.items():
    for f in fields:
        f2p.setdefault(f, []).append(pk)

LOG_ONLY = {"gateway/ai_mesh_gateway/config_sync.py:352", "gateway/ai_mesh_gateway/config_sync.py:353",
            "gateway/ai_mesh_gateway/config_sync.py:576", "gateway/ai_mesh_gateway/config_sync.py:577",
            "gateway/ai_mesh_gateway/config_sync.py:578", "gateway/ai_mesh_gateway/config_sync.py:695",
            "gateway/ai_mesh_gateway/config_sync.py:696"}
DEAD_APPLY = {"gateway/ai_mesh_gateway/config_sync.py:558"}
NOT_FWCFG_RECV = ("payload",)  # middleware api-key payload


GLOBAL_SELF_CONFIG_FILES = ("llm_router.py", "retriever_stage.py", "scanner.py", "output_guard.py:966")
PER_ORG_PROPAGATION = [("gateway/ai_mesh_gateway/main.py", 13877, 13898),  # for _gk in (...): rag_policy[_gk] = _rag_oc[_gk]
                       ("gateway/ai_mesh_gateway/vector_routes.py", 210, 215)]  # _RAG_GUARDRAIL_KEYS -> oc[_k]


def _in_prop(loc):
    f, ln = loc.rsplit(":", 1)
    return any(f == pf and a <= int(ln) <= b for pf, a, b in PER_ORG_PROPAGATION)


def classify_key(pk):
    hits = d2.get(pk, [])
    reads = [h for h in hits if h["class"].startswith("READ") or (h["class"] == "TUPLE/LIST element" and _in_prop(h["loc"]))]
    reads = [h for h in reads if h["loc"] not in LOG_ONLY]
    reads = [h for h in reads if not h["loc"].endswith("middleware.py:163")]  # api-key payload, not FirewallConfig
    if not reads:
        return "NO_READ", []
    per_org, glob, http, dead = [], [], [], []
    for h in reads:
        line, loc, recv = h["line"], h["loc"], h["recv"]
        if loc in DEAD_APPLY:
            dead.append(h)
        elif "enabled_info" in line or re.match(r"gateway/ai_mesh_gateway/mcp_proxy.py:9[67]\d$", loc):
            http.append(h)
        elif _in_prop(loc):
            per_org.append(h)
        elif re.search(r"(^|[^_\w])CONFIG\.get\(\s*[\"']" + re.escape(pk), line) and not re.search(r"org_c(on)?fi?g\.get\(\s*[\"']" + re.escape(pk), line):
            glob.append(h)
        elif "self._config" in recv and any(x in loc for x in GLOBAL_SELF_CONFIG_FILES):
            glob.append(h)
        elif "gateway_config" in recv:
            glob.append(h)
        else:
            per_org.append(h)
    if per_org:
        return "READ_PER_ORG", per_org
    if http:
        return "READ_VIA_HTTP_ONLY", http
    if dead:
        return "READ_DEAD_PATH_ONLY", dead + glob
    return "READ_GLOBAL_CONFIG_ONLY", glob


rows = []
for f in ser:
    pks = f2p.get(f, [])
    if not pks:
        rows.append((f, "-", "NOT_EMITTED_IN_PAYLOAD", []))
        continue
    for pk in pks:
        cls, ev = classify_key(pk)
        rows.append((f, pk, cls, ev))

from collections import Counter
cnt = Counter(r[2] for r in rows)
print(f"serializer fields: {len(ser)}  payload keys: {len(pmap)}")
print("classification counts:", dict(cnt))
for cls in ("NOT_EMITTED_IN_PAYLOAD", "NO_READ", "READ_DEAD_PATH_ONLY", "READ_GLOBAL_CONFIG_ONLY", "READ_VIA_HTTP_ONLY", "READ_PER_ORG"):
    items = [r for r in rows if r[2] == cls]
    print(f"\n== {cls} ({len(items)})")
    for f, pk, _c, ev in items:
        evs = "; ".join(f"{h['loc'].replace('gateway/ai_mesh_gateway/', 'gw/')}[{h['recv'][-22:]}]" for h in ev[:3])
        print(f"   {f:36s} -> {pk:34s} {evs}")
