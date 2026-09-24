#!/usr/bin/env python3
"""claim2a_emit_count.py -- Claim 2 / lane a: EXACT number of log records that reach
ai_mesh_shared.redis_log_handler.RedisLogPublisher.emit() per v1 /v1/chat/completions request,
gateway logger at DEBUG (main.py:6730 as shipped) vs INFO (the proposed fix).

ONE COMMAND (each profile x level in its own fresh subprocess, then aggregate -> summary.json):

    cd "$SP/evidence/micro-claims/claim2/a" && \
      /home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python claim2a_emit_count.py all

One cell:   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$B/gateway:$B/shared:<this dir>/pydeps <python> \
              claim2a_emit_count.py run --profile P1 --level DEBUG [--no-shim]

Code under test: BASELINE tree $SP/baseline-2a657fad (PYTHONPATH=$B/gateway:$B/shared; provenance of
every loaded gateway/shared module is asserted and written to run_<cell>.json).
BASELINE DEFECT: 2a657fad main.py:4745 imports llm_router.catalog_row_is_display_alias, which 2a657fad
llm_router.py lacks (added in 2ed687a6) -> every chat request with a routing catalogue returns HTTP 500.
Cells run with a runtime shim that execs the verbatim 3 pure helpers from 2ed687a6 (baseline_defect/);
the *_noshim cells document the as-is behaviour.

Profiles (details + what is real vs stubbed: run_<cell>.json "singletons"):
  P1   test_openai_sdk_compat.py::_make_sdk_app VERBATIM (pytest MonkeyPatch), publishers attached with
       the statements of main.py:6716-6737 (ASGITransport never runs startup).
  P2   the REAL ASGI lifespan startup (main.py:6350-6878) on load_config() env defaults, all Redis ->
       one fakeredis FakeServer (+lupa for EVAL), org 'acme' seeded (auth key, model routing, EMPTY
       policy bundle signed by the control plane's signer; signing enforced as in prod), ENABLE_TIER2=false;
       only LLMRouter._execute_completion (the litellm network call) is stubbed.
  P2b  P2 + 2-policy bundle (injection BLOCK + email/phone REDACT, conftest compiled shape).
  P2t2 P2 but Tier-2 at its production default (ENABLE_TIER2 unset=true -> OUTPUT tier-2 scan runs):
       REAL BedrockClient/BedrockScanner, only the aiobotocore converse() transport faked (clean verdict).
Scenarios (prompt + upstream reply unique per request): s1 benign non-stream (~260-token prompt),
s2 benign stream (50-token stub stream), s3 PII (email+phone) non-stream, s4 prompt-injection (the
test's block prompt). 5 warm-up + 20 measured per scenario.
Safety: fakeredis only, *.invalid redis URL, socket guard blocks+records any TCP/DNS, AWS disabled,
PYTHON_DOTENV_DISABLED=1 (litellm would otherwise import a developer .env), no timing measured.
"""
from __future__ import annotations

import argparse, asyncio, hashlib, json, logging, os, statistics, subprocess, sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import claim2a_lib as L  # noqa: E402

UPSTREAM_SEEN: dict = {}


