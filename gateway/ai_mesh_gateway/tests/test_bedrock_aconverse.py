"""Task 12: native async Bedrock aconverse (sockets, not OS threads)."""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_mesh_gateway.bedrock_client import BedrockClient
from ai_mesh_gateway.scanner import InputScanner, ScanVerdict


MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def _bare_client() -> BedrockClient:
    client = BedrockClient.__new__(BedrockClient)
    client.region = "ap-south-1"
    client.model_id = MODEL
    client.timeout = 60.0
    client.max_pool_connections = 500
    client._client = MagicMock()
    client._aio_client = None
    client._aio_cm = None
    client._aio_lock = asyncio.Lock()
    return client


def _converse_json(*, request_id: str = "amzn-abc", text: str = '{"ok":true}'):
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": 10, "outputTokens": 4},
        "ResponseMetadata": {"RequestId": request_id},
    }


@pytest.mark.asyncio
async def test_aconverse_500_concurrent_does_not_spawn_500_threads():
    client = _bare_client()

    async def fake_converse(**_kwargs):
        await asyncio.sleep(0.05)
        return _converse_json()

    aio = MagicMock()
    aio.converse = fake_converse

    async def fake_ensure():
        return aio

    client._ensure_async_client = fake_ensure  # type: ignore[method-assign]

    before = threading.active_count()
    results = await asyncio.gather(
        *[
            client.aconverse(
                model=MODEL,
                system_text="sys",
                user_text=f"user-{i}",
                call_site="adjudicator",
                request_id=f"zs-conc-{i}",
            )
            for i in range(500)
        ]
    )
    after = threading.active_count()
    assert len(results) == 500
    assert all(r["ran_inference"] is True for r in results)
    assert (after - before) < 100, (
        f"aconverse spawned too many threads: before={before} after={after} "
        f"delta={after - before} (expected native async, not ThreadPoolExecutor)"
    )


@pytest.mark.asyncio
async def test_aconverse_return_shape_matches_converse():
    client = _bare_client()

    async def fake_converse(**_kwargs):
        return _converse_json(request_id="amzn-shape")

    aio = MagicMock()
    aio.converse = fake_converse

    async def fake_ensure():
        return aio

    client._ensure_async_client = fake_ensure  # type: ignore[method-assign]

    out = await client.aconverse(
        model=MODEL,
        system_text="sys",
        user_text="user",
        call_site="adjudicator",
        request_id="zs-test-shape",
    )
    assert out["ran_inference"] is True
    assert out["x_amzn_request_id"] == "amzn-shape"
    assert out["gateway_request_id"] == "zs-test-shape"
    assert out["phases"]["converse_ms"] >= 0
    assert out["phases"]["parse_ms"] >= 0
    assert "choices" in out["raw"]
    assert out["tokens_in"] == 10
    assert out["tokens_out"] == 4
    assert out["api_method"] == "converse"
    assert out["call_site"] == "adjudicator"


@pytest.mark.asyncio
async def test_ascan_prompt_uses_aconverse_for_converse_models():
    client = _bare_client()
    captured: dict = {}

    async def fake_aconverse(**kwargs):
        captured["kwargs"] = kwargs
        return {
            "raw": {"choices": [{"message": {"content": '{"ok":true}'}}]},
            "tokens_in": 1,
            "tokens_out": 1,
            "elapsed_s": 0.01,
            "ran_inference": True,
            "x_amzn_request_id": "amzn-scan",
            "gateway_request_id": "zs-scan",
            "phases": {"client_init_ms": 0.0, "converse_ms": 1.0, "parse_ms": 0.1},
            "model_id": MODEL,
            "call_site": "tier2_scan",
            "api_method": "converse",
        }

    client.aconverse = fake_aconverse  # type: ignore[method-assign]

    with patch("ai_mesh_gateway.bedrock_client.asyncio.to_thread") as to_thread:
        out = await client.ascan_prompt(
            model=MODEL,
            prompt_payload={
                "system": "sys",
                "messages": [{"role": "user", "content": "scan me"}],
                "max_tokens": 128,
            },
            request_id="zs-ascan",
            call_site="tier2_scan",
        )

    to_thread.assert_not_called()
    assert captured["kwargs"]["model"] == MODEL
    assert captured["kwargs"]["user_text"] == "scan me"
    assert captured["kwargs"]["system_text"] == "sys"
    assert captured["kwargs"]["call_site"] == "tier2_scan"
    assert out["ran_inference"] is True


@pytest.mark.asyncio
async def test_aconverse_missing_http_client_fails_closed():
    client = _bare_client()

    async def boom():
        raise RuntimeError("Bedrock async HTTP client unavailable")

    client._ensure_async_client = boom  # type: ignore[method-assign]

    with pytest.raises((ImportError, RuntimeError)):
        await client.aconverse(
            model=MODEL,
            system_text="sys",
            user_text="user",
            call_site="adjudicator",
        )


