"""
LLM routing via LiteLLM.
Provides async completion (streaming and non-streaming) with error mapping,
plus smart multi-dimensional model selection.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Awaitable, Callable, Callable, TYPE_CHECKING

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

from ai_mesh_gateway.platform_models import (
    is_platform_model_name,
    resolve_platform_bedrock_model,
)

from litellm.exceptions import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadRequestError,
    BudgetExceededError,
    ContentPolicyViolationError,
    ContextWindowExceededError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

# C7: litellm's Router raises RouterRateLimitError / RouterRateLimitErrorBasic when
# no healthy deployment is available (e.g. a model whose only upstream returns 404
# triggers a cooldown). These subclass ONLY ValueError, so they bypass both the
# litellm.exceptions catch and the exact-type _EXCEPTION_STATUS_MAP below, falling
# through to the bare `except Exception` that returns a generic 502 internal_error.
# A "no healthy deployment / throttle" condition is honestly a 503 — collect the
# classes defensively (tolerating litellm version drift) so we can catch them
# explicitly before the bare except. An empty tuple `except ()` simply matches
# nothing, preserving the old behavior if the import path ever changes.
_ROUTER_THROTTLE_EXCS: tuple = ()
try:
    from litellm.types.router import RouterRateLimitError as _RRLE
    _ROUTER_THROTTLE_EXCS += (_RRLE,)
except Exception:  # pragma: no cover - litellm version drift
    pass
try:
    from litellm.types.router import RouterRateLimitErrorBasic as _RRLEB
    _ROUTER_THROTTLE_EXCS += (_RRLEB,)
except Exception:  # pragma: no cover - litellm version drift
    pass

LOG = logging.getLogger("gateway.llm_router")

# E13 mid-stream kill-switch re-check throttle. Once a stream is live there is no
# per-chunk policy gate, so an operator who trips the kill-switch (or model-state
# isolation) DURING an active stream would otherwise keep getting the remainder
# from a disabled model. We re-check the ACTIVE model's live kill/isolate state
# inside the chunk loop, but THROTTLED so the Redis read does not run on every
# token: at most once per ``_MIDSTREAM_KS_CHECK_EVERY_CHUNKS`` chunks AND no more
# often than every ``_MIDSTREAM_KS_CHECK_INTERVAL_S`` seconds of wall-clock. The
# check FAILS OPEN — a Redis hiccup / callback exception never terminates a live,
# legitimate stream; only a DEFINITIVE kill/isolate verdict ends the stream.
_MIDSTREAM_KS_CHECK_EVERY_CHUNKS = 16
_MIDSTREAM_KS_CHECK_INTERVAL_S = 1.0


class _MidStreamKillSwitch(Exception):
    """Internal sentinel: the ACTIVE model was kill-switched / isolated mid-stream.

    Raised from the throttled state-check wrapper so the prefix already emitted to
    the client stays, the upstream generator is aclose()'d (so upstream generation
    halts too), and ``acompletion_stream`` ends the stream with a terminal error
    SSE rather than streaming the remainder from a now-disabled model.
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason or "model disabled by operator mid-stream")
        self.reason = reason

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
    InternalServerError: 502,
    ServiceUnavailableError: 503,
    APIError: 502,
}

# OpenAI-compatible params to forward to litellm
_PASSTHROUGH_PARAMS = (
    "temperature", "top_p", "max_tokens", "stop",
    "presence_penalty", "frequency_penalty", "tools",
    "tool_choice", "response_format", "seed", "n",
    # streaming #7: when the client sends stream_options.include_usage, the
    # OpenAI contract requires a terminal SSE chunk carrying ``usage``. Forward
    # it so providers that honor it emit that usage chunk natively. The gateway
    # also synthesizes one in stream_with_finalize as a fallback for providers
    # that do not.
    "stream_options",
    # SDK-compat: forward GPT-5.x / function-calling params to the provider.
    # All are pure passthrough (no content) — the firewall runs pre-LLM and is
    # unaffected; tool-call scanning (G7) inspects tool_calls regardless of these.
    "logprobs", "top_logprobs", "max_completion_tokens",
    "parallel_tool_calls", "reasoning", "reasoning_effort", "user",
)

# OpenAI Responses API top-level params forwarded to litellm.aresponses.
_RESPONSES_PASSTHROUGH_PARAMS = (
    "instructions", "max_output_tokens", "temperature", "top_p",
    "tools", "tool_choice", "reasoning", "text", "truncation",
    "store", "previous_response_id", "metadata", "parallel_tool_calls",
    "include", "user",
)

# Keep compatibility with historical or UI-facing aliases.
_MODEL_ALIAS_MAP = {
    "bedrock-gpt-oss-120b-long-context": "bedrock-gpt-oss-120b",
}

# Default embedding deployment name. A request with no model (or an unknown
# alias the org has not provisioned) must NOT silently fall through to this
# deployment; it is only the explicit default and the baseline allowed name.
_DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# Defensive upper bound on the total characters across an embedding request's
# `input` items. Mirrors the chat/output-guard input ceilings: without it a
# multi-megabyte payload is forwarded to the upstream deployment (and, on
# failure, re-tried across fallbacks) — a resource-exhaustion vector.
_MAX_EMBEDDING_INPUT_CHARS = 1_000_000

# R5: generic, client-safe messages keyed by HTTP status. Raw LiteLLM exception
# text leaks fallback topology, provider names, API keys, and OpenRouter user_id —
# it must be logged server-side only, never returned to the client.
_CLIENT_SAFE_STATUS_MESSAGES = {
    400: "The request was invalid.",
    401: "Upstream authentication failed.",
    404: "The requested model is not available.",
    429: "Rate limit or budget exceeded. Please retry later.",
    502: "The upstream inference request failed.",
    503: "The upstream inference service is unavailable.",
    504: "The upstream inference request timed out.",
}


_REDACTED_TOPOLOGY_KEYWORDS = (
    "Available Model Group Fallbacks",
    "Model Group",
    "model_group",
    "Fallbacks",
    "fallback chain",
)


def _scrub_internal_topology(text: Any) -> str:
    """M8: redact internal model-group / fallback-chain topology from a string
    BEFORE it is logged. Client responses are already generic (see
    _sanitize_exception_message); this keeps the same internal names
    (e.g. "Model Group=...", "Fallbacks=[...]") out of server-side logs/telemetry
    too, since logs may feed dashboards (compliance)."""
    if not text:
        return str(text) if text is not None else ""
    out = str(text)
    for kw in _REDACTED_TOPOLOGY_KEYWORDS:
        out = out.replace(kw, "[REDACTED]")
    return out


def _sanitize_exception_message(exc: Exception, status: int | None = None) -> str:
    """R5: Return a generic, client-safe error string.

    NEVER includes provider/topology/api-key/user_id details from the raw
    exception. Callers MUST log the raw ``exc`` server-side separately.
    """
    if status is not None and status in _CLIENT_SAFE_STATUS_MESSAGES:
        return _CLIENT_SAFE_STATUS_MESSAGES[status]
    mapped = _EXCEPTION_STATUS_MAP.get(type(exc))
    if mapped is not None and mapped in _CLIENT_SAFE_STATUS_MESSAGES:
        return _CLIENT_SAFE_STATUS_MESSAGES[mapped]
    return "The upstream inference request failed."


