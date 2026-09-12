"""
AWS Bedrock client for platform ML (legacy Tier-2 Converse, routing adjudicator).

Tier-2 chat/MCP/RAG inference is selected by TIER2_PROVIDER (see
default_tier2_client). Bedrock Converse is the legacy T2 transport when
the provider is bedrock. Embeddings, LLM judge, and grounding stay on Bedrock.

Uses Converse for Anthropic/Global CRIS models and invoke_model for OpenAI-compat.

Authentication uses standard AWS IAM credentials from environment:
  - AWS_ACCESS_KEY_ID
  - AWS_SECRET_ACCESS_KEY

Configuration:
  - BEDROCK_REGION: AWS region (default: ap-south-1)
  - BEDROCK_TIER2_SCANNER_MODEL / BEDROCK_ADJUDICATOR_MODEL: Haiku 4.5 global profile
  - BEDROCK_MAX_TOKENS: decode cap (default: 256)
  - BEDROCK_TIMEOUT: request timeout in seconds (default: 60)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import resource
import threading
import time
from typing import Any, Dict, Optional

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:
    boto3 = None  # type: ignore[assignment]
    BotoConfig = None  # type: ignore[assignment,misc]

try:
    from bedrock_logger import (
        bedrock_log as BLOG, new_request_id,
        log_bedrock_request, log_bedrock_response, log_bedrock_error,
        log_health_check, log_metrics,
    )
except Exception:
    BLOG = None  # type: ignore[assignment]

LOG = logging.getLogger("gateway.bedrock_client")

# One boto3 bedrock-runtime client per process (gunicorn worker after fork).
_WORKER_CLIENT: Optional["BedrockClient"] = None
_WORKER_CLIENT_LOCK = threading.Lock()

# Safety ceiling only — not a programmer RPS cap. boto3 default is 10.
_MAX_POOL_CONNECTIONS_CEILING = 10000
_MIN_POOL_CONNECTIONS = 50


def _resolve_bedrock_max_pool_connections(
    explicit: Optional[int] = None,
) -> int:
    """Pool size from env, else RLIMIT_NOFILE. Never boto3's default 10."""
    if explicit is not None:
        try:
            if int(explicit) > 0:
                return int(explicit)
        except (TypeError, ValueError):
            pass
    raw = os.getenv("GATEWAY_BEDROCK_MAX_POOL_CONNECTIONS", "") or ""
    try:
        env_pool = int(str(raw).strip())
    except (TypeError, ValueError):
        env_pool = 0
    if env_pool > 0:
        return env_pool
    try:
        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        nofile = int(soft)
    except Exception:
        return _MAX_POOL_CONNECTIONS_CEILING
    infinity = getattr(resource, "RLIM_INFINITY", -1)
    if nofile <= 0 or nofile == infinity:
        return _MAX_POOL_CONNECTIONS_CEILING
    return min(max(nofile // 4, _MIN_POOL_CONNECTIONS), _MAX_POOL_CONNECTIONS_CEILING)

try:
    from .platform_models import (
        default_tier2_scanner_model,
        uses_converse_api,
    )
except ImportError:
    from platform_models import (
        default_tier2_scanner_model,
        uses_converse_api,
    )


def _amzn_request_id(response: Dict[str, Any]) -> str:
    """Read Amazon request id from Converse ResponseMetadata.

    Prefers ResponseMetadata["RequestId"], then HTTPHeaders
    ``x-amzn-request-id`` / ``x-amzn-requestid`` (case-insensitive).
    """
    meta = response.get("ResponseMetadata") or {}
    if not isinstance(meta, dict):
        return ""
    request_id = meta.get("RequestId")
    if isinstance(request_id, str) and request_id:
        return request_id
    headers = meta.get("HTTPHeaders") or {}
    if not isinstance(headers, dict):
        return ""
    for key, value in headers.items():
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        if lowered in ("x-amzn-request-id", "x-amzn-requestid"):
            return "" if value is None else str(value)
    return ""


def _normalize_converse_to_openai(raw_response: Dict[str, Any]) -> Dict[str, Any]:
    """Map Bedrock Converse output to OpenAI-style choices for shared parsers."""
    text_parts: list[str] = []
    output = raw_response.get("output") or {}
    message = output.get("message") or {}
    for block in message.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
    text = "".join(text_parts)
    usage = raw_response.get("usage") or {}
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {
            "prompt_tokens": usage.get("inputTokens", 0),
            "completion_tokens": usage.get("outputTokens", 0),
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
        },
    }


def _extract_prompt_preview(payload: Dict[str, Any]) -> str:
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            flat = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    flat.append(item["text"])
                elif isinstance(item, str):
                    flat.append(item)
            content = " ".join(flat)
        if not isinstance(content, str):
            content = str(content)
        parts.append(f"{role}: {content}")
    return " | ".join(parts)


def _extract_output_preview(raw_response: Dict[str, Any]) -> str:
    if not isinstance(raw_response, dict):
        return str(raw_response)
    # OpenAI-compatible (gpt-oss): {"choices":[{"message":{"content":"..."}}]}
    choices = raw_response.get("choices") or []
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        if isinstance(first, dict):
            msg = first.get("message") or first.get("delta") or {}
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    flat = []
                    for item in content:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            flat.append(item["text"])
                        elif isinstance(item, str):
                            flat.append(item)
                    return " ".join(flat)
    # Anthropic Messages API: {"content":[{"type":"text","text":"..."}]}
    top_content = raw_response.get("content")
    if isinstance(top_content, list):
        flat = []
        for item in top_content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                flat.append(item["text"])
            elif isinstance(item, str):
                flat.append(item)
        if flat:
            return " ".join(flat)
    return json.dumps(raw_response, default=str)


class BedrockClient:
    """
    AWS Bedrock runtime client using boto3 invoke_model.

    Expects:
      - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (standard AWS env vars)
      - BEDROCK_REGION: AWS region (e.g. ap-south-1, us-west-2)
    - BEDROCK_MODEL: model ID (e.g. global.anthropic.claude-haiku-4-5-20251001-v1:0)
    """

    def __init__(
        self,
        region: Optional[str] = None,
        model_id: Optional[str] = None,
        timeout: float = 60.0,
        max_pool_connections: Optional[int] = None,
    ) -> None:
        if boto3 is None:
            raise ImportError(
                "boto3 is required for Bedrock provider. "
                "Install with: pip install boto3"
            )

        self.region: str = region or os.getenv("BEDROCK_REGION", "ap-south-1")
        self.model_id: str = model_id or default_tier2_scanner_model()
        self.timeout: float = timeout
        self.max_pool_connections: int = _resolve_bedrock_max_pool_connections(
            max_pool_connections,
        )

        boto_config = BotoConfig(
            region_name=self.region,
            read_timeout=int(timeout),
            connect_timeout=5,
            retries={"max_attempts": 2, "mode": "standard"},
            max_pool_connections=self.max_pool_connections,
        )

        self._client = boto3.client("bedrock-runtime", config=boto_config)
        # Lazy per-worker async runtime (aiobotocore). Created on first
        # aconverse — never in the gunicorn master before fork.
        # Lock is created here (post-fork __init__) so the first burst cannot
        # race two asyncio.Lock() assignments and double-__aenter__.
        self._aio_client = None
        self._aio_cm = None
        self._aio_lock = asyncio.Lock()
        aws_key = os.getenv("AWS_ACCESS_KEY_ID", "")
        aws_key_prefix = aws_key[:4] if aws_key else "(not set)"
        LOG.info(
            "BedrockClient initialized: model=%s, region=%s, timeout=%.1fs, "
            "max_pool_connections=%d, aws_key_prefix=%s",
            self.model_id, self.region, self.timeout,
            self.max_pool_connections, aws_key_prefix,
        )

    def converse(
        self,
        *,
        model: str,
        system_text: str,
        user_text: str,
        max_tokens: int = 256,
        temperature: float = 0.0,
        call_site: str = "platform",
        request_id: Optional[str] = None,
        enable_prompt_cache: bool = False,
    ) -> Dict[str, Any]:
        """
        Invoke Bedrock Converse API (Anthropic / Global CRIS models).

        Returns dict with keys: raw, tokens_in, tokens_out, elapsed_s,
        model_id, call_site, api_method, ran_inference, x_amzn_request_id,
        gateway_request_id, phases (client_init_ms / converse_ms / parse_ms).
        ``raw`` is normalized to OpenAI chat-completion shape (choices).
        """
        effective_model = model or self.model_id
        reqid = request_id or (new_request_id() if BLOG else "")
        system_blocks: list[Dict[str, Any]] = [{"text": system_text}]
        if enable_prompt_cache and len(system_text) >= 4096:
            system_blocks.append({"cachePoint": {"type": "default"}})

        converse_input = {
            "modelId": effective_model,
            "system": system_blocks,
            "messages": [
                {"role": "user", "content": [{"text": user_text}]},
            ],
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }
        payload_bytes = len(json.dumps(converse_input, default=str))

        if BLOG:
            log_bedrock_request(
                request_id=reqid,
                model=effective_model,
                region=self.region,
                payload_bytes=payload_bytes,
                prompt_len=len(system_text) + len(user_text),
                truncated_len=len(user_text),
                max_tokens=max_tokens,
                prompt_preview=f"system: {system_text[:200]} | user: {user_text[:200]}",
                call_site=call_site,
                api_method="converse",
            )

        start = time.time()
        t_converse_0 = time.perf_counter()
        try:
            response = self._client.converse(**converse_input)
        except Exception as exc:
            elapsed = time.time() - start
            LOG.error(
                "Bedrock converse FAILED: model=%s, call_site=%s, elapsed=%.3fs, error=%s",
                effective_model, call_site, elapsed, exc,
            )
            if BLOG:
                log_bedrock_error(
                    request_id=reqid,
                    model=effective_model,
                    region=self.region,
                    elapsed_s=elapsed,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    payload_bytes=payload_bytes,
                )
            raise
        converse_ms = (time.perf_counter() - t_converse_0) * 1000.0
        elapsed = time.time() - start

        t_parse_0 = time.perf_counter()
        normalized_raw = _normalize_converse_to_openai(response)
        usage = response.get("usage") or {}
        tokens_in = int(usage.get("inputTokens") or 0)
        tokens_out = int(usage.get("outputTokens") or 0)
        output_preview = _extract_output_preview(normalized_raw)
        x_amzn_request_id = _amzn_request_id(response)
        parse_ms = (time.perf_counter() - t_parse_0) * 1000.0

        LOG.info(
            "Bedrock converse DONE: model=%s, call_site=%s, elapsed=%.3fs, "
            "tokens_in=%d, tokens_out=%d",
            effective_model, call_site, elapsed, tokens_in, tokens_out,
        )
        LOG.info(
            json.dumps(
                {
                    "call_site": call_site,
                    "gateway_request_id": reqid,
                    "x_amzn_request_id": x_amzn_request_id,
                    "tokens_out": tokens_out,
                    "client_init_ms": 0.0,
                    "converse_ms": converse_ms,
                    "parse_ms": parse_ms,
                },
                default=str,
            ),
        )

        if BLOG:
            log_bedrock_response(
                request_id=reqid,
                model=effective_model,
                region=self.region,
                elapsed_s=elapsed,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                success=True,
                response_keys=list(response.keys()),
                output_preview=output_preview,
                call_site=call_site,
                api_method="converse",
            )
            log_metrics(
                method="converse",
                model=effective_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                elapsed_s=elapsed,
                success=True,
                call_site=call_site,
            )

        return {
            "raw": normalized_raw,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "elapsed_s": elapsed,
            "model_id": effective_model,
            "call_site": call_site,
            "api_method": "converse",
            "ran_inference": True,
            "x_amzn_request_id": x_amzn_request_id,
            "gateway_request_id": reqid,
            "phases": {
                "client_init_ms": 0.0,
                "converse_ms": converse_ms,
                "parse_ms": parse_ms,
            },
        }

    async def _ensure_async_client(self):
        """One aiobotocore bedrock-runtime client per worker. Fail closed."""
        if getattr(self, "_aio_client", None) is not None:
            return self._aio_client
        lock = self._aio_lock
        if lock is None:
            # Tests that construct via __new__ must set this; production __init__ does.
            lock = asyncio.Lock()
            self._aio_lock = lock
        async with lock:
            if getattr(self, "_aio_client", None) is not None:
                return self._aio_client
            try:
                from aiobotocore.config import AioConfig
                from aiobotocore.session import get_session
            except ImportError as exc:
                raise ImportError(
                    "aiobotocore is required for native async Bedrock aconverse"
                ) from exc
            try:
                session = get_session()
                aio_config = AioConfig(
                    region_name=self.region,
                    read_timeout=int(self.timeout),
                    connect_timeout=5,
                    retries={"max_attempts": 2, "mode": "standard"},
                    max_pool_connections=self.max_pool_connections,
                )
                self._aio_cm = session.create_client(
                    "bedrock-runtime",
                    region_name=self.region,
                    config=aio_config,
                )
                self._aio_client = await self._aio_cm.__aenter__()
            except ImportError:
                raise
            except Exception as exc:
                self._aio_cm = None
                self._aio_client = None
                raise RuntimeError(
                    f"Failed to create Bedrock async client: {exc}"
                ) from exc
            return self._aio_client

    async def aclose(self) -> None:
        """Close the per-worker async HTTP session (gateway shutdown)."""
        cm = getattr(self, "_aio_cm", None)
        self._aio_cm = None
        self._aio_client = None
        if cm is not None:
            await cm.__aexit__(None, None, None)

    async def aconverse(
        self,
        *,
        model: str,
        system_text: str,
        user_text: str,
        max_tokens: int = 256,
        temperature: float = 0.0,
        call_site: str = "platform",
        request_id: Optional[str] = None,
        enable_prompt_cache: bool = False,
    ) -> Dict[str, Any]:
        """Native async Converse. Same return dict as ``converse``.

        Waits on sockets (aiobotocore), not a thread-pool executor.
        Import/session/client errors raise — never a fake allow.
        """
        aio_client = await self._ensure_async_client()
        if aio_client is None:
            raise RuntimeError("Bedrock async HTTP client unavailable")

        effective_model = model or self.model_id
        reqid = request_id or (new_request_id() if BLOG else "")
        system_blocks: list[Dict[str, Any]] = [{"text": system_text}]
        if enable_prompt_cache and len(system_text) >= 4096:
            system_blocks.append({"cachePoint": {"type": "default"}})

        converse_input = {
            "modelId": effective_model,
            "system": system_blocks,
            "messages": [
                {"role": "user", "content": [{"text": user_text}]},
            ],
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }
        payload_bytes = len(json.dumps(converse_input, default=str))

        if BLOG:
            log_bedrock_request(
                request_id=reqid,
                model=effective_model,
                region=self.region,
                payload_bytes=payload_bytes,
                prompt_len=len(system_text) + len(user_text),
                truncated_len=len(user_text),
                max_tokens=max_tokens,
                prompt_preview=f"system: {system_text[:200]} | user: {user_text[:200]}",
                call_site=call_site,
                api_method="converse",
            )

        start = time.time()
        t_converse_0 = time.perf_counter()
        try:
            response = await aio_client.converse(**converse_input)
        except Exception as exc:
            elapsed = time.time() - start
            LOG.error(
                "Bedrock aconverse FAILED: model=%s, call_site=%s, elapsed=%.3fs, error=%s",
                effective_model, call_site, elapsed, exc,
            )
            if BLOG:
                log_bedrock_error(
                    request_id=reqid,
                    model=effective_model,
                    region=self.region,
                    elapsed_s=elapsed,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    payload_bytes=payload_bytes,
                )
            raise
        converse_ms = (time.perf_counter() - t_converse_0) * 1000.0
        elapsed = time.time() - start

        t_parse_0 = time.perf_counter()
        normalized_raw = _normalize_converse_to_openai(response)
        usage = response.get("usage") or {}
        tokens_in = int(usage.get("inputTokens") or 0)
        tokens_out = int(usage.get("outputTokens") or 0)
        output_preview = _extract_output_preview(normalized_raw)
        x_amzn_request_id = _amzn_request_id(response)
        parse_ms = (time.perf_counter() - t_parse_0) * 1000.0

        LOG.info(
            "Bedrock aconverse DONE: model=%s, call_site=%s, elapsed=%.3fs, "
            "tokens_in=%d, tokens_out=%d",
            effective_model, call_site, elapsed, tokens_in, tokens_out,
        )
        LOG.info(
            json.dumps(
                {
                    "call_site": call_site,
                    "gateway_request_id": reqid,
                    "x_amzn_request_id": x_amzn_request_id,
                    "tokens_out": tokens_out,
                    "client_init_ms": 0.0,
                    "converse_ms": converse_ms,
                    "parse_ms": parse_ms,
                },
                default=str,
            ),
        )

        if BLOG:
            log_bedrock_response(
                request_id=reqid,
                model=effective_model,
                region=self.region,
                elapsed_s=elapsed,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                success=True,
                response_keys=list(response.keys()),
                output_preview=output_preview,
                call_site=call_site,
                api_method="converse",
            )
            log_metrics(
                method="converse",
                model=effective_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                elapsed_s=elapsed,
                success=True,
                call_site=call_site,
            )

        return {
            "raw": normalized_raw,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "elapsed_s": elapsed,
            "model_id": effective_model,
            "call_site": call_site,
            "api_method": "converse",
            "ran_inference": True,
            "x_amzn_request_id": x_amzn_request_id,
            "gateway_request_id": reqid,
            "phases": {
                "client_init_ms": 0.0,
                "converse_ms": converse_ms,
                "parse_ms": parse_ms,
            },
        }

    async def ascan_prompt(
        self,
        model: str,
        prompt_payload: Dict[str, Any],
        deployment_path: Optional[str] = None,
        request_id: Optional[str] = None,
        call_site: str = "tier2_scan",
    ) -> Dict[str, Any]:
        """Async scan_prompt. Converse models await aconverse (not to_thread)."""
        effective_model = model or self.model_id
        if uses_converse_api(effective_model):
            system_text = ""
            user_text = ""
            if isinstance(prompt_payload.get("system"), str):
                system_text = prompt_payload["system"]
            for msg in prompt_payload.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system" and isinstance(content, str):
                    system_text = content
                elif role == "user":
                    user_text = str(content)
            max_tokens = int(
                prompt_payload.get("max_tokens")
                or prompt_payload.get("maxTokens")
                or os.getenv("BEDROCK_MAX_TOKENS", "1024")
            )
            return await self.aconverse(
                model=effective_model,
                system_text=system_text,
                user_text=user_text,
                max_tokens=max_tokens,
                temperature=float(prompt_payload.get("temperature", 0.0)),
                call_site=call_site,
                request_id=request_id,
                enable_prompt_cache=bool(prompt_payload.get("enable_prompt_cache")),
            )

        return await asyncio.to_thread(
            self.scan_prompt,
            model,
            prompt_payload,
            deployment_path,
            request_id,
            call_site,
        )

    def scan_prompt(
        self,
        model: str,
        prompt_payload: Dict[str, Any],
        deployment_path: Optional[str] = None,
        request_id: Optional[str] = None,
        call_site: str = "tier2_scan",
    ) -> Dict[str, Any]:
        """
        Send prompt to Bedrock via invoke_model and return normalized result.

        Returns dict with keys: raw, tokens_in, tokens_out, elapsed_s.
        The ``raw`` value contains the parsed model response body which
        should follow the OpenAI chat-completion schema (with ``choices``).
        """
        effective_model = model or self.model_id
        if uses_converse_api(effective_model):
            system_text = ""
            user_text = ""
            if isinstance(prompt_payload.get("system"), str):
                system_text = prompt_payload["system"]
            for msg in prompt_payload.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system" and isinstance(content, str):
                    system_text = content
                elif role == "user":
                    user_text = str(content)
            max_tokens = int(
                prompt_payload.get("max_tokens")
                or prompt_payload.get("maxTokens")
                or os.getenv("BEDROCK_MAX_TOKENS", "1024")
            )
            return self.converse(
                model=effective_model,
                system_text=system_text,
                user_text=user_text,
                max_tokens=max_tokens,
                temperature=float(prompt_payload.get("temperature", 0.0)),
                call_site=call_site,
                request_id=request_id,
                enable_prompt_cache=bool(prompt_payload.get("enable_prompt_cache")),
            )

        body = json.dumps(prompt_payload)
        payload_bytes = len(body)
        reqid = request_id or (new_request_id() if BLOG else "")
        prompt_preview = _extract_prompt_preview(prompt_payload)

        LOG.info(
            "Bedrock invoke_model START: model=%s, region=%s, payload_bytes=%d",
            effective_model, self.region, payload_bytes,
        )

        # ── Dedicated Bedrock log: REQUEST ──
        if BLOG:
            log_bedrock_request(
                request_id=reqid,
                model=effective_model,
                region=self.region,
                payload_bytes=payload_bytes,
                prompt_len=payload_bytes,
                truncated_len=payload_bytes,
                deployment_path=deployment_path,
                prompt_preview=prompt_preview,
                call_site=call_site,
                api_method="invoke_model",
            )

        start = time.time()
        try:
            response = self._client.invoke_model(
                modelId=effective_model,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
        except Exception as exc:
            elapsed = time.time() - start
            LOG.error(
                "Bedrock invoke_model FAILED: model=%s, region=%s, elapsed=%.3fs, error=%s",
                effective_model, self.region, elapsed, exc,
            )
            # ── Dedicated Bedrock log: ERROR ──
            if BLOG:
                log_bedrock_error(
                    request_id=reqid,
                    model=effective_model,
                    region=self.region,
                    elapsed_s=elapsed,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    payload_bytes=payload_bytes,
                )
            raise
        elapsed = time.time() - start

        response_body = json.loads(response["body"].read())
        output_preview = _extract_output_preview(response_body)

        usage = response_body.get("usage") or {}
        # Support both OpenAI-style (prompt_tokens/completion_tokens) and
        # Anthropic-style (input_tokens/output_tokens) Bedrock response shapes.
        tokens_in = (
            usage.get("prompt_tokens")
            or usage.get("input_tokens")
            or usage.get("total_tokens")
            or 0
        )
        tokens_out = (
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or 0
        )

        LOG.info(
            "Bedrock invoke_model DONE: model=%s, elapsed=%.3fs, tokens_in=%d, "
            "tokens_out=%d, response_keys=%s",
            effective_model, elapsed, int(tokens_in), int(tokens_out),
            list(response_body.keys()),
        )

        # ── Dedicated Bedrock log: RESPONSE + METRICS ──
        if BLOG:
            log_bedrock_response(
                request_id=reqid,
                model=effective_model,
                region=self.region,
                elapsed_s=elapsed,
                tokens_in=int(tokens_in),
                tokens_out=int(tokens_out),
                success=True,
                response_keys=list(response_body.keys()),
                output_preview=output_preview,
                call_site=call_site,
                api_method="invoke_model",
            )
            log_metrics(
                method="invoke_model",
                model=effective_model,
                tokens_in=int(tokens_in),
                tokens_out=int(tokens_out),
                elapsed_s=elapsed,
                success=True,
                call_site=call_site,
            )

        return {
            "raw": response_body,
            "tokens_in": int(tokens_in),
            "tokens_out": int(tokens_out),
            "elapsed_s": elapsed,
            "model_id": effective_model,
            "call_site": call_site,
            "api_method": "invoke_model",
        }

    def is_available(self) -> bool:
        """Health check: verify Bedrock connectivity by listing foundation models."""
        LOG.info("Bedrock health check START: region=%s", self.region)
        start = time.time()
        try:
            bedrock_mgmt = boto3.client("bedrock", region_name=self.region)
            bedrock_mgmt.list_foundation_models()
            elapsed = time.time() - start
            LOG.info("Bedrock health check PASSED: region=%s", self.region)
            if BLOG:
                log_health_check(region=self.region, success=True, elapsed_s=elapsed)
            return True
        except Exception as exc:
            elapsed = time.time() - start
            LOG.warning(
                "Bedrock health check FAILED: region=%s, error=%s",
                self.region, exc,
            )
            if BLOG:
                log_health_check(region=self.region, success=False, elapsed_s=elapsed, error=str(exc))
            return False


def default_bedrock_client() -> BedrockClient:
    """Return the process-wide BedrockClient (one boto3 client per worker)."""
    global _WORKER_CLIENT
    if _WORKER_CLIENT is not None:
        return _WORKER_CLIENT
    with _WORKER_CLIENT_LOCK:
        if _WORKER_CLIENT is None:
            _WORKER_CLIENT = BedrockClient(
                region=os.getenv("BEDROCK_REGION", "ap-south-1"),
                model_id=default_tier2_scanner_model(),
                timeout=float(os.getenv("BEDROCK_TIMEOUT", "60.0")),
            )
        return _WORKER_CLIENT


def reset_bedrock_client_for_tests() -> None:
    """Drop the process singleton so tests stay hermetic."""
    global _WORKER_CLIENT
    _WORKER_CLIENT = None


async def aclose_default_bedrock_client() -> None:
    """Close the worker singleton's async HTTP session (lifespan shutdown)."""
    client = _WORKER_CLIENT
    if client is None:
        return
    closer = getattr(client, "aclose", None)
    if closer is not None:
        await closer()
