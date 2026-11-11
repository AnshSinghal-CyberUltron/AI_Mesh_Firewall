"""L27-1: change each console setting via the REAL control API (PUT /api/firewall/config/),
record status + body excerpt. Ports: control 127.0.0.1:38100 (integration stack, project amfint)."""
import json, sys, time
import httpx

CONTROL = "http://127.0.0.1:38100"
OUT = "docs/plans/evidence/2026-11-04-integration/l27-1-field-changes.jsonl"
tok = httpx.post(f"{CONTROL}/api/auth/token/", json={"email": "admin@zeroshield.io", "password": "Adm1n!Pass#2024"}).json()["access"]
H = {"authorization": f"Bearer {tok}"}
cur = httpx.get(f"{CONTROL}/api/firewall/config/", headers=H).json()
FIELDS = json.load(open("/tmp/field_kinds.json"))
CHOICE = {  # alternative valid values for enumerated fields
    "enforcement_mode": "monitor", "log_level": "minimal", "tier2_execution_mode": "async_post_llm",
    "mcp_ext_scan_action": "block", "hallucination_grounding_mode": "semantic",
    "output_pii_action": "block", "output_credential_action": "redact", "output_ip_leakage_action": "block",
    "output_policy_action": "flag", "output_hallucination_action": "block", "default_data_sensitivity": "restricted",
    "compliance_frameworks": ["SOC2", "HIPAA"], "allowed_models": "echo-1", "default_model": "echo-1",
    "blocked_keywords": "AcmeRivalGW26", "hallucination_grounding_model": "zeroshield-model",
}
NUM = {"requests_per_minute": 6000, "burst_limit": 5, "org_tpm_limit": 50000, "org_token_budget": 123456,
       "toxicity_threshold": 0.5, "prompt_injection_threshold": 0.6, "hallucination_grounding_threshold": 0.4,
       "max_response_tokens": 2048, "tier2_stream_hold_timeout_ms": 900, "rag_max_documents": 7,
       "rag_relevance_threshold": 0.6, "threat_score_threshold": 60, "retention_days": 120,
       "routing_risk_weight": 0.25, "routing_cost_weight": 0.25, "routing_latency_weight": 0.25,
       "routing_priority_weight": 0.25}


def change(field):
    if field in CHOICE:
        return CHOICE[field]
    if field in NUM:
        return NUM[field]
    v = cur[field]
    if isinstance(v, bool):
        return not v
    return v


rows = []
with open(OUT, "w") as fh:
    for field, (kind, *_rest) in FIELDS.items():
        if kind == "identity":
            continue
        new = change(field)
        t = time.time()
        r = httpx.put(f"{CONTROL}/api/firewall/config/", headers=H, json={field: new}, timeout=30)
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        plan = body.get("gateway_v2_plan") if isinstance(body, dict) else None
        rec = {"field": field, "kind": kind, "request": {"method": "PUT", "path": "/api/firewall/config/", "json": {field: new}},
               "status": r.status_code, "body_excerpt": (json.dumps(plan) if plan is not None else json.dumps(body))[:400],
               "ms": round((time.time() - t) * 1000)}
        fh.write(json.dumps(rec) + "\n")
        print(rec["status"], kind, field, rec["body_excerpt"][:140], flush=True)
        # restore so one field is tested at a time (a rejected PUT changes nothing)
        if r.status_code == 200:
            httpx.put(f"{CONTROL}/api/firewall/config/", headers=H, json={field: cur[field]}, timeout=30)