_SENSITIVITY_ORDER = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


def _req_sensitivity_level(value: object) -> int:
    """Caller data_sensitivity -> level, FAIL-CLOSED: an unknown non-empty value
    ('topsecret') is treated as the most restrictive level, not 0 (public), so it
    cannot silently route restricted data onto a public model."""
    key = str(value or "").strip().lower()
    if key in _SENSITIVITY_ORDER:
        return _SENSITIVITY_ORDER[key]
    if key in ("", "public"):
        return 0
    return max(_SENSITIVITY_ORDER.values())

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

# B1 (egress = truth): a digit-bearing sensitive run, possibly split by separators
# (spaces / dots / dashes / parens) — a 5+5 spaced phone ("89295 54991"), a spaced
# SSN, a grouped card. A contiguous ``\d{7,}`` match misses any separator-split
# format, so the byte-verify below considers these wider runs too.
_DIGIT_RUN_RE = re.compile(r"\d[\d .()\-]{5,}\d")


def _redact_text_with_backstop(text, redacted_content):
    """Deterministic redactor shared by the chat (_apply_redaction) and Responses
    (aresponses) paths so a value is masked identically wherever it appears.

    ``redact_all`` is, by construction, WEAKER than the verdict-aware
    ``InputScanner.redact_pii`` that produced ``redacted_content`` (the authoritative
    redacted display / input — what the firewall decided must never reach the model):
    redact_all only masks a 10-digit phone when an adjacent label disambiguates it
    and only as a CONTIGUOUS run, whereas a Tier-2 / policy detector flags the same
    number from ANY phrasing. That asymmetry once forwarded the RAW value upstream
    while the trace showed it masked — a silent PII leak.

    B1 closes it with a FAIL-CLOSED egress byte-verify: any digit-bearing run this
    text carries that the firewall's authoritative ``redacted_content`` REMOVED
    (absent there) but ``redact_all`` left raw is masked too — so the bytes on the
    wire always reflect the verdict (egress = truth), never a phantom redaction.
    FP-safety is structural: we mask ONLY runs the firewall already removed, so a run
    it deliberately KEPT (a legit order id, still present in ``redacted_content``)
    stays intact. Partial masks like ``***-***-4991`` keep only 4 digits and are
    derived from ``text`` (not the masked ``out``), so they never re-trigger."""
    try:
        from patterns import redact_all
    except ImportError:
        from .patterns import redact_all
    out = redact_all(text)
    if not text or redacted_content is None:
        return out
    # Candidate runs: contiguous 7+ digit runs AND separator-split digit groups.
    candidates = set(re.findall(r"\d{7,}", text))
    candidates.update(m.group(0) for m in _DIGIT_RUN_RE.finditer(text))
    # Longest first so a full split run is masked before any contiguous sub-run.
    for run in sorted(candidates, key=len, reverse=True):
        digits = re.sub(r"\D", "", run)
        if len(digits) < 7:
            continue
        # Egress = truth: mask iff the firewall removed this run yet it still rides raw.
        if run not in redacted_content and run in out:
            out = out.replace(run, f"***-***-{digits[-4:]}")
    return out