def _harness_env(profile: str, level: str, outdir: Path) -> dict:
    env = {"GATEWAY_REDIS_URL": L.REDIS_URL_SENTINEL,
           "BEDROCK_LOG_DIR": str(outdir / f"bedrock_logdir_{profile}_{level}"),
           "AWS_EC2_METADATA_DISABLED": "true", "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
           "AWS_CONFIG_FILE": "/dev/null", "AWS_DEFAULT_REGION": "ap-south-1",
           "TIER2_PROVIDER": "bedrock",  # conftest autouse _hermetic_tier2_provider
           "LITELLM_LOCAL_MODEL_COST_MAP": "True",
           # litellm/__init__.py:19-20 calls dotenv.load_dotenv() when LITELLM_MODE is unset ("DEV"); it
           # walks up from site-packages into the repo tree and would import a developer .env into
           # os.environ (observed: AWS_* appeared). Official python-dotenv switch -> no .env is loaded.
           "PYTHON_DOTENV_DISABLED": "1"}
    if profile in ("P2", "P2b"):
        # Tier-2 (Bedrock) OFF via the gateway's own process switch (scanner.py:1137; compose default is
        # true). Without it the OUTPUT tier-2 scan (output_guard.py:1010 -> scanner.py:2574) runs on every
        # non-stream response when the org leaves tier2_enabled unset. P1 keeps the fixture verbatim;
        # P2t2 keeps the production default (ENABLE_TIER2 unset == true) with Bedrock faked at the socket seam.
        env["ENABLE_TIER2"] = "false"
    if profile != "P1":
        # Prod shape: GATEWAY_POLICY_SIGNING_REQUIRED keeps its default (true) and a key is configured;
        # bundles are signed with the control plane's own signer (control/.../policy/signing.py).
        # Ephemeral random harness key; its value is never written to any output file.
        import secrets
        os.environ["POLICY_SIGNING_KEY"] = secrets.token_hex(32)  # noqa: S105 (harness-only)
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", "GOOGLE_API_KEY"):
        os.environ.pop(k, None)
    os.environ.update(env)
    if profile != "P1":
        env["POLICY_SIGNING_KEY"] = "<ephemeral random per run; value not recorded>"
    return env


def _sign_with_control_plane_signer(bundle: dict) -> dict:
    import importlib.util
    path = L.B / "control" / "ai_mesh_control" / "policy" / "signing.py"
    spec = importlib.util.spec_from_file_location("claim2a_control_policy_signing", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.sign_bundle(json.loads(json.dumps(bundle)))


def _provenance() -> dict:
    bad, ours = [], 0
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None) or ""
        if "/AI_Mesh_Firewall/gateway/ai_mesh_gateway" in f or "/AI_Mesh_Firewall/shared/" in f:
            bad.append((name, f))
        if f.startswith(str(L.B)):
            ours += 1
    import ai_mesh_gateway.main as gm, ai_mesh_shared.redis_log_handler as rlh
    return {"main_py": gm.__file__, "redis_log_handler_py": rlh.__file__, "modules_from_baseline": ours,
            "modules_from_LIVE_repo_tree": bad}


def _singletons(gm) -> dict:
    names = ["CONFIG_SYNC", "LLM_ROUTER", "POLICY_SYNC", "RATE_LIMITER", "INPUT_SCANNER", "REDIS_CLIENT",
             "TELEMETRY", "OUTPUT_GUARD", "CIRCUIT_BREAKER", "BEDROCK_EMBEDDER", "GROUNDING_GUARD",
             "RAG_PIPELINE", "AGENT_ID", "_emit_telemetry", "_audit_fire_and_forget"]
    out = {n: f"{type(getattr(gm, n, None)).__module__}.{type(getattr(gm, n, None)).__qualname__}" for n in names}
    for n in ("_emit_telemetry", "_audit_fire_and_forget"):
        out[n] = getattr(getattr(gm, n), "__qualname__", "?")
    ps = getattr(gm, "POLICY_SYNC", None)
    if ps is not None:
        out["POLICY_SYNC.is_loaded"], out["POLICY_SYNC.policy_count"] = ps.is_loaded, ps.policy_count
    og = getattr(gm, "OUTPUT_GUARD", None)
    if og is not None:
        out["OUTPUT_GUARD.grounding_guard"] = type(getattr(og, "_grounding_guard", None)).__name__
    lr = getattr(gm, "LLM_ROUTER", None)
    if lr is not None and "_execute_completion" in getattr(lr, "__dict__", {}):
        out["LLM_ROUTER._execute_completion"] = "STUB " + lr.__dict__["_execute_completion"].__qualname__
    rc = getattr(gm, "REDIS_CLIENT", None)
    if rc is not None:
        out["REDIS_CLIENT.pool.connection_class"] = rc.connection_pool.connection_class.__qualname__
    cfg = getattr(gm, "CONFIG", None) or {}
    out["CONFIG.subset"] = {k: cfg.get(k) for k in ("redis_url", "backend_url", "tier2_enabled", "enforcement_mode",
                                                     "kill_switch_enabled", "policy_cache_enabled", "telemetry_enabled",
                                                     "output_guard_enabled", "circuit_breaker_enabled", "rag_enabled",
                                                     "routing_enabled", "stream_preflight_fail_closed")}
    return out