@pytest.mark.asyncio
async def test_scan_prompt_with_tier2_uses_ascan_not_executor():
    scanner = InputScanner.__new__(InputScanner)
    scanner._config = {}
    scanner.tier2_enabled = True
    scanner._tier2_cache = {}
    scanner._tier2_cache_ttl = 0.0
    scanner._tier2_cache_max = 0
    scanner._tier2_sample_rate = 1.0
    scanner._bedrock_executor = MagicMock()

    ascan_result = {
        "owasp_llm": {},
        "owasp_mcp": {},
        "owasp_agentic": {},
        "pii": {},
        "llm_guard": {"score": 0.0, "is_valid": True, "degraded": False},
        "meta": {
            "request_id": "zs-t2",
            "recommended_action": "allow",
            "raw_findings": [],
            "decision_reason": "model_recommendation",
        },
    }
    mock_bs = MagicMock()
    mock_bs.model = MODEL
    mock_bs.ascan = AsyncMock(return_value=ascan_result)
    scanner._bedrock_scanner = mock_bs

    async def fake_t1(*_a, **_k):
        return ScanVerdict(action="allow", tier="tier_1")

    scanner.scan_prompt = fake_t1  # type: ignore[method-assign]
    scanner._bedrock_scan_sync = MagicMock(
        side_effect=AssertionError("must not use sync bedrock executor path"),
    )

    with patch.object(
        asyncio.BaseEventLoop,
        "run_in_executor",
        side_effect=AssertionError("Bedrock leg must not use run_in_executor"),
    ):
        verdict = await scanner.scan_prompt_with_tier2(
            "hello world, what is 2+2?",
            request_id="zs-t2",
            org_tier2_override=True,
            org_slug="zeroshield",
        )

    mock_bs.ascan.assert_awaited()
    scanner._bedrock_scan_sync.assert_not_called()
    assert verdict.action in ("allow", "flag")
    assert verdict.action != "block"


@pytest.mark.asyncio
async def test_ascan_reraises_import_and_runtime_errors():
    from ai_mesh_gateway.bedrock_scanner import BedrockScanner

    scanner = BedrockScanner.__new__(BedrockScanner)
    scanner.model = MODEL
    scanner.client = MagicMock()
    scanner._prepare_scan = MagicMock(return_value=("zs-fc", 0.0, {"messages": []}, None))
    scanner._scan_client_error = MagicMock(
        side_effect=AssertionError("must not swallow ImportError/RuntimeError"),
    )

    scanner.client.ascan_prompt = AsyncMock(side_effect=ImportError("aiobotocore missing"))
    with pytest.raises(ImportError):
        await scanner.ascan("prompt")
    scanner._scan_client_error.assert_not_called()

    scanner.client.ascan_prompt = AsyncMock(side_effect=RuntimeError("session failed"))
    with pytest.raises(RuntimeError):
        await scanner.ascan("prompt")
    scanner._scan_client_error.assert_not_called()


@pytest.mark.asyncio
async def test_scan_prompt_with_tier2_does_not_flag_forward_on_session_error():
    scanner = InputScanner.__new__(InputScanner)
    scanner._config = {}
    scanner.tier2_enabled = True
    scanner._tier2_cache = {}
    scanner._tier2_cache_ttl = 0.0
    scanner._tier2_cache_max = 0
    scanner._tier2_sample_rate = 1.0
    scanner._deobfuscate_text = lambda text: text  # type: ignore[method-assign]

    mock_bs = MagicMock()
    mock_bs.model = MODEL
    mock_bs.ascan = AsyncMock(side_effect=RuntimeError("Failed to create Bedrock async client"))
    scanner._bedrock_scanner = mock_bs

    async def fake_t1(*_a, **_k):
        return ScanVerdict(action="allow", tier="tier_1")

    scanner.scan_prompt = fake_t1  # type: ignore[method-assign]

    with patch("ai_mesh_gateway.scanner.BREAKER") as breaker:
        breaker.allow.return_value = True
        with pytest.raises(RuntimeError, match="Bedrock async client"):
            await scanner.scan_prompt_with_tier2(
                "hello world",
                request_id="zs-fail-closed",
                org_tier2_override=True,
                org_slug="zeroshield",
            )


@pytest.mark.asyncio
async def test_ensure_async_client_singleflight():
    client = _bare_client()
    enters = {"n": 0}

    class _CM:
        async def __aenter__(self):
            enters["n"] += 1
            await asyncio.sleep(0.05)
            return MagicMock()

        async def __aexit__(self, *_a):
            return None

    session = MagicMock()
    session.create_client.side_effect = lambda *_a, **_k: _CM()

    with patch("aiobotocore.session.get_session", return_value=session), patch(
        "aiobotocore.config.AioConfig", return_value=MagicMock()
    ):
        await asyncio.gather(*[client._ensure_async_client() for _ in range(20)])

    assert enters["n"] == 1
    assert session.create_client.call_count == 1


@pytest.mark.asyncio
async def test_tier2_ttl_zero_does_not_reuse_cached_ascan():
    scanner = InputScanner.__new__(InputScanner)
    scanner._config = {}
    scanner.tier2_enabled = True
    scanner._tier2_cache = {}
    scanner._tier2_cache_ttl = 0.0
    scanner._tier2_cache_max = 100
    scanner._tier2_sample_rate = 1.0
    scanner._deobfuscate_text = lambda text: text  # type: ignore[method-assign]

    ascan_result = {
        "owasp_llm": {},
        "owasp_mcp": {},
        "owasp_agentic": {},
        "pii": {},
        "llm_guard": {"score": 0.0, "is_valid": True, "degraded": False},
        "meta": {
            "request_id": "zs-ttl",
            "recommended_action": "allow",
            "raw_findings": [],
            "decision_reason": "model_recommendation",
        },
    }
    mock_bs = MagicMock()
    mock_bs.model = MODEL
    mock_bs.ascan = AsyncMock(return_value=ascan_result)
    scanner._bedrock_scanner = mock_bs

    async def fake_t1(*_a, **_k):
        return ScanVerdict(action="allow", tier="tier_1")

    scanner.scan_prompt = fake_t1  # type: ignore[method-assign]

    with patch("ai_mesh_gateway.scanner.BREAKER") as breaker:
        breaker.allow.return_value = True
        await scanner.scan_prompt_with_tier2("identical prompt", org_tier2_override=True)
        await scanner.scan_prompt_with_tier2("identical prompt", org_tier2_override=True)

    assert mock_bs.ascan.await_count == 2