def _redact_tool_descriptions(obj, redactor):
    """Recursively mask free-text ``description`` strings in a tool definition,
    leaving every structural schema key (name, type, enum, required, properties …)
    intact so the function-calling contract still resolves. ``redactor`` is the same
    deterministic redactor ``LLMRouter._apply_redaction`` applies to message content,
    so a PII value masked in a message is masked identically in a tool description."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "description" and isinstance(v, str) and v:
                out[k] = redactor(v)
            else:
                out[k] = _redact_tool_descriptions(v, redactor)
        return out
    if isinstance(obj, list):
        return [_redact_tool_descriptions(x, redactor) for x in obj]
    return obj


class LLMRouter:
    """Async LLM router backed by LiteLLM."""

    def __init__(self, config: dict):
        self._config = config
        self._router: LiteLLMRouter | None = None
        self._active_model_names: list[str] = []
        self._qualified_model_names: set[str] = set()  # H7: org::model routing keys
        self._deployment_params: dict[str, dict] = {}   # Responses API: name -> resolved litellm_params (BYOK)
        self._remap_telemetry_hook: Callable[..., None] | None = None

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

    def set_remap_telemetry_hook(self, hook: Callable[..., None] | None) -> None:
        """Optional callback invoked when a requested model is remapped at runtime."""
        self._remap_telemetry_hook = hook

    def get_active_model_names(self) -> list[str]:
        """Bare model names currently loaded in the LiteLLM router model groups."""
        return list(self._active_model_names)

    def _set_active_model_names(self, model_list: list[dict]) -> None:
        # H7: use the BARE name (model_info.base_model_name) — clients send bare
        # model names, so validation/active-name matching must stay un-qualified
        # even though the litellm routing key is org-qualified.
        self._active_model_names = [
            self._normalize_model_alias((entry.get("model_info") or {}).get("base_model_name") or entry.get("model_name", ""))
            for entry in model_list
            if (entry.get("model_info") or {}).get("base_model_name") or entry.get("model_name")
        ]

    def _resolve_runtime_model(self, requested_model: str) -> str:
        if is_platform_model_name(requested_model):
            raise ValueError(
                f"Platform model '{requested_model}' cannot be routed via LiteLLM inference. "
                "Use BedrockClient.converse for guard and adjudicator calls."
            )
        if not self._active_model_names:
            return requested_model
        if requested_model in self._active_model_names:
            return requested_model

        preferred_default = self._normalize_model_alias(self._config.get("litellm_default_model", "") or "")
        if preferred_default and preferred_default in self._active_model_names:
            return preferred_default

        return self._active_model_names[0]

    def _qualify_like_primary(self, candidate_model: str, primary: str) -> str:
        """Org-qualify a vetted-failover candidate with the SAME org prefix as the
        primary routing key. H7 (#17): the compliant-chain retry previously routed
        ``_resolve_runtime_model(candidate)`` — a BARE name — so a failover could be
        resolved by the shared LiteLLM Router to a same-named PEER org's deployment
        (and its BYOK key). Re-applying the primary's ``{org}::`` prefix keeps the
        retry inside the requesting tenant. Fail-safe to bare when the qualified
        deployment doesn't exist (mirrors ``_build_kwargs``)."""
        runtime = self._resolve_runtime_model(candidate_model)
        if "::" in str(runtime):
            return runtime
        _org = str(primary).split("::", 1)[0] if "::" in str(primary) else ""
        if _org:
            _q = f"{_org}::{runtime}"
            if _q in self._qualified_model_names:
                return _q
        return runtime

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
        # R2: defense-in-depth — never allow a reserved platform model
        # (provider=internal / zeroshield-model) to be a primary or fallback target.
        # P4: the global fallback map is the CHAT chain — never let an embedding
        # deployment enter it, else a non-retryable chat 4xx (e.g. bad tool schema)
        # fans out across embedding endpoints (wasted cross-type upstream calls).
        # Embedding requests use their own per-request `_embedding_fallbacks_for`.
        model_names = [
            self._normalize_model_alias(entry.get("model_name", ""))
            for entry in model_list
            if entry.get("model_name")
            and not self._is_reserved_model_entry(entry)
            and not self._is_embedding_model_id(str((entry.get("litellm_params") or {}).get("model") or ""))
        ]
        if not model_names:
            return None
        # Also drop any reserved alias that slipped into the configured fallback list.
        fallback_models = [m for m in fallback_models if not is_platform_model_name(m)]

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
            # R2: reserved platform models (provider=internal / zeroshield-model /
            # legacy 120b aliases / bedrock guard ids) must never enter the router
            # model_list — they are not org inference targets.
            if self._is_reserved_model_entry(entry):
                name = ""
                if isinstance(entry, dict):
                    name = str(entry.get("model_name") or (entry.get("litellm_params") or {}).get("model") or "")
                LOG.info("Excluding reserved platform model '%s' from router reload", name or "unknown")
                continue
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

    @staticmethod
    def _is_reserved_model_entry(entry: dict) -> bool:
        """R2: True when a model entry is a reserved platform model.

        The platform guard/adjudicator/Tier-2 model (``zeroshield-model`` and its
        legacy aliases, provider == 'internal') must NEVER be a routable or
        fallback inference target for org traffic. Detect via provider tag OR a
        reserved name on either the public ``model_name`` or the underlying
        ``litellm_params.model`` id.
        """
        if not isinstance(entry, dict):
            return False
        if str(entry.get("provider") or "").strip().lower() == "internal":
            return True
        model_name = LLMRouter._normalize_model_alias(str(entry.get("model_name") or ""))
        if is_platform_model_name(model_name):
            return True
        litellm_id = str((entry.get("litellm_params") or {}).get("model") or "")
        if is_platform_model_name(LLMRouter._normalize_model_alias(litellm_id)):
            return True
        return False

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

    def _resolve_responses_deployment(self, body: dict) -> tuple[str, dict | None]:
        """Resolve (route_model_name, litellm_params) for a Responses request,
        org-qualifying the model EXACTLY like _build_kwargs (H7). Returns
        (resolved_name, params) — params is None when the requested model is not a
        configured deployment for this org, so the caller maps it to a 404
        model_not_found (the clean OpenAI model-name contract)."""
        requested = self._normalize_model_alias(body.get("model") or self._default_fallback_model())
        _org = str(body.get("_zs_org_slug") or "")
        _qualified = f"{_org}::{requested}" if _org else ""
        # Clean OpenAI model-name contract: the CLIENT's requested model must be a
        # configured model for this org. Do NOT silently remap to the first active
        # model (the legacy _resolve_runtime_model fallback) — return None so the
        # caller emits a 404 model_not_found.
        if (
            requested not in self._active_model_names
            and (not _qualified or _qualified not in self._qualified_model_names)
            and requested not in self._deployment_params
        ):
            return requested, None
        route_model = requested
        if _org and "::" not in str(requested) and _qualified in self._qualified_model_names:
            route_model = _qualified
        params = self._deployment_params.get(route_model) or self._deployment_params.get(requested)
        return route_model, (dict(params) if isinstance(params, dict) else None)

    async def aresponses(self, body: dict, redacted_content: str | None = None) -> tuple[int, dict]:
        """OpenAI Responses API (non-streaming). Returns (http_status, response_dict).

        litellm.Router exposes no aresponses in this version, so route via
        module-level ``litellm.aresponses`` with the org deployment's explicit
        decrypted BYOK api_key/api_base (the byok_embedder pattern) — preserving
        per-tenant key isolation. ``redacted_content`` (when input is a plain
        string) replaces the input so the upstream model never sees raw PII."""
        if redacted_content is not None and isinstance(body.get("input"), str):
            body = {**body, "input": redacted_content}
        route_model, params = self._resolve_responses_deployment(body)
        if params is None:
            return 404, {"error": {
                "message": f"Model '{body.get('model')}' is not configured for this organization.",
                "type": "invalid_request_error",
                "code": "model_not_found",
            }}
        upstream_model = str(params.get("model") or route_model)
        kwargs: dict = {"input": body.get("input"), "model": upstream_model}
        if params.get("api_key"):
            kwargs["api_key"] = params["api_key"]
        if params.get("api_base"):
            kwargs["api_base"] = params["api_base"]
        for p in _RESPONSES_PASSTHROUGH_PARAMS:
            if p in body and body[p] is not None:
                kwargs[p] = body[p]
        # Tools are a passthrough param here too — mask their free-text descriptions
        # when redaction fired, mirroring the chat path (_apply_redaction). Without
        # this, PII smuggled in a Responses tool definition reaches the model raw
        # while the trace shows the input masked (same silent-leak class).
        if redacted_content is not None and isinstance(kwargs.get("tools"), list):
            kwargs["tools"] = [
                _redact_tool_descriptions(
                    t, lambda s: _redact_text_with_backstop(s, redacted_content)
                )
                for t in kwargs["tools"]
            ]
        try:
            resp = await litellm.aresponses(**kwargs)
            return 200, (resp.model_dump() if hasattr(resp, "model_dump") else dict(resp))
        except (BadRequestError, NotFoundError) as exc:
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 400)
            return status, {"error": {"message": _sanitize_exception_message(exc, status), "type": type(exc).__name__, "code": status}}
        except tuple(_EXCEPTION_STATUS_MAP.keys()) as exc:
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
            LOG.warning("litellm responses error [%s %d]: %s", type(exc).__name__, status, _scrub_internal_topology(exc))
            return status, {"error": {"message": _sanitize_exception_message(exc, status), "type": type(exc).__name__, "code": status}}
        except Exception as exc:  # noqa: BLE001
            LOG.exception("Unexpected responses error")
            return 502, {"error": {"message": _sanitize_exception_message(exc, 502), "type": "internal_error"}}

    def _apply_redaction(self, body: dict, redacted_content: str | None) -> dict:
        """Redact PII/secrets in EVERY conversation message before the upstream call.

        Previously this overwrote ONLY the last user message with the whole
        redacted-conversation blob, which (a) dumped the entire role-prefixed
        concatenation into that one slot and (b) left every EARLIER user turn with
        its ORIGINAL raw content — so PII in any non-final turn reached the
        upstream LLM verbatim, defeating the "the model never sees raw PII"
        guarantee for any multi-turn chat (the common case for SDK/history replay).
        ``redacted_content`` is now only a SIGNAL that input redaction fired; we
        redact each message's content in place with the SAME deterministic
        redactor the input scanner uses (patterns.redact_all), covering str and
        multimodal text parts. System messages (instructions) are left untouched.
        """
        if redacted_content is None or not body.get("messages"):
            return body

        def _redact_msg_text(text: str) -> str:
            # Shared chat/Responses redactor: redact_all + a fail-closed digit backstop
            # keyed off ``redacted_content`` (the firewall's "what must never reach the
            # model"). See _redact_text_with_backstop for the full rationale.
            return _redact_text_with_backstop(text, redacted_content)

        new_messages = []
        for m in body["messages"]:
            if not isinstance(m, dict) or m.get("role") == "system":
                new_messages.append(m)
                continue
            c = m.get("content")
            if isinstance(c, str) and c:
                new_messages.append({**m, "content": _redact_msg_text(c)})
            elif isinstance(c, list):
                parts = [
                    ({**p, "text": _redact_msg_text(p["text"])}
                     if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"]
                     else p)
                    for p in c
                ]
                new_messages.append({**m, "content": parts})
            else:
                new_messages.append(m)
        result = {**body, "messages": new_messages}

        # Tool-definition free text reaches the upstream LLM too. ``_extract_tool_definitions_text``
        # (G7) already FOLDS tools[].function.{name,description} into the scanned prompt,
        # so PII/secrets smuggled in a tool definition trigger the same verdict — but
        # redaction historically masked only ``messages``, forwarding the tool-def text
        # RAW on a redact-and-forward path (the verdict said "mask it", the wire showed
        # it raw — the exact leak class this method was created to close for messages).
        # Mask every free-text ``description`` string at any depth (function.description
        # + nested JSON-Schema parameter descriptions) with the SAME redactor. Structural
        # identifiers (name, type, enum, required, …) are intentionally left intact so the
        # function-calling contract still resolves; a description is pure natural language
        # where PII realistically hides and masking it never breaks a tool call.
        tools = body.get("tools")
        if isinstance(tools, list):
            result["tools"] = [_redact_tool_descriptions(t, _redact_msg_text) for t in tools]
        return result

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
            # M-03 (allowlist bypass) fix: the requested model is NOT in a present
            # allowlist, so we MUST NOT use it (previously the multi-item branch fell
            # through to ``model = requested_model``, allowing a non-allowlisted model).
            # Fail closed: pick the first allowlisted model that is also runtime-resolvable,
            # otherwise just the first allowlist member. Sort for deterministic selection.
            # A rerouted/compliant-fallback model that IS allowed still hits the ``else``
            # branch below and resolves normally, so reroute is unaffected.
            allowed_candidates = sorted(inference_allowlist)
            model = next(
                (m for m in allowed_candidates if m in self._active_model_names),
                allowed_candidates[0],
            )
        else:
            model = self._resolve_runtime_model(requested_model)
        if inference_allowlist and model not in inference_allowlist:
            # Never re-assign a non-allowlisted model: if runtime resolution drifted
            # outside the allowlist, fail closed onto an allowlisted (and, where possible,
            # runtime-resolvable) model instead of the original requested_model.
            allowed_candidates = sorted(inference_allowlist)
            model = next(
                (m for m in allowed_candidates if m in self._active_model_names),
                allowed_candidates[0],
            )
        if model != requested_model:
            LOG.warning(
                "Requested model '%s' is not active in router model groups; remapping to '%s'",
                requested_model,
                model,
            )
            if self._remap_telemetry_hook is not None:
                try:
                    self._remap_telemetry_hook(
                        requested_model=requested_model,
                        resolved_model=model,
                        body=body,
                    )
                except Exception:  # pragma: no cover - telemetry must never break routing
                    LOG.debug("model_remap telemetry hook failed", exc_info=True)
        messages = body.get("messages") or body.get("input") or []
        # H7: org-qualify the routing key so litellm selects THIS org's deployment
        # (and its BYOK key), never a same-named peer from another tenant. The
        # router deployments are keyed `{org}::{model}`; the bare name stays
        # everywhere else. Qualify only when the qualified key actually exists in
        # the router (fail-safe to bare so a missing tag never 404s a request).
        _org = str(body.get("_zs_org_slug") or "")
        _route_model = model
        if _org and "::" not in str(model):
            _qualified = f"{_org}::{model}"
            if _qualified in self._qualified_model_names:
                _route_model = _qualified
        kwargs = {
            "model": _route_model,
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
        # H2: disable LiteLLM's constructor cross-model auto-failover on the chat
        # path. That map is built from the STATIC catalog and is blind to the live
        # kill_switch:*/model_state:* override namespaces, so a retryable provider
        # error (429/5xx/timeout) on the requested model could silently fail over
        # to ANY org chat model — including one an operator kill-switched/isolated.
        # The gateway's OWN compliant_chain (below, kill-switch/model-state
        # re-checked per G6/B7) owns all vetted failover; num_retries still retries
        # the SAME allowed model for transient errors.
        kwargs["fallbacks"] = []

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
        except (BadRequestError, NotFoundError, APIConnectionError) as exc:
            if allowlist and compliant_chain:
                primary = kwargs.get("model")
                for candidate in compliant_chain:
                    if candidate == primary:
                        continue
                    try:
                        retry_kwargs = {**kwargs, "model": self._qualify_like_primary(candidate, primary)}
                        response = await self._execute_completion(retry_kwargs)
                        LOG.warning(
                            "Compliant fallback retry: %s -> %s after %s",
                            primary,
                            candidate,
                            type(exc).__name__,
                        )
                        return 200, response.model_dump()
                    except ValueError as resolve_exc:
                        LOG.warning(
                            "Skipping unroutable compliant fallback candidate '%s': %s",
                            candidate,
                            resolve_exc,
                        )
                        continue
                    except (BadRequestError, NotFoundError, APIConnectionError):
                        continue
                    except tuple(_EXCEPTION_STATUS_MAP.keys()):
                        continue
                    except Exception as retry_exc:
                        LOG.warning(
                            "Compliant fallback candidate '%s' failed (%s); trying next",
                            candidate,
                            type(retry_exc).__name__,
                        )
                        continue
            if allowlist:
                status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
                LOG.warning(
                    "Org-scoped inference model '%s' failed (%s); no compliant fallback: %s",
                    kwargs.get("model"),
                    type(exc).__name__,
                    exc,
                )
                return status, {
                    "error": {
                        "message": _sanitize_exception_message(exc, status),
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
                    LOG.warning("LiteLLM fallback retry error [%s %d]: %s", type(retry_exc).__name__, status, _scrub_internal_topology(retry_exc))
                    return status, {
                        "error": {
                            "message": _sanitize_exception_message(retry_exc, status),
                            "type": type(retry_exc).__name__,
                            "code": status,
                        }
                    }
                except Exception as retry_exc:
                    LOG.exception("Unexpected fallback retry LLM error")
                    return 502, {
                        "error": {
                            "message": _sanitize_exception_message(retry_exc, 502),
                            "type": "internal_error",
                        }
                    }
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
            LOG.warning("LiteLLM error [%s %d]: %s", type(exc).__name__, status, _scrub_internal_topology(exc))
            return status, {
                "error": {
                    "message": _sanitize_exception_message(exc, status),
                    "type": type(exc).__name__,
                    "code": status,
                }
            }
        except tuple(_EXCEPTION_STATUS_MAP.keys()) as exc:
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
            LOG.warning("LiteLLM error [%s %d]: %s", type(exc).__name__, status, _scrub_internal_topology(exc))
            return status, {
                "error": {
                    "message": _sanitize_exception_message(exc, status),
                    "type": type(exc).__name__,
                    "code": status,
                }
            }
        except _ROUTER_THROTTLE_EXCS as exc:
            # C7: no healthy deployment / router throttle -> honest 503, not 502.
            LOG.warning("LiteLLM no-deployment/throttle [503]: %s", _scrub_internal_topology(exc))
            return 503, {
                "error": {
                    "message": _sanitize_exception_message(exc, 503),
                    "type": "ServiceUnavailableError",
                    "code": 503,
                }
            }
        except Exception as exc:
            LOG.exception("Unexpected LLM error")
            return 502, {
                "error": {
                    "message": _sanitize_exception_message(exc, 502),
                    "type": "internal_error",
                }
            }

    async def _stream_with_state_check(
        self,
        response,
        client_model: str = "",
        state_check: "Callable[[], Awaitable[bool]] | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Wrap ``_stream_chunks_from_response`` with a THROTTLED mid-stream
        kill-switch / model-state re-check (E13).

        ``state_check`` is an async callback that returns ``True`` ONLY on a
        DEFINITIVE kill/isolate verdict for the active model, and ``False``
        otherwise — including on any Redis/exception error (the caller fails open).
        It is called at most once every ``_MIDSTREAM_KS_CHECK_EVERY_CHUNKS`` chunks
        AND no more than once per ``_MIDSTREAM_KS_CHECK_INTERVAL_S`` seconds, so the
        per-token streaming hot path stays cheap.

        On a ``True`` verdict we STOP yielding, aclose() the upstream chunk
        generator (so upstream generation halts) and raise ``_MidStreamKillSwitch``
        — ``acompletion_stream`` catches it and emits the terminal error SSE. The
        prefix already yielded to the client is preserved; the remainder is not
        emitted. When ``state_check`` is None this is a transparent passthrough.
        """
        inner = self._stream_chunks_from_response(response, client_model=client_model)
        if state_check is None:
            async for chunk in inner:
                yield chunk
            return

        chunks_since_check = 0
        last_check_ts = time.perf_counter()
        try:
            async for chunk in inner:
                yield chunk
                chunks_since_check += 1
                now = time.perf_counter()
                if (
                    chunks_since_check >= _MIDSTREAM_KS_CHECK_EVERY_CHUNKS
                    or (now - last_check_ts) >= _MIDSTREAM_KS_CHECK_INTERVAL_S
                ):
                    chunks_since_check = 0
                    last_check_ts = now
                    killed = False
                    try:
                        killed = bool(await state_check())
                    except Exception:
                        # FAIL-OPEN: never terminate a legitimate live stream
                        # because the re-check (Redis/callback) hiccuped. Only a
                        # definitive kill/isolate verdict ends the stream.
                        killed = False
                    if killed:
                        # Halt upstream generation, then signal the terminal-SSE
                        # path. The remainder is NOT yielded. Close BOTH the
                        # wrapper generator AND the raw upstream response: throwing
                        # GeneratorExit into the wrapper does not reliably finalize
                        # a hand-rolled async-iterator upstream, so close it
                        # directly too (if it exposes aclose()) so upstream token
                        # generation actually stops.
                        await self._aclose_quietly(inner)
                        await self._aclose_quietly(response)
                        raise _MidStreamKillSwitch()
        finally:
            # Ensure the upstream is closed on ANY exit (normal completion, early
            # return, client disconnect, or the kill-switch raise) so we never
            # leak an open upstream stream.
            await self._aclose_quietly(inner)
            await self._aclose_quietly(response)

    @staticmethod
    async def _aclose_quietly(obj) -> None:
        """Best-effort aclose() of an async generator / streaming response. A
        finalization failure must never propagate (it would mask the real
        terminal verdict / completion path)."""
        aclose = getattr(obj, "aclose", None)
        if aclose is None:
            return
        try:
            await aclose()
        except Exception:
            pass

    async def _stream_chunks_from_response(self, response, client_model: str = "") -> AsyncGenerator[str, None]:
        """Emit SSE chunks from an active LiteLLM streaming response.

        ``client_model`` is the client-facing model name (the requested/org-facing
        alias). When set, each chunk's ``model`` field is rewritten to it so the
        RAW upstream provider id (e.g. "anthropic/claude-3-5-haiku") never leaks in
        the stream — matching the non-stream MODEL-ID LEAK fix in proxy_chat.
        """
        async for chunk in response:
            chunk_dict = chunk.model_dump()
            # Defense-in-depth: a provider/litellm chunk must NEVER carry a raw
            # error (traceback / container paths / platform model id / fallback
            # topology) to the client. The try/except branches below already
            # sanitize errors raised as exceptions; this catches an error that
            # litellm surfaces as a passthrough CHUNK instead of raising.
            if isinstance(chunk_dict, dict) and chunk_dict.get("error"):
                LOG.warning("Sanitized provider error chunk in stream")
                chunk_dict["error"] = {
                    "message": _sanitize_exception_message(None, 502),
                    "type": "upstream_error",
                    "code": 502,
                }
            if isinstance(chunk_dict, dict):
                if client_model and chunk_dict.get("model"):
                    chunk_dict["model"] = client_model
                # TOPOLOGY LEAK FIX: litellm/OpenRouter stream chunks also carry the
                # upstream `provider` (e.g. "Amazon Bedrock") and cost-basis fields;
                # strip them the same way the non-stream path does (not OpenAI-schema,
                # not consumed by clients).
                chunk_dict.pop("provider", None)
                _u = chunk_dict.get("usage")
                if isinstance(_u, dict):
                    for _k in ("cost", "cost_details", "is_byok"):
                        _u.pop(_k, None)
                # OAS-LEAK (class): strip upstream-internal passthrough from each chunk
                # — normalized id (gen-… -> chatcmpl-…), top-level `citations`
                # (OpenRouter/Perplexity), and provider_specific_fields (reasoning_details
                # .format = provider family) + native_finish_reason at choice + delta level.
                _cid = chunk_dict.get("id")
                if isinstance(_cid, str) and _cid and not _cid.startswith("chatcmpl-"):
                    _p = _cid.split("-", 1)
                    chunk_dict["id"] = "chatcmpl-" + (_p[1] if len(_p) == 2 else _cid)
                chunk_dict.pop("citations", None)
                # OAS-LEAK-STREAM-ROOT-PSF: litellm also exposes provider_specific_fields
                # at the CHUNK ROOT (not just choice/delta) — strip it here too.
                chunk_dict.pop("provider_specific_fields", None)
                for _ch in (chunk_dict.get("choices") or []):
                    if not isinstance(_ch, dict):
                        continue
                    _ch.pop("provider_specific_fields", None)
                    _ch.pop("native_finish_reason", None)
                    _delta = _ch.get("delta")
                    if isinstance(_delta, dict):
                        _delta.pop("provider_specific_fields", None)
            yield f"data: {json.dumps(chunk_dict)}\n\n"
        yield "data: [DONE]\n\n"

    async def acompletion_stream(
            self,
            body: dict,
            redacted_content: str | None = None,
            metrics: "StreamRunMetrics | None" = None,
            echo_model: str | None = None,
            state_check: "Callable[[], Awaitable[bool]] | None" = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming completion. Yields SSE-formatted chunks.

        Optional ``metrics`` (from stream_orchestration) records provider start,
        first-token time, and fallback-before-first-token (no mid-stream switch).

        E13: optional ``state_check`` is an async callback returning ``True`` ONLY
        on a DEFINITIVE kill-switch / model-state-isolation verdict for the ACTIVE
        model. When provided it is invoked THROTTLED inside the chunk loop (see
        ``_stream_with_state_check``); on a kill verdict the stream STOPS emitting
        the remainder, the upstream generator is aclose()'d, and a terminal error
        SSE is emitted. It FAILS OPEN on any error, so a Redis hiccup never
        terminates a legitimate stream.
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
        kwargs["fallbacks"] = []  # H2: see acompletion — gateway owns vetted failover, not LiteLLM's catalog-blind constructor map
        # MODEL-ID LEAK FIX: the client-facing model name to echo in every streamed
        # chunk, so the raw upstream id in kwargs["model"] never leaks. Prefer the
        # caller-supplied echo_model (the ORIGINAL requested model, for parity with
        # the non-stream path) and fall back to body["model"] (the resolved alias);
        # strip any org-qualification ("org::model" -> "model").
        _client_model = str(echo_model or body.get("model") or "").split("::")[-1]

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
            async for chunk in self._stream_with_state_check(response, client_model=_client_model, state_check=state_check):
                yield _track_chunk(chunk)
            local_metrics.completed = True
        except _MidStreamKillSwitch as ks_exc:
            # E13: the ACTIVE model was kill-switched / model-state-isolated mid-
            # stream. The prefix already emitted stays; we stop here and end the
            # stream with a terminal error SSE (the upstream gen was aclose()'d).
            LOG.warning("Mid-stream kill-switch: halting active stream (%s)", ks_exc.reason or "model disabled")
            error_chunk = {
                "error": {
                    "message": "Model disabled by operator kill-switch; stream terminated.",
                    "type": "ServiceUnavailableError",
                    "code": 503,
                }
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            local_metrics.had_error = True
            return
        except (BadRequestError, NotFoundError) as exc:
            if allowlist and compliant_chain and not emitted:
                primary = kwargs.get("model")
                for candidate in compliant_chain:
                    if candidate == primary:
                        continue
                    try:
                        retry_kwargs = {**kwargs, "model": self._qualify_like_primary(candidate, primary)}
                        local_metrics.fallback_before_first_token = True
                        response = await self._execute_completion(retry_kwargs)
                        async for chunk in self._stream_with_state_check(response, client_model=_client_model, state_check=state_check):
                            yield _track_chunk(chunk)
                        local_metrics.completed = True
                        return
                    except _MidStreamKillSwitch as ks_exc:
                        # E13: fallback model killed mid-stream — terminate cleanly.
                        LOG.warning("Mid-stream kill-switch on compliant fallback: halting (%s)", ks_exc.reason or "model disabled")
                        yield f"data: {json.dumps({'error': {'message': 'Model disabled by operator kill-switch; stream terminated.', 'type': 'ServiceUnavailableError', 'code': 503}})}\n\n"
                        yield "data: [DONE]\n\n"
                        local_metrics.had_error = True
                        return
                    except ValueError as resolve_exc:
                        LOG.warning(
                            "Skipping unroutable compliant fallback candidate '%s': %s",
                            candidate,
                            resolve_exc,
                        )
                        continue
                    except (BadRequestError, NotFoundError):
                        continue
                    except tuple(_EXCEPTION_STATUS_MAP.keys()):
                        continue
                    except Exception as retry_exc:
                        LOG.warning(
                            "Compliant stream fallback candidate '%s' failed (%s); trying next",
                            candidate,
                            type(retry_exc).__name__,
                        )
                        continue
            if allowlist:
                status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
                LOG.warning(
                    "LiteLLM stream org-scoped error [%s %d]: %s",
                    type(exc).__name__, status, exc,
                )
                error_chunk = {
                    "error": {"message": _sanitize_exception_message(exc, status), "type": type(exc).__name__, "code": status}
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
                    async for chunk in self._stream_with_state_check(response, client_model=_client_model, state_check=state_check):
                        yield _track_chunk(chunk)
                    local_metrics.completed = True
                    return
                except _MidStreamKillSwitch as ks_exc:
                    # E13: fallback model killed mid-stream — terminate cleanly
                    # (must precede the generic `except Exception` below, which a
                    # _MidStreamKillSwitch would otherwise be swallowed by).
                    LOG.warning("Mid-stream kill-switch on default fallback: halting (%s)", ks_exc.reason or "model disabled")
                    yield f"data: {json.dumps({'error': {'message': 'Model disabled by operator kill-switch; stream terminated.', 'type': 'ServiceUnavailableError', 'code': 503}})}\n\n"
                    yield "data: [DONE]\n\n"
                    local_metrics.had_error = True
                    return
                except tuple(_EXCEPTION_STATUS_MAP.keys()) as retry_exc:
                    status = _EXCEPTION_STATUS_MAP.get(type(retry_exc), 502)
                    LOG.warning("LiteLLM stream fallback retry error [%s %d]: %s", type(retry_exc).__name__, status, _scrub_internal_topology(retry_exc))
                    error_chunk = {
                        "error": {"message": _sanitize_exception_message(retry_exc, status), "type": type(retry_exc).__name__, "code": status}
                    }
                    yield f"data: {json.dumps(error_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    local_metrics.had_error = True
                    return
                except Exception as retry_exc:
                    LOG.exception("Unexpected fallback retry LLM stream error")
                    error_chunk = {"error": {"message": _sanitize_exception_message(retry_exc, 502), "type": "internal_error"}}
                    yield f"data: {json.dumps(error_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    local_metrics.had_error = True
                    return
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
            LOG.warning("LiteLLM stream error [%s %d]: %s", type(exc).__name__, status, _scrub_internal_topology(exc))
            error_chunk = {
                "error": {"message": _sanitize_exception_message(exc, status), "type": type(exc).__name__, "code": status}
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            local_metrics.had_error = True
            return
        except tuple(_EXCEPTION_STATUS_MAP.keys()) as exc:
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
            LOG.warning("LiteLLM stream error [%s %d]: %s", type(exc).__name__, status, _scrub_internal_topology(exc))
            error_chunk = {
                "error": {"message": _sanitize_exception_message(exc, status), "type": type(exc).__name__, "code": status}
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            local_metrics.had_error = True
        except _ROUTER_THROTTLE_EXCS as exc:
            # C7: no healthy deployment / router throttle -> honest 503, not 502.
            LOG.warning("LiteLLM stream no-deployment/throttle [503]: %s", _scrub_internal_topology(exc))
            error_chunk = {"error": {"message": _sanitize_exception_message(exc, 503), "type": "ServiceUnavailableError", "code": 503}}
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            local_metrics.had_error = True
        except Exception as exc:
            LOG.exception("Unexpected LLM stream error")
            error_chunk = {"error": {"message": _sanitize_exception_message(exc, 502), "type": "internal_error"}}
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            local_metrics.had_error = True
    
    async def aembedding(
            self,
            body: dict,
    ) -> tuple[int, dict]:
        """Create embeddings. Returns (http_status, response_dict)."""
        model = body.get("model") or _DEFAULT_EMBEDDING_MODEL
        model = self._normalize_model_alias(model)

        # R2 (defense-in-depth): a reserved platform/guard model name must NEVER
        # be served via LiteLLM — and on the embedding path it would otherwise be
        # silently answered by the single embedding deployment. Reject it with a
        # GENERIC message; never echo the internal model id. Mirrors
        # ``_resolve_runtime_model`` on the completion path.
        if is_platform_model_name(model):
            LOG.warning("Rejected reserved platform model on embedding path")
            return 404, {
                "error": {
                    "message": _CLIENT_SAFE_STATUS_MESSAGES[404],
                    "type": "invalid_request_error",
                    "code": "model_not_allowed",
                }
            }

        # Silent-substitution fix: a chat model or any unknown/wrong-type alias
        # previously fell through to the single embedding deployment. Fail CLOSED
        # for anything that is not a known embedding model instead of substituting.
        if model not in self._allowed_embedding_model_names():
            LOG.warning("Rejected non-embedding/unknown model on embedding path")
            return 404, {
                "error": {
                    "message": _CLIENT_SAFE_STATUS_MESSAGES[404],
                    "type": "invalid_request_error",
                    "code": "model_not_allowed",
                }
            }

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

        # R6: validate EVERY item is a string before dispatch. Nested lists /
        # mixed types previously reached LiteLLM, which retried each malformed
        # item across num_retries * fallback models — a 120s+ retry-storm DoS.
        # Fail fast with a 400 instead.
        if not all(isinstance(item, str) for item in input_list):
            return 400, {
                "error": {
                    "message": "`input` must be a string or a flat list of strings.",
                    "type": "invalid_request_error",
                }
            }

        # Defensive input-size ceiling (mirrors the chat/output-guard caps): an
        # oversized payload would be forwarded to the upstream deployment and, on
        # failure, re-tried across fallbacks — a resource-exhaustion vector.
        if sum(len(item) for item in input_list) > _MAX_EMBEDDING_INPUT_CHARS:
            return 400, {
                "error": {
                    "message": "`input` is too large.",
                    "type": "invalid_request_error",
                }
            }

        # H7: org-qualify the routing key (mirror _build_kwargs) so litellm selects
        # THIS org's embedding deployment + its own BYOK key. The allowed-names
        # gate above validated the BARE client name; the router deployments are
        # keyed {org}::{model}. Fail-safe to bare if no qualified key exists.
        _org = str(body.get("_zs_org_slug") or "")
        _route_model = model
        if _org and "::" not in str(model):
            _q = f"{_org}::{model}"
            if _q in self._qualified_model_names:
                _route_model = _q
        kwargs = {
            "model": _route_model,
            "input": input_list,
        }
        # Pass encoding_format if specified
        if "encoding_format" in body:
            kwargs["encoding_format"] = body["encoding_format"]
        if "dimensions" in body:
            kwargs["dimensions"] = body["dimensions"]
        # R6: restrict embedding fallbacks to embedding models only. The router's
        # global fallback map is built from chat models; without this an embedding
        # failure would fall back onto chat models (and add to the retry storm).
        if self._router is not None:
            kwargs["fallbacks"] = self._embedding_fallbacks_for(_route_model)

        try:
            response = await self._execute_embedding(kwargs)
            return 200, response.model_dump() if hasattr(response, "model_dump") else dict(response)
        except tuple(_EXCEPTION_STATUS_MAP.keys()) as exc:
            status = _EXCEPTION_STATUS_MAP.get(type(exc), 502)
            LOG.warning("LiteLLM embedding error [%s %d]: %s", type(exc).__name__, status, _scrub_internal_topology(exc))
            return status, {
                "error": {
                    "message": _sanitize_exception_message(exc, status),
                    "type": type(exc).__name__,
                    "code": status,
                }
            }
        except Exception as exc:
            LOG.exception("Unexpected embedding error")
            return 502, {
                "error": {
                    "message": _sanitize_exception_message(exc, 502),
                    "type": "internal_error",
                }
            }

    def _allowed_embedding_model_names(self) -> set[str]:
        """Names that may legitimately be served as an embedding request.

        An embedding model is one whose router entry resolves to the
        ``embedding`` mode (same check used to build embedding-only fallbacks).
        Reserved platform models are excluded. The default deployment name is
        always allowed so a no-model / default request keeps working even when
        no router is configured (legacy single-deployment mode).
        """
        allowed = {self._normalize_model_alias(_DEFAULT_EMBEDDING_MODEL)}
        router = self._router
        if router is None or not hasattr(router, "model_list"):
            return allowed
        for entry in router.model_list:
            if not isinstance(entry, dict) or self._is_reserved_model_entry(entry):
                continue
            # H7 regression fix: clients send the BARE model name, but H7 rewrote
            # router model_name to {org}::{model}. Use base_model_name so the gate
            # accepts the bare client name (the route is org-qualified separately
            # in aembedding). Without this, /v1/embeddings 404'd for every org.
            name = self._normalize_model_alias(str((entry.get("model_info") or {}).get("base_model_name") or entry.get("model_name") or ""))
            if not name:
                continue
            litellm_id = str((entry.get("litellm_params") or {}).get("model") or "")
            if self._is_embedding_model_id(litellm_id):
                allowed.add(name)
        return allowed

    def _embedding_fallbacks_for(self, model: str) -> list[dict]:
        """R6: build an embedding-only fallback list for an embedding request.

        Only models whose litellm id resolves to the ``embedding`` mode are
        eligible — chat models must never be an embedding fallback. Reserved
        platform models are excluded as well. Returns ``[]`` when no other
        embedding model is available (disables cross-type fallback entirely).
        """
        router = self._router
        if router is None or not hasattr(router, "model_list"):
            return []
        embedding_names: list[str] = []
        # H7: the requested model is now an org-qualified routing key ({org}::name).
        # Keep embedding fallbacks WITHIN that org so a failure can't fall back to
        # ANOTHER tenant's embedding deployment + BYOK key.
        _model_org = str(model).split("::", 1)[0] if "::" in str(model) else ""
        for entry in router.model_list:
            if not isinstance(entry, dict):
                continue
            if self._is_reserved_model_entry(entry):
                continue
            # Fallback TARGETS must be the qualified routing keys litellm knows.
            name = self._normalize_model_alias(str(entry.get("model_name") or ""))
            if not name or name == model or name in embedding_names:
                continue
            if _model_org and "::" in name and not name.startswith(f"{_model_org}::"):
                continue
            litellm_id = str((entry.get("litellm_params") or {}).get("model") or "")
            if not self._is_embedding_model_id(litellm_id):
                continue
            embedding_names.append(name)
        if not embedding_names:
            return []
        return [{model: embedding_names}]

    @staticmethod
    def _is_embedding_model_id(litellm_id: str) -> bool:
        """Best-effort check that a litellm model id is an embedding model."""
        mid = (litellm_id or "").strip().lower()
        if not mid:
            return False
        if "embed" in mid:
            return True
        try:
            info = litellm.get_model_info(mid)
            return str((info or {}).get("mode") or "").lower() == "embedding"
        except Exception:
            return False

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

        # H7: org-qualify each deployment's litellm ROUTING KEY to `{org}::{model}`
        # so two tenants' same-named models cannot become load-balanced peers (and
        # cross-use their distinct BYOK keys). The BARE name is kept in
        # model_info.base_model_name for active-name validation / telemetry; the
        # request path (_build_kwargs) qualifies kwargs['model'] the same way.
        for _e in valid_models:
            if not isinstance(_e, dict):
                continue
            _org = str(_e.get("_zs_org") or "")
            _bare = str(_e.get("model_name") or "")
            if _org and _bare and "::" not in _bare:
                _mi = _e.get("model_info")
                if not isinstance(_mi, dict):
                    _mi = {}
                    _e["model_info"] = _mi
                _mi["base_model_name"] = _bare
                _e["model_name"] = f"{_org}::{_bare}"
        self._qualified_model_names = {
            _e["model_name"]
            for _e in valid_models
            if isinstance(_e, dict) and _e.get("model_name") and "::" in str(_e["model_name"])
        }

        # Responses API: litellm.Router exposes NO aresponses in this version, so
        # stash each deployment's resolved litellm_params (decrypted api_key +
        # api_base + upstream model id) keyed by BOTH the qualified model_name and
        # the bare base_model_name. aresponses() routes via module-level
        # litellm.aresponses with these explicit BYOK creds (the byok_embedder
        # pattern), preserving per-tenant key isolation.
        _dep: dict[str, dict] = {}
        for _e in valid_models:
            if not isinstance(_e, dict):
                continue
            _lp = _e.get("litellm_params")
            if not isinstance(_lp, dict):
                continue
            _nm = str(_e.get("model_name") or "")
            if _nm:
                _dep[_nm] = _lp
            _bn = str((_e.get("model_info") or {}).get("base_model_name") or "")
            if _bn and _bn not in _dep:
                _dep[_bn] = _lp
        self._deployment_params = _dep

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
    def estimate_prompt_tokens(messages: list[dict], max_tokens: int = 0, n: int = 1) -> int:
        prompt_chars = 0
        for message in messages or []:
            content = message.get("content", "") if isinstance(message, dict) else ""
            if isinstance(content, list):
                content = " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
            prompt_chars += len(str(content or ""))
        prompt_tokens = max(1, prompt_chars // 4) if prompt_chars else 0
        # Charge the requested OUTPUT budget too: a request for ``n`` completions
        # of ``max_tokens`` each can generate max_tokens*n output tokens, so fold
        # that into the pre-inference TPM reservation (else a high n/max_tokens
        # request under-charges the rate limiter — a DoS lever). (#4)
        try:
            _n = max(1, int(n or 1))
        except (TypeError, ValueError):
            _n = 1
        return max(prompt_tokens + max(max_tokens or 0, 0) * _n, 1)

    @staticmethod
    def _normalize_weights(weights: dict[str, float] | None = None) -> dict[str, float]:
        resolved = {**_DEFAULT_ROUTING_WEIGHTS, **(weights or {})}
        # R2-RT-3: guard a non-positive sum (not just zero). A negative total would
        # INVERT every normalized component (caller-driven worst-model selection); the
        # per-request weights are clamped to [0,1] upstream (_weight), but defend in
        # depth here so an org-config or future caller path can't reintroduce it.
        total = sum(resolved.values())
        if total <= 0:
            total = 1.0
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
        # Lowercase: a mixed-case caller value ('RESTRICTED') must not miss the
        # lowercase dict keys and collapse req level to 0 (public) — that would
        # silently route restricted data onto a public model.
        req_sens_level = _req_sensitivity_level(data_sensitivity)
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
            model_sens = _SENSITIVITY_ORDER.get(str(model.get("data_sensitivity_level", "public")).strip().lower(), 0)
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
        adjudicator_model: str | None = None,
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

        # ── H5 perf: short-circuit the synchronous Bedrock adjudicator LLM call ──
        # The deterministic weighted heuristic above already picked a model. The
        # Bedrock adjudicator (Claude Haiku ~2-3s/request) only adds decision value
        # when there is a genuine, governance-sensitive choice to make. Skip it when:
        #   (a) there is at most one candidate — there is NO routing decision; or
        #   (b) the request is low-risk AND carries no compliance constraints.
        # This removes the dominant per-request latency (7-12s observed) for the
        # common trivial path. Operators can force full adjudication with
        # ROUTING_ADJUDICATOR_ALWAYS=true; the risk floor is tunable.
        _adj_always = os.getenv("ROUTING_ADJUDICATOR_ALWAYS", "false").lower() in ("1", "true", "yes")
        _adj_risk_floor = float(os.getenv("ROUTING_ADJUDICATOR_RISK_FLOOR", "0.30"))
        _no_real_choice = len(scored) <= 1
        _low_risk = (request_risk_score < _adj_risk_floor) and not (required_compliance or [])
        if not _adj_always and (_no_real_choice or _low_risk):
            heuristic.decision_source = "weighted_fastpath"
            heuristic.requested_model = preferred_model or "auto"
            heuristic.evaluator_model = "deterministic_weighted"
            heuristic.policy_summary = (
                "Single candidate — no routing decision required."
                if _no_real_choice
                else "Low-risk request routed by deterministic weighted scoring (adjudicator skipped for latency)."
            )
            heuristic.decision_factors = (heuristic.decision_factors or []) + [
                "adjudicator_skipped_single_candidate" if _no_real_choice else "adjudicator_skipped_low_risk"
            ]
            return heuristic

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

        adjudicator_bedrock_model = resolve_platform_bedrock_model(
            "adjudicator",
            adjudicator_model,
        )
        adjudicator_system = (
            "You are ZeroShield's /v1/chat/completions routing adjudicator — "
            "a platform Bedrock model that analyzes user input and governance "
            "settings to select the optimal organization LLM for each request.\n\n"
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
        )
        adjudicator_user = json.dumps(adjudicator_prompt, ensure_ascii=True)
        adjudicator_max_tokens = int(os.getenv("BEDROCK_ADJUDICATOR_MAX_TOKENS", "200"))

        _log = logging.getLogger("gateway")
        code = 502
        response: dict[str, Any] = {}
        try:
            from ai_mesh_gateway.bedrock_client import default_bedrock_client

            bedrock_client = default_bedrock_client()
            result = await asyncio.to_thread(
                bedrock_client.converse,
                model=adjudicator_bedrock_model,
                system_text=adjudicator_system,
                user_text=adjudicator_user,
                max_tokens=adjudicator_max_tokens,
                temperature=0.0,
                call_site="adjudicator",
            )
            response = result.get("raw") or {}
            code = 200
        except Exception as exc:
            _log.warning("Bedrock adjudicator converse failed: %s", exc)

        _log.info(
            "Bedrock adjudicator call: model=%s, backend=bedrock, call_site=adjudicator, "
            "candidates=%d, status=%d",
            adjudicator_bedrock_model,
            len(scored),
            code,
        )
        if code != 200 or not isinstance(response, dict):
            heuristic.reason = f"Policy adjudicator unavailable; {heuristic.reason}"
            heuristic.decision_source = "weighted_fallback"
            heuristic.evaluator_model = adjudicator_bedrock_model
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
            heuristic.evaluator_model = adjudicator_bedrock_model
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
                f"ZeroShield Policy Adjudicator selected '{resolved_name}' "
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
            evaluator_model=adjudicator_bedrock_model,
            policy_summary=bedrock_policy,
            decision_factors=[str(item) for item in decision_factors if item],
            candidate_count=len(scored),
        )