def _logger_state() -> dict:
    out = {}
    for name in ("gateway", "bedrock", ""):
        lg = logging.getLogger(name)
        out[name or "root"] = {"level": logging.getLevelName(lg.level), "effective": logging.getLevelName(
            lg.getEffectiveLevel()), "propagate": lg.propagate,
            "handlers": [f"{type(h).__name__}(level={logging.getLevelName(h.level)})" for h in lg.handlers]}
    return out


async def _setup_p1(mp, gm):
    """tests/test_openai_sdk_compat.py::_make_sdk_app, statement for statement (+ conftest autouse)."""
    import fakeredis.aioredis
    from unittest.mock import AsyncMock, MagicMock
    from ai_mesh_gateway import middleware as gw_middleware
    from ai_mesh_gateway.scanner import InputScanner
    from ai_mesh_gateway.bedrock_client import reset_bedrock_client_for_tests
    reset_bedrock_client_for_tests()
    try:
        from bedrock_scanner import reset_tier2_factory_for_tests as _rf
    except ImportError:
        from ai_mesh_gateway.bedrock_scanner import reset_tier2_factory_for_tests as _rf
    _rf()
    auth_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    key_hash = hashlib.sha256(L.API_KEY.encode("utf-8")).hexdigest()
    await auth_redis.set(f"auth:apikey:{key_hash}", json.dumps(L._auth_payload()))

    async def _get_redis(self):
        return auth_redis

    mp.setattr(gw_middleware.AuthMiddleware, "_get_redis", _get_redis)
    config_sync = MagicMock()
    config_sync.get_config = MagicMock(return_value=dict(L.TEST_CONFIG))
    config_sync.get_model_routing = MagicMock(return_value=[dict(L.TEST_MODEL), dict(L.EMBED_MODEL)])
    config_sync.reload_models_now = AsyncMock()
    config_sync.get_fallback_chains = MagicMock(return_value={"chains": {}, "per_primary": {}})
    llm_router = MagicMock()

    async def _acompletion(body, redacted_prompt=None, **kw):
        _note_upstream(body, redacted_prompt, **kw)
        return await L._fake_completion(body, redacted_prompt, **kw)

    async def _acompletion_stream(body, redacted_prompt=None, metrics=None, **kw):
        _note_upstream(body, redacted_prompt, **kw)
        async for f in L._fake_stream(body, redacted_prompt, metrics, **kw):
            yield f

    llm_router.acompletion = AsyncMock(side_effect=_acompletion)
    llm_router.acompletion_stream = _acompletion_stream
    llm_router.aembedding = AsyncMock(side_effect=L._fake_embedding)
    llm_router.get_model_list = MagicMock(return_value=[
        {"id": "gpt-4o-mini", "object": "model", "created": 1704067200, "owned_by": "openai"}])
    llm_router.estimate_prompt_tokens = MagicMock(return_value=500)
    mp.setattr(gm, "CONFIG", dict(L.TEST_CONFIG))
    mp.setattr(gm, "CONFIG_SYNC", config_sync)
    mp.setattr(gm, "LLM_ROUTER", llm_router)
    mp.setattr(gm, "INPUT_SCANNER", InputScanner(thread_pool_size=2))
    mp.setattr(gm, "AGENT_ID", None)
    mp.setattr(gm, "POLICY_SYNC", None)
    mp.setattr(gm, "RATE_LIMITER", None)
    mp.setattr(gm, "CIRCUIT_BREAKER", None)
    mp.setattr(gm, "REDIS_CLIENT", None)
    mp.setattr(gm, "TELEMETRY", None)
    mp.setattr(gm, "OUTPUT_GUARD", None)
    mp.setattr(gm, "_emit_telemetry", lambda **_kw: None)
    mp.setattr(gm, "_audit_fire_and_forget", lambda **_kw: None)
    return auth_redis


