"""
LLM routing via LiteLLM.
Provides async completion (streaming and non-streaming) with error mapping,
plus smart multi-dimensional model selection.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, TYPE_CHECKING

if TYPE_CHECKING:
    from stream_orchestration import StreamRunMetrics

import litellm
from litellm import Router as LiteLLMRouter

from ai_mesh_shared.llm_model_crypto import decrypt_api_key
from ai_mesh_shared.litellm_byok import (
    apply_bedrock_byok_credentials,
    apply_bedrock_env_credentials,
    normalize_litellm_params,
    resolve_bedrock_model_id,
)

from litellm.exceptions import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadRequestError,
    BudgetExceededError,
    ContentPolicyViolationError,
    ContextWindowExceededError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

LOG = logging.getLogger("gateway.llm_router")

# Map LiteLLM exceptions to HTTP status codes
_EXCEPTION_STATUS_MAP = {
    BadRequestError: 400,
    AuthenticationError: 401,
    NotFoundError: 404,
    ContentPolicyViolationError: 400,
    ContextWindowExceededError: 400,
    RateLimitError: 429,
    BudgetExceededError: 429,
    Timeout: 504,
    APIConnectionError: 502,
    ServiceUnavailableError: 503,
    APIError: 502,
}

# OpenAI-compatible params to forward to litellm
_PASSTHROUGH_PARAMS = (
    "temperature", "top_p", "max_tokens", "stop",
    "presence_penalty", "frequency_penalty", "tools",
    "tool_choice", "response_format", "seed", "n",
)

# Keep compatibility with historical or UI-facing aliases.
_MODEL_ALIAS_MAP = {
    "bedrock-gpt-oss-120b-long-context": "bedrock-gpt-oss-120b",
}

_SENSITIVITY_ORDER = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

_DEFAULT_ROUTING_WEIGHTS = {
    "risk": 0.30,
    "cost": 0.20,
    "latency": 0.20,
    "priority": 0.30,
}


@dataclass
class ModelSelection:
    """Result of smart model selection."""

    model_name: str = ""
    model_id: str = ""
    score: float = 0.0
    reason: str = ""
    fallback_chain: list[str] = field(default_factory=list)
    requested_model: str = ""
    decision_source: str = "weighted"
    evaluator_model: str = ""
    policy_summary: str = ""
    decision_factors: list[str] = field(default_factory=list)
    candidate_count: int = 0

class LLMRouter:
    """Async LLM router backed by LiteLLM."""

    def __init__(self, config: dict):
        self._config = config
        self._router: LiteLLMRouter | None = None
        self._active_model_names: list[str] = []

        #global litellm settings
        litellm.drop_params = config.get("litellm_drop_params", True)
        litellm.request_timeout = config.get("litellm_request_timeout", 120)
        litellm.num_retries = config.get("litellm_num_retries", 2)
        litellm.ssl_verify = config.get("litellm_ssl_verify", os.environ.get("SSL_VERIFY", "true").lower() not in ("false", "0", "no"))
        
        org_only = bool(config.get("org_only_inference", True))
        if config.get("upstream_llm_url") and not org_only:
            self._init_legacy(config)
        elif org_only:
            LOG.info(
                "Org-only inference enabled. LiteLLM router models load from "
                "Redis (llm:model_configs:{org_slug}) after Control Plane sync."
            )
        else:
            LOG.warning(
                "Org-only inference disabled without upstream URL; "
                "router starts empty until Redis model reload."
            )

    def _set_active_model_names(self, model_list: list[dict]) -> None:
        self._active_model_names = [
            self._normalize_model_alias(entry.get("model_name", ""))
            for entry in model_list
            if entry.get("model_name")
        ]

    def _resolve_runtime_model(self, requested_model: str) -> str:
        if not self._active_model_names:
            return requested_model
        if requested_model in self._active_model_names:
            return requested_model

        preferred_default = self._normalize_model_alias(self._config.get("litellm_default_model", "") or "")
        if preferred_default and preferred_default in self._active_model_names:
            return preferred_default

        return self._active_model_names[0]

    def _init_legacy(self, config: dict):
        """Backward-compat: use single upstream_llm_url as custom OpenAI-like base."""
        litellm.api_base = config["upstream_llm_url"] + "/v1"
        if config.get("llm_api_key"):
            litellm.api_key = config["llm_api_key"]
            LOG.info(f"LiteLLM legacy mode: using single upstream URL {config['upstream_llm_url']} with OpenAI-compatible API and provided API key")

    def _build_fallbacks(self, model_list: list[dict] | None = None) -> list[dict] | None:
        fallback_models = self._config.get("litellm_fallback_models", "")
        if isinstance(fallback_models, str):
            fallback_models = [m.strip() for m in fallback_models.split(",") if m.strip()]
        fallback_models = [self._normalize_model_alias(m) for m in (fallback_models or []) if m]

        # Backward compatibility for existing unit tests and callers.
        if not model_list:
            return [{"model": m} for m in fallback_models] if fallback_models else None

        # LiteLLM Router expects: [{"primary-model": ["fallback-a", "fallback-b"]}]
        model_names = [
            self._normalize_model_alias(entry.get("model_name", ""))
            for entry in model_list
            if entry.get("model_name")
        ]
        if not model_names:
            return None

        configured_default = self._normalize_model_alias(self._config.get("litellm_default_model", "") or "")
        fallback_map = []
        for primary in model_names:
            chain: list[str] = []

            # 1) explicit configured fallbacks first
            for model in fallback_models:
                if model != primary and model in model_names and model not in chain:
                    chain.append(model)

            # 2) configured default model (if different)
            if (
                configured_default
                and configured_default != primary
                and configured_default in model_names
                and configured_default not in chain
            ):
                chain.append(configured_default)

            # 3) finally add all remaining models to avoid missing model-group fallback maps
            for model in model_names:
                if model != primary and model not in chain:
                    chain.append(model)

            if chain:
                fallback_map.append({primary: chain})
        return fallback_map or None

    @staticmethod
    def _validate_reload_model_entry(entry: dict) -> tuple[bool, str]:
        """Validate a model entry before feeding it to LiteLLM Router reload."""
        try:
            params = entry.get("litellm_params") if isinstance(entry, dict) else None
            model_id = str((params or {}).get("model") or "").strip()
            model_name = str(entry.get("model_name") or "").strip() if isinstance(entry, dict) else ""
            if not model_id or not model_name:
                return False, "missing model_name or litellm_params.model"

            validator = getattr(litellm, "get_llm_provider", None)
            if callable(validator):
                validator(model=model_id)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _filter_valid_reload_models(self, model_list: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
        valid_models: list[dict] = []
        invalid_models: list[tuple[str, str]] = []
        for entry in model_list:
            prepared = self._prepare_reload_entry(entry)
            ok, reason = self._validate_reload_model_entry(prepared)
            if ok:
                valid_models.append(prepared)
            else:
                model_name = ""
                model_id = ""
                if isinstance(entry, dict):
                    model_name = str(entry.get("model_name") or "")
                    model_id = str((entry.get("litellm_params") or {}).get("model") or "")
                invalid_models.append((model_name or model_id or "unknown", reason))
        return valid_models, invalid_models

    @staticmethod
    def _prepare_reload_entry(entry: dict) -> dict:
        """Decrypt encrypted API keys in-memory for LiteLLM; never persist plaintext."""
        if not isinstance(entry, dict):
            return entry
        params = dict(entry.get("litellm_params") or {})
        provider = str(entry.get("provider") or params.get("provider") or "")
        bedrock_region = (
            str(params.get("aws_region_name") or "").strip()
            or os.environ.get("BEDROCK_REGION", "")
            or os.environ.get("AWS_DEFAULT_REGION", "")
        )
        encrypted = params.pop("api_key_encrypted", None)
        if encrypted and not params.get("api_key"):
            fallback_secret = os.environ.get("DJANGO_SECRET_KEY", "") or os.environ.get(
                "SECRET_KEY", ""
            )
            decrypted = decrypt_api_key(
                str(encrypted),
                fallback_secret=fallback_secret,
            )
            if decrypted:
                if provider.lower() == "aws_bedrock":
                    params = apply_bedrock_byok_credentials(
                        params,
                        decrypted,
                        env_access_key=os.environ.get("AWS_ACCESS_KEY_ID", ""),
                        env_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
                        default_region=bedrock_region,
                    )
                else:
                    params["api_key"] = decrypted
        elif provider.lower() == "aws_bedrock":
            params = apply_bedrock_env_credentials(
                params,
                env_access_key=os.environ.get("AWS_ACCESS_KEY_ID", ""),
                env_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
                default_region=bedrock_region,
            )
        if provider.lower() == "aws_bedrock" and params.get("model"):
            params["model"] = resolve_bedrock_model_id(
                str(params.get("model") or ""),
                region=bedrock_region,
            )
        provider = str(entry.get("provider") or params.pop("provider", "") or "")
        params = normalize_litellm_params(params, provider=provider)
        return {**entry, "litellm_params": params}

    @staticmethod
    def _normalize_model_alias(model: str) -> str:
        return _MODEL_ALIAS_MAP.get(model, model)

    def _default_fallback_model(self) -> str:
        configured = self._config.get("litellm_default_model", "")
        return self._normalize_model_alias(configured or "gpt-4o-mini")

    async def _execute_completion(self, kwargs: dict):
        if self._router:
            return await self._router.acompletion(**kwargs)
        return await litellm.acompletion(**kwargs)

    async def _execute_embedding(self, kwargs: dict):
        if self._router:
            return await self._router.aembedding(**kwargs)
        return await litellm.aembedding(**kwargs)

    def _apply_redaction(self, body: dict, redacted_content: str | None) -> dict:
        """Replace last user message content with redacted version."""
        if redacted_content is None or not body.get("messages"):
            return body
        messages = list(body["messages"])
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                messages = (
                    messages[:i]
                    + [{"role": "user", "content": redacted_content}]
                    + messages[i + 1:]
                )
                break
        return {**body, "messages": messages}

    def _build_kwargs(
        self,
        body: dict,
        stream: bool,
        inference_allowlist: set[str] | None = None,
    ) -> dict:
        """Build kwargs for litellm.acompletion from request body."""
        requested_model = body.get("model") or self._default_fallback_model()
        requested_model = self._normalize_model_alias(requested_model)
        if inference_allowlist and requested_model not in inference_allowlist:
            if len(inference_allowlist) == 1:
                model = next(iter(inference_allowlist))
            else:
                model = requested_model
        else:
            model = self._resolve_runtime_model(requested_model)
        if inference_allowlist and model not in inference_allowlist:
            model = requested_model
        if model != requested_model:
            LOG.warning(
                "Requested model '%s' is not active in router model groups; remapping to '%s'",
                requested_model,
                model,
            )
        messages = body.get("messages") or body.get("input") or []
        kwargs = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        for param in _PASSTHROUGH_PARAMS:
            if param in body:
                kwargs[param] = body[param]
        return kwargs

    def _pop_inference_allowlist(self, body: dict) -> set[str] | None:
        raw = body.pop("_inference_allowlist", None)
        if not raw:
            return None
        return {self._normalize_model_alias(str(m)) for m in raw if m}

    @staticmethod
    def _pop_compliant_fallback_chain(body: dict) -> list[str]:
        raw = body.pop("_compliant_fallback_chain", None)
        if not raw:
            return []
        return [str(m) for m in raw if m]

    async def acompletion(
            self,
            body: dict,
            redacted_content: str | None = None,
    ) -> tuple[int, dict]:
        """Non-streaming completion. Returns (http_status, response_dict)."""
        body = self._apply_redaction(body, redacted_content)
        allowlist = self._pop_inference_allowlist(body)
        compliant_chain = self._pop_compliant_fallback_chain(body)
        kwargs = self._build_kwargs(body, stream=False, inference_allowlist=allowlist)

        if not kwargs['model']:
            return 400, {
                "error": {
                    "message": "No model specified in request and no LITELLM_DEFAULT_MODEL configured.",
                    "type": "invalid_request_error",
                }
            }
        try:
            response = await self._execute_completion(kwargs)
            return 200, response.model_dump()
        except (BadRequestError, NotFoundError) as exc:
            if allowlist and compliant_chain:
                primary = kwargs.get("model")
                for candidate in compliant_chain:
                    if candidate == primary:
                        continue
                    retry_kwargs = {**kwargs, "model": self._resolve_runtime_model(candidate)}
                    try:
                        response = await self._execute_completion(retry_kwargs)
                        LOG.warning(
                            "Compliant fallback retry: %s -> %s after %s",
                            primary,
                            candidate,
                            type(exc).__name__,
                        )
                        return 200, response.model_dump()
                    except (BadRequestError, NotFoundError):
                        continue
                    except tuple(_EXCEPTION_STATUS_MAP.keys()):
                        continue
            if allowlist:
                status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
                LOG.warning(
                    "Org-scoped inference model '%s' failed (%s); no compliant fallback",
                    kwargs.get("model"),
                    type(exc).__name__,
                )
                return status, {
                    "error": {
                        "message": str(exc),
                        "type": type(exc).__name__,
                        "code": status,
                    }
                }
            fallback_model = self._resolve_runtime_model(self._default_fallback_model())
            if kwargs.get("model") != fallback_model:
                LOG.warning(
                    "Primary model '%s' failed (%s). Retrying once with fallback model '%s'.",
                    kwargs.get("model"),
                    type(exc).__name__,
                    fallback_model,
                )
                retry_kwargs = {**kwargs, "model": fallback_model}
                try:
                    response = await self._execute_completion(retry_kwargs)
                    return 200, response.model_dump()
                except tuple(_EXCEPTION_STATUS_MAP.keys()) as retry_exc:
                    status = _EXCEPTION_STATUS_MAP.get(type(retry_exc), 502)
                    LOG.warning("LiteLLM fallback retry error [%s %d]: %s", type(retry_exc).__name__, status, retry_exc)
                    return status, {
                        "error": {
                            "message": str(retry_exc),
                            "type": type(retry_exc).__name__,
                            "code": status,
                        }
                    }
                except Exception as retry_exc:
                    LOG.exception("Unexpected fallback retry LLM error")
                    return 502, {
                        "error": {
                            "message": f"Upstream LLM request failed after fallback retry: {retry_exc}",
                            "type": "internal_error",
                        }
                    }
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
            LOG.warning("LiteLLM error [%s %d]: %s", type(exc).__name__, status, exc)
            return status, {
                "error": {
                    "message": str(exc),
                    "type": type(exc).__name__,
                    "code": status,
                }
            }
        except tuple(_EXCEPTION_STATUS_MAP.keys()) as exc:
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
            LOG.warning("LiteLLM error [%s %d]: %s", type(exc).__name__, status, exc)
            return status, {
                "error": {
                    "message": str(exc),
                    "type": type(exc).__name__,
                    "code": status,
                }
            }
        except Exception as exc:
            LOG.exception("Unexpected LLM error")
            return 502, {
                "error": {
                    "message": f"Upstream LLM request failed: {exc}",
                    "type": "internal_error",
                }
            }
    
    async def _stream_chunks_from_response(self, response) -> AsyncGenerator[str, None]:
        """Emit SSE chunks from an active LiteLLM streaming response."""
        async for chunk in response:
            chunk_dict = chunk.model_dump()
            yield f"data: {json.dumps(chunk_dict)}\n\n"
        yield "data: [DONE]\n\n"

    async def acompletion_stream(
            self,
            body: dict,
            redacted_content: str | None = None,
            metrics: "StreamRunMetrics | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming completion. Yields SSE-formatted chunks.

        Optional ``metrics`` (from stream_orchestration) records provider start,
        first-token time, and fallback-before-first-token (no mid-stream switch).
        """
        try:
            from stream_orchestration import StreamRunMetrics
        except ImportError:
            from .stream_orchestration import StreamRunMetrics

        local_metrics = metrics if metrics is not None else StreamRunMetrics()
        local_metrics.provider_start_ts = time.perf_counter()

        body = self._apply_redaction(body, redacted_content)
        allowlist = self._pop_inference_allowlist(body)
        compliant_chain = self._pop_compliant_fallback_chain(body)
        kwargs = self._build_kwargs(body, stream=True, inference_allowlist=allowlist)

        if not kwargs['model']:
            error_chunk = {
                "error": {
                    "message": "No model specified.",
                    "type": "invalid_request_error",
                }
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            local_metrics.had_error = True
            return

        emitted = False

        try:
            from stream_orchestration import _extract_usage_from_sse_line
        except ImportError:
            from .stream_orchestration import _extract_usage_from_sse_line

        def _track_chunk(chunk: str) -> str:
            nonlocal emitted
            if chunk and chunk.strip().startswith("data: "):
                payload = chunk.strip()[6:].strip()
                if payload and payload != "[DONE]":
                    if '"error"' in payload:
                        local_metrics.had_error = True
                    elif not emitted:
                        local_metrics.first_token_ts = time.perf_counter()
                        emitted = True
                        local_metrics.chunks_emitted += 1
                    else:
                        local_metrics.chunks_emitted += 1
                usage = _extract_usage_from_sse_line(chunk)
                if usage:
                    local_metrics.usage = usage
            elif chunk:
                local_metrics.chunks_emitted += 1
            return chunk

        try:
            response = await self._execute_completion(kwargs)
            async for chunk in self._stream_chunks_from_response(response):
                yield _track_chunk(chunk)
            local_metrics.completed = True
        except (BadRequestError, NotFoundError) as exc:
            if allowlist and compliant_chain and not emitted:
                primary = kwargs.get("model")
                for candidate in compliant_chain:
                    if candidate == primary:
                        continue
                    retry_kwargs = {**kwargs, "model": self._resolve_runtime_model(candidate)}
                    try:
                        local_metrics.fallback_before_first_token = True
                        response = await self._execute_completion(retry_kwargs)
                        async for chunk in self._stream_chunks_from_response(response):
                            yield _track_chunk(chunk)
                        local_metrics.completed = True
                        return
                    except (BadRequestError, NotFoundError):
                        continue
                    except tuple(_EXCEPTION_STATUS_MAP.keys()):
                        continue
            if allowlist:
                status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
                error_chunk = {
                    "error": {"message": str(exc), "type": type(exc).__name__, "code": status}
                }
                yield f"data: {json.dumps(error_chunk)}\n\n"
                yield "data: [DONE]\n\n"
                local_metrics.had_error = True
                return
            fallback_model = self._resolve_runtime_model(self._default_fallback_model())
            if kwargs.get("model") != fallback_model and not emitted:
                LOG.warning(
                    "Primary stream model '%s' failed (%s). Retrying once with fallback model '%s'.",
                    kwargs.get("model"),
                    type(exc).__name__,
                    fallback_model,
                )
                local_metrics.fallback_before_first_token = True
                retry_kwargs = {**kwargs, "model": fallback_model}
                try:
                    response = await self._execute_completion(retry_kwargs)
                    async for chunk in self._stream_chunks_from_response(response):
                        yield _track_chunk(chunk)
                    local_metrics.completed = True
                    return
                except tuple(_EXCEPTION_STATUS_MAP.keys()) as retry_exc:
                    status = _EXCEPTION_STATUS_MAP.get(type(retry_exc), 502)
                    LOG.warning("LiteLLM stream fallback retry error [%s %d]: %s", type(retry_exc).__name__, status, retry_exc)
                    error_chunk = {
                        "error": {"message": str(retry_exc), "type": type(retry_exc).__name__, "code": status}
                    }
                    yield f"data: {json.dumps(error_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    local_metrics.had_error = True
                    return
                except Exception as retry_exc:
                    LOG.exception("Unexpected fallback retry LLM stream error")
                    error_chunk = {"error": {"message": str(retry_exc), "type": "internal_error"}}
                    yield f"data: {json.dumps(error_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    local_metrics.had_error = True
                    return
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
            LOG.warning("LiteLLM stream error [%s %d]: %s", type(exc).__name__, status, exc)
            error_chunk = {
                "error": {"message": str(exc), "type": type(exc).__name__, "code": status}
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            local_metrics.had_error = True
            return
        except tuple(_EXCEPTION_STATUS_MAP.keys()) as exc:
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
            LOG.warning("LiteLLM stream error [%s %d]: %s", type(exc).__name__, status, exc)
            error_chunk = {
                "error": {"message": str(exc), "type": type(exc).__name__, "code": status}
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            local_metrics.had_error = True
        except Exception as exc:
            LOG.exception("Unexpected LLM stream error")
            error_chunk = {"error": {"message": str(exc), "type": "internal_error"}}
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            local_metrics.had_error = True
    
    async def aembedding(
            self,
            body: dict,
    ) -> tuple[int, dict]:
        """Create embeddings. Returns (http_status, response_dict)."""
        model = body.get("model") or "text-embedding-3-small"
        model = self._normalize_model_alias(model)
        raw_input = body.get("input", "")
        # Normalize input to a list of strings
        if isinstance(raw_input, str):
            input_list = [raw_input]
        elif isinstance(raw_input, list):
            input_list = raw_input
        else:
            return 400, {
                "error": {
                    "message": "`input` must be a string or list of strings.",
                    "type": "invalid_request_error",
                }
            }

        kwargs = {
            "model": model,
            "input": input_list,
        }
        # Pass encoding_format if specified
        if "encoding_format" in body:
            kwargs["encoding_format"] = body["encoding_format"]
        if "dimensions" in body:
            kwargs["dimensions"] = body["dimensions"]

        try:
            response = await self._execute_embedding(kwargs)
            return 200, response.model_dump() if hasattr(response, "model_dump") else dict(response)
        except tuple(_EXCEPTION_STATUS_MAP.keys()) as exc:
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
            LOG.warning("LiteLLM embedding error [%s %d]: %s", type(exc).__name__, status, exc)
            return status, {
                "error": {
                    "message": str(exc),
                    "type": type(exc).__name__,
                    "code": status,
                }
            }
        except Exception as exc:
            LOG.exception("Unexpected embedding error")
            return 502, {
                "error": {
                    "message": f"Upstream embedding request failed: {exc}",
                    "type": "internal_error",
                }
            }

    def get_model_list(self) -> list[dict]:
        """Return available models in OpenAI-compatible format with model_id."""
        models = []
        if self._router and hasattr(self._router, "model_list"):
            for entry in self._router.model_list:
                model_name = entry.get("model_name", "")
                litellm_model = entry.get("litellm_params", {}).get("model", "")
                owned_by = litellm_model.split("/")[0] if "/" in litellm_model else "custom"
                models.append({
                    "id": model_name,
                    "object": "model",
                    "owned_by": owned_by,
                    "model_id": litellm_model,
                })
        elif self._config.get("litellm_default_model"):
            models.append({
                "id": self._config["litellm_default_model"],
                "object": "model",
                "owned_by": "default",
                "model_id": self._config["litellm_default_model"],
            })
        return models

    def reload_models(self, model_list: list[dict]) -> None:
        """
        Hot-reload the LiteLLM Router with an updated model list from Redis.

        Replaces the router with org-scoped entries only. If model_list is empty,
        the existing configuration is preserved.
        """
        if not model_list:
            LOG.info("reload_models called with empty list; keeping current config")
            return

        valid_models, invalid_models = self._filter_valid_reload_models(model_list)
        if invalid_models:
            sample = "; ".join(f"{name}: {reason}" for name, reason in invalid_models[:3])
            LOG.warning(
                "Skipping %d invalid model entries during reload. Sample: %s",
                len(invalid_models),
                sample,
            )

        if not valid_models:
            LOG.error("All model entries were invalid during reload; keeping previous configuration")
            return

        try:
            fallbacks = self._build_fallbacks(valid_models)
            self._router = LiteLLMRouter(
                model_list=valid_models,
                num_retries=self._config.get("litellm_num_retries", 2),
                timeout=self._config.get("litellm_request_timeout", 120),
                fallbacks=fallbacks,
            )
            self._set_active_model_names(valid_models)
            LOG.info(
                "LiteLLM router hot-reloaded with %d valid models from Redis (%d invalid dropped)",
                len(valid_models),
                len(invalid_models),
            )
        except Exception:
            LOG.exception(
                "Failed to reload LiteLLM router with new model list; "
                "keeping previous configuration"
            )

    @staticmethod
    def _extract_response_text(response: dict) -> str:
        choices = response.get("choices") or []
        if not choices:
            return ""
        first_choice = choices[0] or {}
        if isinstance(first_choice.get("message"), dict):
            return str(first_choice["message"].get("content") or "")
        if isinstance(first_choice.get("delta"), dict):
            return str(first_choice["delta"].get("content") or "")
        return ""

    @staticmethod
    def _parse_json_object(raw_text: str) -> dict[str, Any] | None:
        if not raw_text:
            return None
        text = raw_text.strip()
        for candidate in (text, text.replace("```json", "").replace("```", "").strip()):
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def estimate_prompt_tokens(messages: list[dict], max_tokens: int = 0) -> int:
        prompt_chars = 0
        for message in messages or []:
            content = message.get("content", "") if isinstance(message, dict) else ""
            if isinstance(content, list):
                content = " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
            prompt_chars += len(str(content or ""))
        prompt_tokens = max(1, prompt_chars // 4) if prompt_chars else 0
        return max(prompt_tokens + max(max_tokens or 0, 0), 1)

    @staticmethod
    def _normalize_weights(weights: dict[str, float] | None = None) -> dict[str, float]:
        resolved = {**_DEFAULT_ROUTING_WEIGHTS, **(weights or {})}
        total = sum(resolved.values()) or 1.0
        return {key: value / total for key, value in resolved.items()}

    def _score_routing_models(
        self,
        routing_models: list[dict],
        request_risk_score: float = 0.0,
        required_compliance: list[str] | None = None,
        data_sensitivity: str = "public",
        estimated_tokens: int = 500,
        latency_budget_ms: int = 30000,
        weights: dict[str, float] | None = None,
        allowed_models: list[str] | None = None,
    ) -> list[dict]:
        normalized_weights = self._normalize_weights(weights)
        allowed_set = {str(model).lower() for model in (allowed_models or []) if model}
        req_sens_level = _SENSITIVITY_ORDER.get(data_sensitivity, 0)
        required_tags = [tag for tag in (required_compliance or []) if tag]

        eligible: list[dict] = []
        for model in routing_models:
            model_name = str(model.get("model_name") or "")
            model_id = str(model.get("model_id") or model_name)
            if not model.get("is_active", True):
                continue
            if allowed_set and model_name.lower() not in allowed_set and model_id.lower() not in allowed_set:
                continue
            if required_tags:
                model_tags = model.get("compliance_tags") or []
                if not all(tag in model_tags for tag in required_tags):
                    continue
            model_sens = _SENSITIVITY_ORDER.get(model.get("data_sensitivity_level", "public"), 0)
            if model_sens < req_sens_level:
                continue
            eligible.append(model)

        if not eligible:
            return []

        max_priority = max((m.get("routing_priority", 0) for m in eligible), default=1) or 1
        scored: list[dict] = []
        for model in eligible:
            model_name = str(model.get("model_name") or "")
            model_id = str(model.get("model_id") or model_name)
            model_risk = model.get("risk_score", 0.0) or 0.0
            risk_component = (1.0 - model_risk) * (1.0 - request_risk_score)

            cost_input = model.get("cost_per_1k_input_tokens", 0.0) or 0.0
            cost_component = 1.0 / (1.0 + cost_input * estimated_tokens / 1000)

            model_latency = model.get("latency_sla_ms", 30000) or 30000
            latency_component = 1.0 if model_latency <= latency_budget_ms else max(latency_budget_ms / model_latency, 0.0)

            priority = model.get("routing_priority", 0) or 0
            priority_component = priority / max_priority

            composite = (
                normalized_weights["risk"] * risk_component
                + normalized_weights["cost"] * cost_component
                + normalized_weights["latency"] * latency_component
                + normalized_weights["priority"] * priority_component
            )
            scored.append({
                "model_name": model_name,
                "model_id": model_id,
                "model": model,
                "score": round(composite, 4),
                "risk_component": round(risk_component, 4),
                "cost_component": round(cost_component, 4),
                "latency_component": round(latency_component, 4),
                "priority_component": round(priority_component, 4),
            })

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored

    @staticmethod
    def extract_usage(response: dict) -> dict | None:
        """Extract token usage from a litellm response dict."""
        usage = response.get("usage")
        if usage:
            return {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        return None

    def select_model(
        self,
        routing_models: list[dict],
        request_risk_score: float = 0.0,
        required_compliance: list[str] | None = None,
        data_sensitivity: str = "public",
        estimated_tokens: int = 500,
        latency_budget_ms: int = 30000,
        weights: dict[str, float] | None = None,
        allowed_models: list[str] | None = None,
    ) -> ModelSelection | None:
        """
        Multi-dimensional model selection using weighted scoring.

        Hard filters (binary): compliance tags, data sensitivity, is_active.
        Soft scoring (weighted): risk, cost, latency, priority.
        """
        normalized_weights = self._normalize_weights(weights)
        scored = self._score_routing_models(
            routing_models=routing_models,
            request_risk_score=request_risk_score,
            required_compliance=required_compliance,
            data_sensitivity=data_sensitivity,
            estimated_tokens=estimated_tokens,
            latency_budget_ms=latency_budget_ms,
            weights=normalized_weights,
            allowed_models=allowed_models,
        )
        if not scored:
            return None

        best = scored[0]
        best_model = best["model"]
        fallbacks = [item["model_name"] for item in scored[1:4]]

        return ModelSelection(
            model_name=best_model.get("model_name", ""),
            model_id=best_model.get("model_id", ""),
            score=best["score"],
            reason=f"Weighted selection (risk={normalized_weights['risk']:.0%}, cost={normalized_weights['cost']:.0%}, latency={normalized_weights['latency']:.0%}, priority={normalized_weights['priority']:.0%})",
            fallback_chain=fallbacks,
            decision_source="weighted",
            candidate_count=len(scored),
        )

    async def adjudicate_model_selection(
        self,
        routing_models: list[dict],
        request_messages: list[dict],
        preferred_model: str = "",
        request_risk_score: float = 0.0,
        required_compliance: list[str] | None = None,
        data_sensitivity: str = "public",
        estimated_tokens: int = 500,
        latency_budget_ms: int = 30000,
        weights: dict[str, float] | None = None,
        allowed_models: list[str] | None = None,
        token_budget_tpm: int | None = None,
        adjudicator_model: str = "bedrock-gpt-oss-120b",
    ) -> ModelSelection | None:
        normalized_weights = self._normalize_weights(weights)
        scored = self._score_routing_models(
            routing_models=routing_models,
            request_risk_score=request_risk_score,
            required_compliance=required_compliance,
            data_sensitivity=data_sensitivity,
            estimated_tokens=estimated_tokens,
            latency_budget_ms=latency_budget_ms,
            weights=normalized_weights,
            allowed_models=allowed_models,
        )
        if not scored:
            return None

        heuristic = self.select_model(
            routing_models=routing_models,
            request_risk_score=request_risk_score,
            required_compliance=required_compliance,
            data_sensitivity=data_sensitivity,
            estimated_tokens=estimated_tokens,
            latency_budget_ms=latency_budget_ms,
            weights=normalized_weights,
            allowed_models=allowed_models,
        )
        if heuristic is None:
            return None

        candidate_map = {item["model_name"]: item for item in scored}
        # Build case-insensitive + model_id lookup for robust matching
        _candidate_lookup: dict[str, str] = {}
        for item in scored:
            _candidate_lookup[item["model_name"].lower().strip()] = item["model_name"]
            _candidate_lookup[item["model_id"].lower().strip()] = item["model_name"]

        request_preview = []
        for message in request_messages[-4:]:
            if not isinstance(message, dict):
                continue
            request_preview.append({
                "role": message.get("role", "user"),
                "content": str(message.get("content", ""))[:400],
            })

        adjudicator_prompt = {
            "preferred_model": preferred_model or "auto",
            "request_risk_score": round(request_risk_score, 4),
            "required_compliance": required_compliance or [],
            "data_sensitivity": data_sensitivity,
            "estimated_tokens": estimated_tokens,
            "latency_budget_ms": latency_budget_ms,
            "token_budget_tpm": token_budget_tpm,
            "weights": normalized_weights,
            "governance_context": {
                "routing_strategy": "weighted_bedrock_adjudication",
                "weight_interpretation": {
                    "risk": f"{normalized_weights.get('risk', 0):.0%} — higher = prefer safer (lower risk_score) models",
                    "cost": f"{normalized_weights.get('cost', 0):.0%} — higher = prefer cheaper models",
                    "latency": f"{normalized_weights.get('latency', 0):.0%} — higher = prefer faster models",
                    "priority": f"{normalized_weights.get('priority', 0):.0%} — higher = prefer higher-priority models",
                },
                "sensitivity_requirement": f"Model must support data_sensitivity_level >= '{data_sensitivity}'",
                "compliance_requirement": f"Model must have ALL of: {required_compliance or ['none']}",
            },
            "request_preview": request_preview,
            "candidate_models": [
                {
                    "model_name": item["model_name"],
                    "model_id": item["model_id"],
                    "score": item["score"],
                    "risk_component": item["risk_component"],
                    "cost_component": item["cost_component"],
                    "latency_component": item["latency_component"],
                    "priority_component": item["priority_component"],
                    "compliance_tags": item["model"].get("compliance_tags") or [],
                    "data_sensitivity_level": item["model"].get("data_sensitivity_level", "public"),
                    "latency_sla_ms": item["model"].get("latency_sla_ms", 30000),
                    "cost_per_1k_input_tokens": item["model"].get("cost_per_1k_input_tokens", 0.0),
                    "routing_priority": item["model"].get("routing_priority", 0),
                    "risk_score": item["model"].get("risk_score", 0.0),
                }
                for item in scored[:5]
            ],
        }

        adjudicator_body = {
            "model": adjudicator_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are ZeroShield's /v1/chat/completions routing adjudicator — "
                        "a Bedrock GPT OSS 120B model that analyzes user input and governance "
                        "settings to select the optimal LLM for each request.\n\n"
                        "INSTRUCTIONS:\n"
                        "1. You MUST select exactly one model from the candidate_models list.\n"
                        "2. Return the model_name field EXACTLY as it appears in the candidate — "
                        "do NOT rephrase, alias, or invent a model name.\n"
                        "3. Decision priority:\n"
                        "   a) Hard constraints: compliance_tags and data_sensitivity_level MUST meet requirements.\n"
                        "   b) Weighted scoring: evaluate risk, cost, latency, and priority using the provided weights.\n"
                        "   c) Request analysis: consider the request content to pick the best-suited model "
                        "(e.g., complex reasoning → high-capability model, simple Q&A → fast/cheap model).\n"
                        "4. An explicit preferred_model is a soft preference, not a hard constraint.\n"
                        "5. Return ONLY valid JSON with keys: selected_model, reason, policy_summary, decision_factors.\n"
                        "   - selected_model: exact model_name string from candidate_models\n"
                        "   - reason: 1-2 sentence explanation of why this model was chosen, explicitly referencing risk, latency budget, and cost/token budget impact\n"
                        "   - policy_summary: brief governance summary explicitly covering data sensitivity and compliance requirements\n"
                        "   - decision_factors: list of factor strings that influenced the decision"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(adjudicator_prompt, ensure_ascii=True),
                },
            ],
            "temperature": 0,
            "max_tokens": 350,
            "response_format": {"type": "json_object"},
        }

        code, response = await self.acompletion(adjudicator_body)
        _log = logging.getLogger("gateway")
        _log.info(
            "Bedrock adjudicator call: model=%s, candidates=%d, status=%d",
            adjudicator_model,
            len(scored),
            code,
        )
        if code != 200 or not isinstance(response, dict):
            heuristic.reason = f"Policy adjudicator unavailable; {heuristic.reason}"
            heuristic.decision_source = "weighted_fallback"
            heuristic.evaluator_model = adjudicator_model
            heuristic.requested_model = preferred_model or "auto"
            heuristic.policy_summary = "Fallback to weighted routing after adjudicator failure."
            heuristic.decision_factors = ["adjudicator_unavailable"]
            return heuristic

        parsed = self._parse_json_object(self._extract_response_text(response)) or {}
        selected_name = str(parsed.get("selected_model") or "").strip()
        _log.info(
            "Bedrock adjudicator raw parsed JSON: %s",
            json.dumps(parsed, default=str)[:500],
        )

        # --- Robust candidate matching: exact → case-insensitive → model_id ---
        resolved_name: str | None = None
        if selected_name in candidate_map:
            resolved_name = selected_name
        elif selected_name.lower().strip() in _candidate_lookup:
            resolved_name = _candidate_lookup[selected_name.lower().strip()]
        else:
            # Try partial match as last resort (Bedrock sometimes adds provider prefix)
            sel_lower = selected_name.lower().strip()
            for key, canon_name in _candidate_lookup.items():
                if sel_lower.endswith(key) or key.endswith(sel_lower):
                    resolved_name = canon_name
                    break

        _log.info(
            "Bedrock adjudicator response: selected_model=%r, resolved=%r, "
            "candidates=%s, reason=%s",
            selected_name,
            resolved_name,
            [c["model_name"] for c in scored[:5]],
            str(parsed.get("reason", ""))[:200],
        )

        if resolved_name is None:
            _log.warning(
                "ROUTING ADJUDICATOR: returned unrecognised model %r; "
                "candidates were %s — falling back to weighted selection.",
                selected_name,
                list(candidate_map.keys()),
            )
            heuristic.reason = f"Policy adjudicator returned an invalid candidate '{selected_name}'; {heuristic.reason}"
            heuristic.decision_source = "weighted_fallback"
            heuristic.evaluator_model = adjudicator_model
            heuristic.requested_model = preferred_model or "auto"
            heuristic.policy_summary = "Fallback to weighted routing after invalid adjudicator response."
            heuristic.decision_factors = ["invalid_adjudicator_selection"]
            return heuristic

        selected = candidate_map[resolved_name]
        fallback_chain = [item["model_name"] for item in scored if item["model_name"] != resolved_name][:3]
        decision_factors = parsed.get("decision_factors")
        if not isinstance(decision_factors, list):
            decision_factors = []

        # Build meaningful reason/summary if Bedrock didn't provide them
        bedrock_reason = str(parsed.get("reason") or "").strip()
        bedrock_policy = str(parsed.get("policy_summary") or "").strip()
        if not bedrock_reason:
            bedrock_reason = (
                f"Bedrock GPT OSS 120B adjudicator selected '{resolved_name}' "
                f"(score={selected['score']:.4f}) from {len(scored)} candidates. "
                f"Risk={request_risk_score:.2f}, latency_budget_ms={latency_budget_ms}, "
                f"estimated_tokens={estimated_tokens}, token_budget_tpm={token_budget_tpm or 'n/a'}. "
                f"Weights: risk={normalized_weights.get('risk',0):.0%}, "
                f"cost={normalized_weights.get('cost',0):.0%}, "
                f"latency={normalized_weights.get('latency',0):.0%}, "
                f"priority={normalized_weights.get('priority',0):.0%}."
            )
        if not bedrock_policy:
            sens_model = selected["model"].get("data_sensitivity_level", "public")
            comp_model = selected["model"].get("compliance_tags") or []
            bedrock_policy = (
                f"Model meets sensitivity={sens_model}, compliance={comp_model}. "
                f"Risk={selected['model'].get('risk_score',0):.2f}, "
                f"latency_sla={selected['model'].get('latency_sla_ms',0)}ms."
            )
        if not decision_factors:
            decision_factors = [
                f"model_score={selected['score']:.4f}",
                f"risk_component={selected.get('risk_component',0):.4f}",
                f"cost_component={selected.get('cost_component',0):.4f}",
                f"latency_component={selected.get('latency_component',0):.4f}",
                f"priority_component={selected.get('priority_component',0):.4f}",
                f"candidates_evaluated={len(scored)}",
                f"data_sensitivity={data_sensitivity}",
            ]

        _log.info(
            "ROUTING DECISION: bedrock_adjudicator selected '%s' "
            "(score=%.4f, %d candidates, fallback=%s)",
            resolved_name,
            selected["score"],
            len(scored),
            fallback_chain,
        )

        return ModelSelection(
            model_name=resolved_name,
            model_id=selected["model_id"],
            score=selected["score"],
            reason=bedrock_reason,
            fallback_chain=fallback_chain,
            requested_model=preferred_model or "auto",
            decision_source="policy_adjudicator",
            evaluator_model=adjudicator_model,
            policy_summary=bedrock_policy,
            decision_factors=[str(item) for item in decision_factors if item],
            candidate_count=len(scored),
        )