def _note_upstream(body, redacted_prompt=None, **kw):
    key = L.HARNESS_CTX.get()
    if key is None:
        return
    rp = redacted_prompt if redacted_prompt is not None else kw.get("redacted_content")
    txt = json.dumps(body.get("messages") or body.get("input") or "", default=str)
    UPSTREAM_SEEN[key["req_seq"]] = {"raw_email_in_upstream_messages": "jane.doe@example.com" in txt,
                                     "redacted_prompt_passed": rp is not None}


async def _p2_execute_completion(kwargs):
    _note_upstream(kwargs)
    return await L._fake_execute_completion(kwargs)


async def _lifespan_start(app) -> dict:
    rq, sq = asyncio.Queue(), asyncio.Queue()

    async def receive():
        return await rq.get()

    async def send(m):
        await sq.put(m)

    scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}, "state": {}}
    task = asyncio.create_task(app(scope, receive, send), name="claim2a-lifespan")
    await rq.put({"type": "lifespan.startup"})
    msg = await asyncio.wait_for(sq.get(), timeout=180)
    if msg.get("type") != "lifespan.startup.complete":
        raise SystemExit(f"lifespan startup failed: {msg}")
    return {"task": task, "rq": rq, "sq": sq, "startup_msg": msg}


async def _lifespan_stop(ls) -> dict:
    await ls["rq"].put({"type": "lifespan.shutdown"})
    try:
        return await asyncio.wait_for(ls["sq"].get(), timeout=60)
    except Exception as exc:  # noqa: BLE001
        return {"type": f"shutdown-wait-error {exc!r}"}


FAKE_BEDROCK: dict = {}


async def _setup_p2(gm, server, bundle, profile) -> dict:
    import fakeredis
    seed = fakeredis.FakeRedis(server=server, decode_responses=True)
    key_hash = hashlib.sha256(L.API_KEY.encode("utf-8")).hexdigest()
    # rate_limit_tpm raised so 100 sequential requests never trip the REAL per-key TPM limiter
    seed.set(f"auth:apikey:{key_hash}", json.dumps(L._auth_payload(org_slug=L.ORG, tpm=10_000_000)))
    seed.set(f"llm:model_configs:{L.ORG}", json.dumps({"routing": [L.TEST_MODEL, L.EMBED_MODEL]}))
    seed.set(f"policies:compiled:{L.ORG}", json.dumps(_sign_with_control_plane_signer(bundle)))
    import bedrock_client as bc_flat
    from ai_mesh_gateway import bedrock_client as bc_pkg
    if profile == "P2t2":
        # REAL BedrockClient singletons (both module identities); only the aiobotocore transport is fake.
        FAKE_BEDROCK["fake"] = fake = L._FakeAioBedrockRuntime()
        for m in (bc_flat, bc_pkg):
            inst = m.default_bedrock_client()
            inst._aio_client = fake               # _ensure_async_client() returns it (bedrock_client.py:406)
            inst.is_available = (lambda: True)    # startup credential preflight (one-time) without AWS
            FAKE_BEDROCK.setdefault("instances", []).append(f"{m.__name__}:{type(inst).__qualname__}")
    else:
        dummy = L._NoNetworkBedrock()
        for m in (bc_flat, bc_pkg):
            m.default_bedrock_client = (lambda _d=dummy: _d)
    return await _lifespan_start(gm.app)


async def _one_request(client, sc, seq: int) -> dict:
    import openai
    # unique per request (prod prompts vary; no cache can turn repeats into cheaper/quieter hits)
    kw = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": f"{sc['prompt']} (ref {seq})"}]}
    out: dict = {}
    try:
        if sc["stream"]:
            raw = await client.chat.completions.with_raw_response.create(**kw, stream=True)
            out["status"] = raw.http_response.status_code
            stream = raw.parse()  # LegacyAPIResponse.parse() is synchronous (returns AsyncStream)
            n, chars, zs = 0, 0, None
            async for ch in stream:
                if ch.choices and ch.choices[0].delta and ch.choices[0].delta.content:
                    n += 1; chars += len(ch.choices[0].delta.content)
                if (ch.model_extra or {}).get("zeroshield"):
                    zs = ch.model_extra["zeroshield"]
            out.update(content_chunks=n, content_chars=chars, action=(zs or {}).get("action"))
        else:
            raw = await client.chat.completions.with_raw_response.create(**kw)
            out["status"] = raw.http_response.status_code
            comp = raw.parse()
            zs = (comp.model_extra or {}).get("zeroshield") or {}
            content = comp.choices[0].message.content if comp.choices else ""
            out.update(action=zs.get("action"), content_chars=len(content or ""))
    except openai.APIStatusError as e:
        out["status"] = e.status_code
        try:
            body = json.loads(e.response.text)
        except Exception:  # noqa: BLE001
            body = {}
        err = body.get("error")
        out.update(code=body.get("code"), error_code=err.get("code") if isinstance(err, dict) else err,
                   blocked_by=body.get("blocked_by"), category=body.get("category"),
                   action=((body.get("zeroshield") or {}).get("action") or body.get("action")
                           or ("error" if e.status_code >= 500 else "blocked")))
    except Exception as e:  # noqa: BLE001
        out.update(status=None, exception=repr(e)[:300])
    return out


async def _drive(client, warmup: int, measured: int) -> list:
    outcomes, seq = [], 0
    for sc in L.SCENARIOS:
        for req_phase, n in (("warmup", warmup), ("measured", measured)):
            for i in range(n):
                seq += 1
                key = {"scenario": sc["name"], "req_phase": req_phase, "req_idx": i, "req_seq": seq}
                tok = L.HARNESS_CTX.set(key); L.STATE["window"] = key
                try:
                    out = await _one_request(client, sc, seq)
                finally:
                    L.STATE["window"] = None; L.HARNESS_CTX.reset(tok)
                outcomes.append({**key, "stream": sc["stream"], **out, **UPSTREAM_SEEN.get(seq, {})})
        await asyncio.sleep(0.2)  # drain fire-and-forget tasks (they keep their ctx attribution regardless)
    return outcomes


async def _async_main(profile, level, gm, server, args) -> dict:
    import httpx, openai
    info, mp, ls = {}, None, None
    if profile == "P1":
        from _pytest.monkeypatch import MonkeyPatch
        mp = MonkeyPatch()
        await _setup_p1(mp, gm)
        L._attach_publishers_like_main(L.REDIS_URL_SENTINEL, logging.getLevelName(level))
    else:
        L.STATE["phase"] = "startup"
        ls = await _setup_p2(gm, server, L.BUNDLES[profile], profile)
        L.STATE["phase"] = "setup"
        gm.LLM_ROUTER._execute_completion = _p2_execute_completion  # the ONLY upstream seam stubbed
    info["singletons"], info["logger_state"] = _singletons(gm), _logger_state()
    http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gm.app), base_url="http://testserver")
    client = openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=L.API_KEY, http_client=http_client,
                                max_retries=0)
    L.STATE["phase"] = "run"
    info["outcomes"] = await _drive(client, args.warmup, args.measured)
    await asyncio.sleep(0.5)
    L.STATE["phase"] = "shutdown"
    await client.close()
    if ls is not None:
        info["lifespan_shutdown"] = await _lifespan_stop(ls)
    if mp is not None:
        mp.undo()
    return info


def _stats(xs) -> dict:
    return {"n": len(xs), "min": min(xs), "median": statistics.median(xs), "max": max(xs),
            "mean": round(statistics.mean(xs), 3)} if xs else {"n": 0}


def _per_run_summary(profile, level, info) -> dict:
    by_req, root_by_req = defaultdict(list), defaultdict(list)
    for r in L.REC.pub:
        if r["attr"] != "unattributed" and r["scenario"]:
            by_req[(r["scenario"], r["req_phase"], r["req_idx"])].append(r)
    for r in L.REC.root:
        if r["attr"] != "unattributed" and r["scenario"]:
            root_by_req[(r["scenario"], r["req_phase"], r["req_idx"])].append(r)
    scen = {}
    for sc in L.SCENARIOS:
        nm = sc["name"]
        meas = [o for o in info["outcomes"] if o["scenario"] == nm and o["req_phase"] == "measured"]
        warm = [o for o in info["outcomes"] if o["scenario"] == nm and o["req_phase"] == "warmup"]
        recs = [by_req[(nm, "measured", o["req_idx"])] for o in meas]
        roots = [root_by_req[(nm, "measured", o["req_idx"])] for o in meas]
        k = max(len(meas), 1)
        cnt = lambda rs, f: [sum(1 for r in x if f(r)) for x in rs]  # noqa: E731
        scen[nm] = {
            "emits_per_request": _stats([len(x) for x in recs]),
            "gateway_pub_per_request": _stats(cnt(recs, lambda r: r["handler"] == "gateway_pub")),
            "bedrock_pub_per_request": _stats(cnt(recs, lambda r: r["handler"] == "bedrock_pub")),
            "non_debug_emits_per_request": _stats(cnt(recs, lambda r: r["levelno"] > logging.DEBUG)),
            "per_request_counts_measured": [len(x) for x in recs],
            "per_request_counts_warmup": [len(by_req[(nm, "warmup", o["req_idx"])]) for o in warm],
            "payload_bytes_per_request": _stats([sum(r["payload_bytes"] or 0 for r in x) for x in recs]),
            "unpublished_emits_total": sum(1 for x in recs for r in x if not r["published"]),
            "by_level_mean_per_req": {a: round(b / k, 3) for a, b in Counter(
                r["levelname"] for x in recs for r in x).most_common()},
            "by_logger_level_mean_per_req": {f"{a[0]}|{a[1]}": round(b / k, 3) for a, b in Counter(
                (r["logger"], r["levelname"]) for x in recs for r in x).most_common()},
            "by_src_mean_per_req": {f"{a[0]}|{a[1]}|{a[2]}": round(b / k, 3) for a, b in Counter(
                (r["src"], r["logger"], r["levelname"]) for x in recs for r in x).most_common()},
            "attribution_modes": dict(Counter(r["attr"] for x in recs for r in x)),
            "root_stream_per_request": _stats([len(x) for x in roots]),
            "root_stream_gateway_family_per_request": _stats(cnt(roots, lambda r: r["logger"].startswith("gateway"))),
            "root_stream_other_loggers": dict(Counter(r["logger"] for x in roots for r in x
                                                      if not r["logger"].startswith("gateway"))),
            "statuses": dict(Counter(str(o.get("status")) for o in meas)),
            "actions": dict(Counter(str(o.get("action")) for o in meas)),
            "codes": dict(Counter(f"{o.get('code')}/{o.get('error_code')}" for o in meas if o.get("code") or o.get("error_code"))),
            "upstream_saw_raw_email": dict(Counter(str(o.get("raw_email_in_upstream_messages")) for o in meas)),
            "exceptions": [o["exception"] for o in meas if o.get("exception")][:3],
        }
    first = by_req[(L.SCENARIOS[0]["name"], "warmup", 0)]
    s1m = [by_req[(L.SCENARIOS[0]["name"], "measured", i)] for i in range(len(
        [o for o in info["outcomes"] if o["scenario"] == L.SCENARIOS[0]["name"] and o["req_phase"] == "measured"]))]
    steady_srcs = {r["src"] for x in s1m for r in x}
    phase_ct = Counter(r["phase"] for r in L.REC.pub if r["attr"] == "unattributed")
    bg = [r for r in L.REC.pub if r["attr"] == "unattributed" and r["phase"] == "run"]
    one_time = {
        "publisher_emits_by_phase_outside_requests": dict(phase_ct),
        "startup_publisher_emits_by_logger_level": dict(Counter(
            f"{r['logger']}|{r['levelname']}" for r in L.REC.pub if r["phase"] == "startup")),
        "first_request_of_process": {"key": [L.SCENARIOS[0]["name"], "warmup", 0], "emits": len(first),
                                     "s1_measured_median": statistics.median([len(x) for x in s1m]) if s1m else None,
                                     "srcs_not_seen_in_s1_steady_state": sorted(
                                         {f"{r['src']}|{r['logger']}|{r['levelname']}" for r in first
                                          if r["src"] not in steady_srcs})},
        "background_during_run": {"count": len(bg), "by_logger_level_src": dict(Counter(
            f"{r['logger']}|{r['levelname']}|{r['src']}" for r in bg))},
        "records_created_during_import_by_logger_level": dict(Counter(
            {f"{lg}|{lv}": n for (ph, lg, lv), n in L.CREATED.items() if ph == "import"})),
    }
    return {"profile": profile, "level": level, "scenarios": scen, "one_time": one_time}


def cmd_run(args) -> int:
    outdir = Path(args.out).resolve(); outdir.mkdir(parents=True, exist_ok=True)
    tag = f"{args.profile}_{args.level}" + ("_noshim" if args.no_shim else "")
    env = _harness_env(args.profile, args.level, outdir)
    L._install_socket_guard()
    server = L._install_redis_fakes()
    L._install_record_factory_tally()
    try:
        import lupa  # fakeredis EVAL support (middleware last_used + RateLimiter scripts); from ./pydeps
        lua = f"lupa {getattr(lupa, '__version__', '?')} {lupa.__file__}"
    except ImportError:
        lua = "ABSENT (fakeredis EVAL unsupported)"
    L.STATE["phase"] = "import"
    from ai_mesh_gateway import main as gm
    L.GM = gm
    L.STATE["phase"] = "setup"
    prov = _provenance()
    if prov["modules_from_LIVE_repo_tree"]:
        raise SystemExit(f"provenance violation: {prov['modules_from_LIVE_repo_tree'][:5]}")
    shim = ["NOT APPLIED (--no-shim): baseline as-is"] if args.no_shim else L._apply_baseline_import_shim()
    pub_info, root_info = L._instrument_publisher(server), L._instrument_root(outdir, tag)
    L._intercept_gateway_setlevel(logging.DEBUG if args.level == "DEBUG" else logging.INFO)
    info = asyncio.run(_async_main(args.profile, args.level, gm, server, args))
    prov_end = _provenance()  # again after startup + all requests (P2* import most modules lazily)
    summ = _per_run_summary(args.profile, args.level, info)
    with open(outdir / f"records_pub_{tag}.jsonl", "w") as f:
        for r in L.REC.pub:
            f.write(json.dumps({"profile": args.profile, "level_cfg": args.level, **r}) + "\n")
    with open(outdir / f"records_root_{tag}.jsonl", "w") as f:
        for r in L.REC.root:
            f.write(json.dumps({"profile": args.profile, "level_cfg": args.level, **r}) + "\n")
    with open(outdir / f"replay_payloads_{tag}.jsonl", "w") as f:  # measured per-request records only
        for r in L.REC.pub:
            if r["attr"] != "unattributed" and r["req_phase"] == "measured" and r["published"]:
                f.write(json.dumps({"channel": r["channel"], "payload": r["payload"], "scenario": r["scenario"],
                                    "req_idx": r["req_idx"], "logger": r["logger"], "levelname": r["levelname"],
                                    "payload_bytes": r["payload_bytes"]}) + "\n")
    blocked = [{**b, "classification": L.classify_blocked(b)} for b in L.BLOCKED]
    _pfx = ("AWS", "GATEWAY", "BEDROCK", "LITELLM", "TIER2", "AIGUARDX", "GOOGLE", "OPENAI", "ANTHROPIC",
            "POLICY", "MONGO", "REDIS", "DATABASE", "CHROMA", "PINECONE", "MILVUS")
    foreign_env = sorted(k for k in os.environ if k.startswith(_pfx) and k not in env)  # NAMES only
    run = {**summ, "cell": tag, "env_set_by_harness": env, "env_names_present_not_set_by_harness": foreign_env,
           "lua_support": lua, "baseline_import_shim": shim,
           "provenance": prov, "provenance_end_of_run": prov_end, "publisher_instrumentation": pub_info,
           "root_handler": root_info, "gateway_setLevel_calls": L.SETLEVEL_CALLS, **{k: info[k] for k in (
               "singletons", "logger_state")}, "lifespan_shutdown": info.get("lifespan_shutdown"),
           "blocked_network_attempts": blocked,
           "blocked_network_attempts_unexpected": sum(1 for b in blocked if b["classification"] == "UNEXPECTED"),
           "redis_from_url_calls": dict(L.FROM_URL_CALLS),
           "fake_bedrock_converse_calls": (FAKE_BEDROCK["fake"].calls if "fake" in FAKE_BEDROCK else None),
           "fake_bedrock_instances": FAKE_BEDROCK.get("instances"),
           "total_publisher_emits": len(L.REC.pub), "total_root_records": len(L.REC.root),
           "outcomes": info["outcomes"]}
    (outdir / f"run_{tag}.json").write_text(json.dumps(run, indent=1, default=str))
    print(json.dumps({tag: {s: v["emits_per_request"] for s, v in summ["scenarios"].items()},
                      "blocked": len(blocked), "blocked_unexpected": run["blocked_network_attempts_unexpected"]},
                     default=str))
    return 0


def cmd_all(args) -> int:
    outdir = Path(args.out).resolve(); outdir.mkdir(parents=True, exist_ok=True)
    # baseline first; ./pydeps (lupa only) LAST so it can never shadow gateway/shared code
    env = {**os.environ, "PYTHONPATH": f"{L.B}/gateway:{L.B}/shared:{HERE / 'pydeps'}",
           "PYTHONDONTWRITEBYTECODE": "1"}  # never write __pycache__ into the shared baseline tree
    rcs, cells = {}, [(p, lv, False) for p in L.PROFILES for lv in L.LEVELS] + [("P1", lv, True) for lv in L.LEVELS]
    for p, lv, noshim in cells:
        tag = f"{p}_{lv}" + ("_noshim" if noshim else "")
        cmd = [L.PY, str(HERE / "claim2a_emit_count.py"), "run", "--profile", p, "--level", lv,
               "--warmup", str(args.warmup), "--measured", str(args.measured), "--out", str(outdir)]
        cmd += ["--no-shim"] if noshim else []
        with open(outdir / f"stdout_{tag}.log", "w") as so, open(outdir / f"stderr_{tag}.log", "w") as se:
            rcs[tag] = subprocess.run(cmd, env=env, stdout=so, stderr=se, cwd=str(outdir)).returncode
        print(f"{tag}: rc={rcs[tag]}", flush=True)
    summary = {"return_codes": rcs, "cells": {}}
    for tag in rcs:
        fp = outdir / f"run_{tag}.json"
        if fp.exists():
            summary["cells"][tag] = {k: v for k, v in json.loads(fp.read_text()).items() if k != "outcomes"}
    table = []
    for p, sfx in [(p, "") for p in L.PROFILES] + [("P1", "_noshim")]:
        d, i = summary["cells"].get(f"{p}_DEBUG{sfx}"), summary["cells"].get(f"{p}_INFO{sfx}")
        p = p + sfx
        for sc in L.SCENARIOS:
            nm = sc["name"]
            ds, is_ = (d or {}).get("scenarios", {}).get(nm, {}), (i or {}).get("scenarios", {}).get(nm, {})
            e, f = ds.get("emits_per_request", {}), is_.get("emits_per_request", {})
            table.append({"profile": p, "scenario": nm, "debug_min_med_max": [e.get("min"), e.get("median"), e.get("max")],
                          "info_min_med_max": [f.get("min"), f.get("median"), f.get("max")],
                          "debug_run_non_debug_median": ds.get("non_debug_emits_per_request", {}).get("median"),
                          "top_loggers_debug": list(ds.get("by_logger_level_mean_per_req", {}).items())[:4],
                          "status_debug": ds.get("statuses"), "action_debug": ds.get("actions"),
                          "root_stream_med_debug": ds.get("root_stream_per_request", {}).get("median"),
                          "root_stream_med_info": is_.get("root_stream_per_request", {}).get("median")})
    summary["table"] = table
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    for row in table:
        print(json.dumps(row, default=str))
    return 0 if all(v == 0 for v in rcs.values()) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "all"])
    ap.add_argument("--profile", choices=L.PROFILES, default="P1")
    ap.add_argument("--level", choices=L.LEVELS, default="DEBUG")
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--measured", type=int, default=20)
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--no-shim", action="store_true", help="run the baseline AS-IS (documents the 500 defect)")
    a = ap.parse_args()
    return cmd_run(a) if a.cmd == "run" else cmd_all(a)


if __name__ == "__main__":
    sys.exit(main())
