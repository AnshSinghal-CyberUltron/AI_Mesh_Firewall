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
from typing import Any, AsyncGenerator, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from stream_orchestration import StreamRunMetrics

import litellm
from litellm import Router as LiteLLMRouter

from ai_mesh_shared.llm_model_crypto import decrypt_api_key
from ai_mesh_shared.litellm_byok import normalize_litellm_params

from ai_mesh_gateway.platform_models import is_platform_model_name

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

# Load-test only: skip BYOK/LiteLLM and return an instant tiny completion so
# saturation runs measure gateway addon (policy + scan + routing + output
# guard) without billing the customer model. Default OFF. Never honor a
# client header — env on the gateway process only.
_STUB_LLM_TRUTHY = frozenset({"1", "true", "yes", "on"})
_STUB_LLM_WARNED = False
_STUB_TOK_PER_S_MIN = 30.0
_STUB_TOK_PER_S_MAX = 100.0
_STUB_DURATION_MAX_S = 10.0


def loadtest_stub_llm_enabled() -> bool:
    return os.environ.get("GATEWAY_LOADTEST_STUB_LLM", "").strip().lower() in _STUB_LLM_TRUTHY


def loadtest_stub_tok_per_s() -> float:
    raw = os.environ.get("GATEWAY_LOADTEST_STUB_TOK_PER_S", "50")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 50.0
    if v <= 0:
        v = 50.0
    return max(_STUB_TOK_PER_S_MIN, min(_STUB_TOK_PER_S_MAX, v))


def loadtest_stub_duration_s() -> float:
    """Hold time for the token-emitting stub. 0 = instant one-token (default).

    Positive values are capped at 10s. Values in (0, 2) are allowed so unit tests
    can prove the hold without a 2s sleep; soaks set 2–10 via env.
    """
    raw = os.environ.get("GATEWAY_LOADTEST_STUB_DURATION_S", "0")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.0
    if v <= 0:
        return 0.0
    return min(_STUB_DURATION_MAX_S, v)


def _stub_token_count(duration_s: float, tok_per_s: float, body: dict | None = None) -> int:
    if duration_s <= 0:
        return 1
    n = max(1, int(tok_per_s * duration_s))
    max_tokens = (body or {}).get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        n = max(1, min(n, max_tokens))
    return n


def _stub_token_words(n: int) -> list[str]:
    if n <= 1:
        return ["ok"]
    return [f"tok{i}" for i in range(n)]


def loadtest_stub_completion(body: dict | None = None) -> dict:
    """OpenAI-shaped chat.completion with a benign one-token assistant reply."""
    model = str((body or {}).get("model") or "loadtest-stub")
    return {
        "id": "chatcmpl-loadtest-stub",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model.split("::")[-1],
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 8, "completion_tokens": 1, "total_tokens": 9},
    }


def _loadtest_stub_held_completion(body: dict | None = None) -> dict:
    duration = loadtest_stub_duration_s()
    rate = loadtest_stub_tok_per_s()
    n = _stub_token_count(duration, rate, body)
    words = _stub_token_words(n)
    model = str((body or {}).get("model") or "loadtest-stub")
    return {
        "id": "chatcmpl-loadtest-stub",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model.split("::")[-1],
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": " ".join(words)},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": n,
            "total_tokens": 8 + n,
        },
    }


async def loadtest_stub_acompletion(body: dict | None = None) -> dict:
    duration = loadtest_stub_duration_s()
    if duration > 0:
        await asyncio.sleep(duration)
        return _loadtest_stub_held_completion(body)
    return loadtest_stub_completion(body)


async def loadtest_stub_stream(body: dict | None = None) -> AsyncGenerator[str, None]:
    duration = loadtest_stub_duration_s()
    rate = loadtest_stub_tok_per_s()
    meta = loadtest_stub_completion(body)
    if duration <= 0:
        chunk = {
            "id": meta["id"],
            "object": "chat.completion.chunk",
            "created": meta["created"],
            "model": meta["model"],
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"
        return
    n = _stub_token_count(duration, rate, body)
    words = _stub_token_words(n)
    interval = duration / float(n)
    for i, word in enumerate(words):
        await asyncio.sleep(interval)
        piece = word if i == n - 1 else f"{word} "
        delta: dict[str, str] = {"content": piece}
        if i == 0:
            delta = {"role": "assistant", "content": piece}
        chunk = {
            "id": meta["id"],
            "object": "chat.completion.chunk",
            "created": meta["created"],
            "model": meta["model"],
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": "stop" if i == n - 1 else None,
            }],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
    yield "data: [DONE]\n\n"


def _warn_stub_llm_once() -> None:
    global _STUB_LLM_WARNED
    if not _STUB_LLM_WARNED:
        LOG.warning("GATEWAY_LOADTEST_STUB_LLM is ON — BYOK inference is stubbed")
        _STUB_LLM_WARNED = True

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


_SCORE_TIE_EPSILON = 1e-4

# A3: at or above this request_risk_score, models riskier than (1 - request_risk) are
# hard-filtered out of the candidate set. Below it, request risk does not constrain
# candidacy (the soft risk dimension still applies via the operator's risk weight).
_RISK_ESCALATION_FLOOR = float(os.getenv("ROUTING_RISK_ESCALATION_FLOOR", "0.50"))

# D2: a dimension whose candidate span is at or below this is treated as carrying no
# signal (every candidate identical), so min-max normalization returns 1.0 for all
# rather than dividing by ~zero.
_SPAN_EPSILON = 1e-12

# RC-8: canonical compliance-framework identity. Operators type these free-form in the
# model form ("hipaa", "HIPAA", "PCI-DSS", "pci_dss") and clients send them free-form in
# ``compliance_requirements``; the scorer's old exact ``in`` comparison treated casing or
# separator drift as "no model satisfies this tag" and returned a spurious 403. Compare
# canonically on BOTH sides instead. Canonical form: upper-case, non-alphanumerics
# collapsed to "_", so PCI-DSS / pci dss / pci_dss all become PCI_DSS.
_COMPLIANCE_CANON_RE = re.compile(r"[^A-Z0-9]+")

# Aliases folded onto one canonical identity. Keys are already canonicalized.
_COMPLIANCE_ALIASES = {
    "SOC_2": "SOC2",
    "SOC2_TYPE_II": "SOC2",
    "SOC2_TYPE2": "SOC2",
    "ISO_27001": "ISO27001",
    "ISO_IEC_27001": "ISO27001",
    "PCIDSS": "PCI_DSS",
    "PCI": "PCI_DSS",
    "HIPPA": "HIPAA",          # common misspelling, seen in operator input
    "NIST_CSF": "NIST",
    "NIST_800_53": "NIST",
    "GDPR_EU": "GDPR",
}


def canonical_compliance_tag(value: object) -> str:
    """Normalize one compliance-framework token to its canonical identity ('' if empty)."""
    token = _COMPLIANCE_CANON_RE.sub("_", str(value or "").strip().upper()).strip("_")
    if not token:
        return ""
    return _COMPLIANCE_ALIASES.get(token, token)


def _canonical_compliance_set(values: object) -> set[str]:
    """Canonicalize an iterable of compliance tags into a comparable set."""
    if values is None:
        return set()
    if isinstance(values, (str, bytes)):
        values = [values]
    out: set[str] = set()
    try:
        for item in values:  # type: ignore[union-attr]
            tag = canonical_compliance_tag(item)
            if tag:
                out.add(tag)
    except TypeError:
        tag = canonical_compliance_tag(values)
        if tag:
            out.add(tag)
    return out


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
    # Honesty / transparency (routing-bias fix)
    sensitivity_fallback: bool = False
    score_tie: bool = False
    candidate_scores: list[dict] = field(default_factory=list)
    remapped_from: str = ""
    runtime_model: str = ""

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


def _redact_fn_call_arguments(fn, redactor):
    """G59: redact the ``arguments`` of a tool_call.function / legacy function_call dict
    before it reaches the model. A conversation-history assistant turn's
    ``tool_calls[].function.arguments`` is FOLDED into the scanned prompt (G7), so PII/
    secrets there trigger the redact verdict — but ``_apply_redaction`` only masked message
    ``content`` + tool DEFINITIONS, forwarding the tool-CALL arguments RAW (the detect-but-
    don't-enforce class this method's siblings close for content/tools). ``arguments`` is a
    JSON string per spec (a non-conforming parsed DICT is coerced to JSON first); structural
    fields (name, id, type) are left intact so the function-calling contract still resolves."""
    if not isinstance(fn, dict):
        return fn
    args = fn.get("arguments")
    if isinstance(args, str) and args:
        return {**fn, "arguments": redactor(args)}
    if args is not None and not isinstance(args, str):
        try:
            return {**fn, "arguments": redactor(json.dumps(args))}
        except (TypeError, ValueError):
            return fn
    return fn


def _name_carries_pii(name: str) -> bool:
    """G60: True if a participant ``name`` carries real PII/secret/credential (an SSN/phone/
    CC fits the OpenAI name charset). Uses the DETECTORS (obfuscation-aware) rather than the
    digit-backstop redactor, so a benign identifier with a digit run ("session-2024-001") is
    NOT flagged while a name that IS an SSN/token is. Detector-import is lazy (package-safe)."""
    try:
        from patterns import detect_pii, detect_secrets, detect_credential_exposure
    except ImportError:  # pragma: no cover - packaging fallback
        from .patterns import detect_pii, detect_secrets, detect_credential_exposure
    return bool(detect_pii(name) or detect_secrets(name) or detect_credential_exposure(name))


def _redact_message_tool_calls(m, redactor):
    """Return ``m`` with every ``tool_calls[].function.arguments`` and a legacy
    ``function_call.arguments`` redacted (G59). No-op when the message carries neither."""
    out = m
    tcs = m.get("tool_calls")
    if isinstance(tcs, list) and tcs:
        out = {**out, "tool_calls": [
            ({**tc, "function": _redact_fn_call_arguments(tc["function"], redactor)}
             if isinstance(tc, dict) and isinstance(tc.get("function"), dict) else tc)
            for tc in tcs
        ]}
    fc = m.get("function_call")
    if isinstance(fc, dict):
        out = {**out, "function_call": _redact_fn_call_arguments(fc, redactor)}
    return out


def _redact_responses_input_list(items, redactor):
    """G48: redact free-text in a STRUCTURED Responses ``input`` list (the modern
    ``[{"role":..,"content":[{"type":"input_text","text":..}]}]`` shape). ``aresponses``
    only replaced a plain-STRING ``input``, so a list-form input rode to the model RAW
    despite a redact verdict (the exact silent-leak class ``_apply_redaction`` closed for
    chat ``messages``). Each item's ``content`` may be a str or a list of parts with a
    ``text`` field; both are masked. Role==system items are left untouched for parity
    with the chat path's trusted-instructions decision. Structural fields are preserved."""
    if not isinstance(items, list):
        return items
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("role") == "system":
            out.append(it)
            continue
        c = it.get("content")
        if isinstance(c, str) and c:
            out.append({**it, "content": redactor(c)})
        elif isinstance(c, list):
            parts = [
                ({**p, "text": redactor(p["text"])}
                 if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"]
                 else p)
                for p in c
            ]
            out.append({**it, "content": parts})
        else:
            out.append(it)
    return out


class LLMRouter:
    """Async LLM router backed by LiteLLM."""

    def __init__(self, config: dict):
        self._config = config
        self._router: LiteLLMRouter | None = None
        self._active_model_names: list[str] = []
        self._qualified_model_names: set[str] = set()  # H7: org::model routing keys
        self._deployment_params: dict[str, dict] = {}   # Responses API: name -> resolved litellm_params (BYOK)
        # RC-7: model names the LAST reload could not turn into a servable deployment.
        # Excluded from routing candidates so the scorer never ranks a model the
        # router cannot serve (which previously handed the decision to the remap).
        self._unroutable_model_names: set[str] = set()

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
        # H7: use the BARE name (model_info.base_model_name) — clients send bare
        # model names, so validation/active-name matching must stay un-qualified
        # even though the litellm routing key is org-qualified.
        self._active_model_names = [
            self._normalize_model_alias((entry.get("model_info") or {}).get("base_model_name") or entry.get("model_name", ""))
            for entry in model_list
            if (entry.get("model_info") or {}).get("base_model_name") or entry.get("model_name")
        ]

    def _resolve_runtime_model(
        self,
        requested_model: str,
        preferred_active: list[str] | None = None,
    ) -> str:
        """Map a selected model name onto a LiteLLM-active deployment.

        When the selected name is not in the live router groups, prefer the
        highest-scored *active* candidate from ``preferred_active`` (fallback
        chain order) before the org default / first active group — never silently
        pretend the inactive selection was served.
        """
        if is_platform_model_name(requested_model):
            raise ValueError(
                f"Platform model '{requested_model}' cannot be routed via LiteLLM inference. "
                "Use BedrockClient.converse for guard and adjudicator calls."
            )
        if not self._active_model_names:
            return requested_model
        if requested_model in self._active_model_names:
            return requested_model

        for name in preferred_active or []:
            alias = self._normalize_model_alias(str(name or ""))
            if alias and alias in self._active_model_names:
                return alias

        preferred_default = self._normalize_model_alias(self._config.get("litellm_default_model", "") or "")
        if preferred_default and preferred_default in self._active_model_names:
            return preferred_default

        return self._active_model_names[0]

    def resolve_runtime_selection(
        self,
        selection: ModelSelection,
    ) -> ModelSelection:
        """Attach runtime remap honesty onto an existing ModelSelection."""
        if not selection or not selection.model_name:
            return selection
        preferred = [selection.model_name, *(selection.fallback_chain or [])]
        runtime = self._resolve_runtime_model(selection.model_name, preferred_active=preferred)
        selection.runtime_model = runtime
        if runtime != selection.model_name:
            selection.remapped_from = selection.model_name
            factors = list(selection.decision_factors or [])
            factors.append(f"runtime_remap_from={selection.model_name}")
            factors.append(f"runtime_remap_to={runtime}")
            factors.append("inactive_model_remapped")
            selection.decision_factors = factors
            selection.reason = (
                f"{selection.reason} Requested model '{selection.model_name}' is not "
                f"active in router model groups; remapping to '{runtime}'."
            ).strip()
            selection.model_name = runtime
            # Keep model_id aligned with the runtime name when unknown.
            if not selection.model_id or selection.model_id == selection.remapped_from:
                selection.model_id = runtime
        return selection

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
                # RC-7: honor the OpenAI-compat hint that ``_prepare_reload_entry``
                # already stamped via ``normalize_litellm_params``. Without it litellm
                # cannot resolve a BYOK slug carrying a bare vendor prefix
                # (``google/…``, ``nvidia/…``, ``poolside/…``, ``liquid/…``) and the
                # entry was DROPPED at reload — 8 of 11 live OpenRouter models
                # disappeared from the router while the scorer kept ranking all 11,
                # so ``resolve_runtime_selection`` (not the governance weights) chose
                # what actually got served.
                validator(
                    model=model_id,
                    custom_llm_provider=(params or {}).get("custom_llm_provider") or None,
                )
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
                params["api_key"] = decrypted
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
        if redacted_content is not None:
            _inp = body.get("input")
            if isinstance(_inp, str):
                body = {**body, "input": redacted_content}
            elif isinstance(_inp, list):
                # G48: a structured list-form input previously rode to the model RAW —
                # redact each turn's text with the same deterministic redactor.
                body = {**body, "input": _redact_responses_input_list(
                    _inp, lambda s: _redact_text_with_backstop(s, redacted_content)
                )}
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

    def _apply_redaction(
        self,
        body: dict,
        redacted_content: str | None,
        redaction_hints: list | None = None,
    ) -> dict:
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
        if (redacted_content is None and not redaction_hints) or not body.get("messages"):
            return body

        # I-04: apply the POLICY engine's own redact rules per message.
        #
        # ``redacted_content`` is deliberately only a SIGNAL (see above) and the
        # re-derivation uses patterns.redact_all + a DIGIT-run backstop. Both are
        # blind to operator-authored ``redaction_config`` rules that target
        # non-numeric text: a codename or customer name matched by a policy regex
        # was masked in ``redacted_prompt`` (what the trace/telemetry reported) but
        # NEVER on the wire — a phantom redaction that attested success while the
        # raw value reached the provider. There was no channel for a policy mask to
        # reach the wire at all; ``redaction_hints`` is that channel.
        #
        # policy_engine.apply_redaction is reused verbatim so the wire mask is
        # byte-identical to the mask the trace reports, and it is already
        # ReDoS-budgeted (_compile_regex shape rejection + _run_with_timeout per
        # substitution), so an operator regex cannot pin the worker here either.
        _policy_redact = None
        if redaction_hints:
            try:
                try:
                    from policy_engine import apply_redaction as _pol_apply
                except ImportError:
                    from .policy_engine import apply_redaction as _pol_apply
                _policy_redact = _pol_apply
            except Exception:  # noqa: BLE001 - never break inference on an import slip
                _policy_redact = None

        def _redact_msg_text(text: str) -> str:
            # Shared chat/Responses redactor: redact_all + a fail-closed digit backstop
            # keyed off ``redacted_content`` (the firewall's "what must never reach the
            # model"). See _redact_text_with_backstop for the full rationale.
            out = text
            if _policy_redact is not None:
                try:
                    out = _policy_redact(out, redaction_hints)
                except Exception:  # noqa: BLE001 - a bad rule must not drop the mask
                    LOG.warning("policy redaction hint failed; falling back to redact_all")
            if redacted_content is None:
                return out
            return _redact_text_with_backstop(out, redacted_content)

        new_messages = []
        for m in body["messages"]:
            if not isinstance(m, dict) or m.get("role") == "system":
                new_messages.append(m)
                continue
            c = m.get("content")
            if isinstance(c, str) and c:
                nm = {**m, "content": _redact_msg_text(c)}
            elif isinstance(c, list):
                parts = [
                    ({**p, "text": _redact_msg_text(p["text"])}
                     if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"]
                     else p)
                    for p in c
                ]
                nm = {**m, "content": parts}
            else:
                nm = m
            # G59: also redact tool_calls[].function.arguments (+ legacy function_call) —
            # a conversation-history turn's tool-call arguments are folded into the scanned
            # prompt (G7) so PII there triggers the verdict, but were forwarded RAW.
            nm = _redact_message_tool_calls(nm, _redact_msg_text)
            # G60: a participant `name` can carry digit-PII (SSN/phone/CC fit the OpenAI
            # name charset). DROP a name that carries REAL PII/secret/credential: a redacted
            # name ("[SSN_REDACTED]"/"***-**-…") is charset-INVALID (provider 400), and
            # `name` is optional so dropping removes the channel without breaking the call.
            # Decide with the DETECTORS (not the digit-backstop redactor, which over-fires on
            # any 7+ digit run) so a benign identifier like "session-2024-001" is preserved
            # even on a request that redacts PII elsewhere (no over-redaction).
            _nm_name = nm.get("name")
            if isinstance(_nm_name, str) and _nm_name and _name_carries_pii(_nm_name):
                nm = {k: v for k, v in nm.items() if k != "name"}
            new_messages.append(nm)
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
            *,
            redaction_hints: list | None = None,
    ) -> tuple[int, dict]:
        """Non-streaming completion. Returns (http_status, response_dict).

        ``redaction_hints`` (I-04) are the policy engine's compiled redact rules
        ({regex|keywords, replacement}); see ``_apply_redaction``.
        """
        body = self._apply_redaction(body, redacted_content, redaction_hints)
        if loadtest_stub_llm_enabled():
            _warn_stub_llm_once()
            return 200, await loadtest_stub_acompletion(body)
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
        except (BadRequestError, NotFoundError) as exc:
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
                    except (BadRequestError, NotFoundError):
                        continue
                    except tuple(_EXCEPTION_STATUS_MAP.keys()):
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
            redaction_hints: list | None = None,
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

        body = self._apply_redaction(body, redacted_content, redaction_hints)
        if loadtest_stub_llm_enabled():
            _warn_stub_llm_once()
            async for frame in loadtest_stub_stream(body):
                yield frame
            return
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

        # CALLBACK-LEAK FIX: reload_models runs on every config-update notification
        # (~every 2 min), but the model list rarely changes. Each LiteLLMRouter(...)
        # reconstruction re-registers callbacks into litellm's global
        # logging_callback_manager without releasing the previous router's, so they
        # accumulate to the MAX_CALLBACKS=100 cap and flood logs with thousands of
        # "Cannot add callback - would exceed MAX_CALLBACKS limit" warnings. Skip the
        # rebuild when the incoming list is identical to the last one we applied.
        try:
            _sig = json.dumps(model_list, sort_keys=True, default=str)
        except Exception:
            _sig = None
        if (
            _sig is not None
            and _sig == getattr(self, "_last_model_list_sig", None)
            and getattr(self, "_router", None) is not None
        ):
            LOG.debug("reload_models: model list unchanged; skipping router rebuild")
            return

        valid_models, invalid_models = self._filter_valid_reload_models(model_list)
        # RC-7 (A0-2/A0-3): publish the dropped set so routing can EXCLUDE models the
        # router cannot serve. Previously the scorer ranked every credentialed model
        # while the router served only the valid subset, so an unservable winner was
        # silently remapped by ``resolve_runtime_selection`` — the remap, not the
        # governance weights, decided what ran. A model that cannot be served must
        # never win a routing decision.
        self._unroutable_model_names = {
            str(name).strip() for name, _reason in (invalid_models or []) if str(name).strip()
        }
        if invalid_models:
            sample = "; ".join(f"{name}: {reason}" for name, reason in invalid_models[:3])
            # A0-3: make the drop LOUD. This used to be the only trace that a large
            # fraction of an org's catalogue had vanished.
            LOG.error(
                "ROUTING CAPACITY LOSS: %d of %d model entries are UNROUTABLE and were "
                "dropped at reload (they are excluded from routing candidates). Sample: %s",
                len(invalid_models),
                len(model_list or []),
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
            # Cache the signature ONLY after a successful rebuild, so a failed build
            # (router left unchanged) is retried on the next identical notification.
            self._last_model_list_sig = _sig
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
        apply_sensitivity: bool = True,
        apply_latency_budget: bool = True,
        apply_risk_floor: bool = True,
    ) -> list[dict]:
        normalized_weights = self._normalize_weights(weights)
        allowed_set = {str(model).lower() for model in (allowed_models or []) if model}
        # Lowercase: a mixed-case caller value ('RESTRICTED') must not miss the
        # lowercase dict keys and collapse req level to 0 (public) — that would
        # silently route restricted data onto a public model.
        req_sens_level = _req_sensitivity_level(data_sensitivity)
        # RC-8: compliance tags are operator free-text on one side and client input on
        # the other, so compare them CANONICALLY. A model tagged 'HIPAA' must satisfy a
        # request for 'hipaa'/'Hipaa'/'PCI-DSS' vs 'pci_dss'; the old exact ``in`` test
        # made casing/separator drift look like "no compliant model" and 403'd.
        required_tags = _canonical_compliance_set(required_compliance)
        # RC-7: never rank a model the router cannot actually serve.
        unroutable = {
            str(n).strip().lower()
            for n in (getattr(self, "_unroutable_model_names", None) or set())
        }

        eligible: list[dict] = []
        for model in routing_models:
            model_name = str(model.get("model_name") or "")
            model_id = str(model.get("model_id") or model_name)
            if not model.get("is_active", True):
                continue
            if unroutable and (
                model_name.strip().lower() in unroutable or model_id.strip().lower() in unroutable
            ):
                continue
            if allowed_set and model_name.lower() not in allowed_set and model_id.lower() not in allowed_set:
                continue
            if required_tags:
                model_tags = _canonical_compliance_set(model.get("compliance_tags"))
                if not required_tags.issubset(model_tags):
                    continue
            if apply_sensitivity:
                model_sens = _SENSITIVITY_ORDER.get(
                    str(model.get("data_sensitivity_level", "public")).strip().lower(), 0
                )
                if model_sens < req_sens_level:
                    continue
            # A2: the caller's latency budget is a CONSTRAINT, not a scoring nudge.
            # Fusing it into the score is what made the latency dimension inert; a
            # model that cannot meet the stated SLA should be excluded outright, the
            # same way compliance is. Soft-fallback if this empties the pool.
            if apply_latency_budget and latency_budget_ms and latency_budget_ms > 0:
                try:
                    _sla = float(model.get("latency_sla_ms", 30000) or 30000)
                except (TypeError, ValueError):
                    _sla = 30000.0
                if _sla > float(latency_budget_ms):
                    continue
            # A3: a high-risk REQUEST must not be served by a high-risk MODEL. The old
            # `(1-model_risk)*(1-request_risk)` made request risk a constant factor that
            # could never reorder candidates, so request risk had no enforcement effect
            # at all. Express it as a floor instead. Soft-fallback if it empties the pool.
            if apply_risk_floor and request_risk_score >= _RISK_ESCALATION_FLOOR:
                try:
                    _mrisk = float(model.get("risk_score", 0.0) or 0.0)
                except (TypeError, ValueError):
                    _mrisk = 0.0
                if _mrisk > (1.0 - float(request_risk_score)):
                    continue
            eligible.append(model)

        if not eligible:
            return []

        def _cost_of(m: dict) -> float:
            try:
                return max(float(m.get("cost_per_1k_input_tokens", 0.0) or 0.0), 0.0)
            except (TypeError, ValueError):
                return 0.0

        def _sla_of(m: dict) -> float:
            try:
                return max(float(m.get("latency_sla_ms", 30000) or 30000), 0.0)
            except (TypeError, ValueError):
                return 30000.0

        def _risk_of(m: dict) -> float:
            try:
                return min(max(float(m.get("risk_score", 0.0) or 0.0), 0.0), 1.0)
            except (TypeError, ValueError):
                return 0.0

        def _prio_of(m: dict) -> float:
            try:
                return max(float(m.get("routing_priority", 0) or 0), 0.0)
            except (TypeError, ValueError):
                return 0.0

        # ── D2: MIN–MAX NORMALIZE EACH DIMENSION ACROSS THE CANDIDATE SET ──
        # The previous formulas were ABSOLUTE, which made three of four dimensions
        # inert against real-world data: `1/(1+cost*t/1000)` asymptotes to 1.0 (a
        # 1000x price gap moved the score by 0.005), the latency branch returned a
        # flat 1.0 for every model whose SLA fell under the budget (spread exactly
        # 0.000000), and `(1-request_risk)` was a constant factor that scaled every
        # candidate identically and so could never reorder them. Net effect: only
        # routing_priority decided anything and all five UI Strategy Presets picked
        # the same model — the most expensive and slowest in the catalogue.
        #
        # Normalizing per candidate set makes each dimension span the full [0,1]
        # range, so an operator's weight is what actually decides the winner.
        def _span(values: list[float]) -> tuple[float, float]:
            return (min(values), max(values)) if values else (0.0, 0.0)

        costs = [_cost_of(m) for m in eligible]
        slas = [_sla_of(m) for m in eligible]
        risks = [_risk_of(m) for m in eligible]
        prios = [_prio_of(m) for m in eligible]
        c_lo, c_hi = _span(costs)
        l_lo, l_hi = _span(slas)
        r_lo, r_hi = _span(risks)
        p_lo, p_hi = _span(prios)

        def _norm(value: float, lo: float, hi: float, *, invert: bool) -> float:
            # Degenerate span (every candidate identical on this dimension) carries no
            # signal. Return 1.0 rather than 0.0/0.5 so a uniform dimension neither
            # penalises anyone nor shrinks the composite relative to a diverse catalogue.
            if (hi - lo) <= _SPAN_EPSILON:
                return 1.0
            t = (value - lo) / (hi - lo)
            return (1.0 - t) if invert else t

        scored: list[dict] = []
        for model in eligible:
            model_name = str(model.get("model_name") or "")
            model_id = str(model.get("model_id") or model_name)

            # Cheapest / fastest / safest / highest-priority each score 1.0.
            cost_component = _norm(_cost_of(model), c_lo, c_hi, invert=True)
            latency_component = _norm(_sla_of(model), l_lo, l_hi, invert=True)
            risk_component = _norm(_risk_of(model), r_lo, r_hi, invert=True)
            priority_component = _norm(_prio_of(model), p_lo, p_hi, invert=False)

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
                "score": round(composite, 6),
                "risk_component": round(risk_component, 4),
                "cost_component": round(cost_component, 4),
                "latency_component": round(latency_component, 4),
                "priority_component": round(priority_component, 4),
            })

        # ── A4: DETERMINISTIC TOTAL ORDERING ──
        # A bare score sort is STABLE, so equal scores previously resolved to input
        # order — i.e. Redis sync order. With an all-default catalogue every candidate
        # ties exactly and the winner was whatever the control plane happened to
        # serialize first, which could change across a resync. This key is total: no
        # two distinct candidates can be order-ambiguous (model_name is unique per org).
        scored.sort(key=lambda item: (
            -item["score"],
            -_prio_of(item["model"]),
            _cost_of(item["model"]),
            _sla_of(item["model"]),
            item["model_name"],
        ))
        return scored

    def _score_with_sensitivity_fallback(
        self,
        routing_models: list[dict],
        request_risk_score: float = 0.0,
        required_compliance: list[str] | None = None,
        data_sensitivity: str = "public",
        estimated_tokens: int = 500,
        latency_budget_ms: int = 30000,
        weights: dict[str, float] | None = None,
        allowed_models: list[str] | None = None,
    ) -> tuple[list[dict], bool]:
        """Score candidates, degrading only the SOFT constraints when they empty the pool.

        Constraint classes, hardest first:

        * **compliance tags** — HARD. Unsatisfiable → ``[]`` (caller 403s).
        * **data sensitivity** — HARD as of D3. Unsatisfiable → ``[]`` (caller 403s).
          Previously this soft-fell-back to "best available", which contradicted the
          Routing Governance copy ("Models below this level are excluded from routing")
          and silently served regulated data on an under-approved model.
        * **latency budget** — SOFT. Unsatisfiable → re-score ignoring the budget and
          report ``latency_budget_unsatisfiable``; a tight budget should degrade, not 503.
        * **request risk floor** — SOFT. Unsatisfiable → re-score ignoring the floor and
          report ``risk_floor_unsatisfiable``.

        Returns ``(scored, degradations)`` where *degradations* is the list of soft
        constraints that had to be relaxed, so callers can report it honestly.
        """
        def _score(**over) -> list[dict]:
            kw = dict(
                routing_models=routing_models,
                request_risk_score=request_risk_score,
                required_compliance=required_compliance,
                data_sensitivity=data_sensitivity,
                estimated_tokens=estimated_tokens,
                latency_budget_ms=latency_budget_ms,
                weights=weights,
                allowed_models=allowed_models,
                apply_sensitivity=True,
                apply_latency_budget=True,
                apply_risk_floor=True,
            )
            kw.update(over)
            return self._score_routing_models(**kw)

        scored = _score()
        if scored:
            return scored, []

        # Relax the soft constraints in order of least-surprising first. Sensitivity and
        # compliance are NEVER relaxed — an empty pool there is a genuine fail-closed.
        for relaxations, labels in (
            ({"apply_latency_budget": False}, ["latency_budget_unsatisfiable"]),
            ({"apply_risk_floor": False}, ["risk_floor_unsatisfiable"]),
            (
                {"apply_latency_budget": False, "apply_risk_floor": False},
                ["latency_budget_unsatisfiable", "risk_floor_unsatisfiable"],
            ),
        ):
            scored = _score(**relaxations)
            if scored:
                return scored, labels

        return [], []

    @staticmethod
    def _candidate_score_preview(scored: list[dict], limit: int = 5) -> list[dict]:
        out: list[dict] = []
        for item in scored[:limit]:
            out.append({
                "model_name": item.get("model_name"),
                "score": item.get("score"),
                "risk_component": item.get("risk_component"),
                "cost_component": item.get("cost_component"),
                "latency_component": item.get("latency_component"),
                "priority_component": item.get("priority_component"),
            })
        return out

    @staticmethod
    def _scores_tied(scored: list[dict]) -> bool:
        if len(scored) < 2:
            return False
        top = float(scored[0].get("score") or 0.0)
        second = float(scored[1].get("score") or 0.0)
        return abs(top - second) <= _SCORE_TIE_EPSILON

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
        preferred_model: str = "",
        token_budget_tpm: int | None = None,
    ) -> ModelSelection | None:
        """
        Deterministic multi-dimensional model selection. NO LLM is involved.

        Hard filters (unsatisfiable -> ``None``, caller 403s): compliance tags,
        data sensitivity, is_active, allowlist, router servability.
        Soft constraints (relaxed with a reported degradation rather than failing):
        latency budget, request-risk floor.
        Soft scoring: risk, cost, latency, priority — each min-max normalized across
        the candidate set so the operator's weights actually decide the winner.

        Ordering is a TOTAL order (score, priority, cost, sla, name), so the same
        inputs always produce the same winner regardless of candidate list order.
        """
        normalized_weights = self._normalize_weights(weights)
        scored, degradations = self._score_with_sensitivity_fallback(
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
        score_tie = self._scores_tied(scored)
        factors = [
            f"risk_weight={normalized_weights['risk']:.2f}",
            f"cost_weight={normalized_weights['cost']:.2f}",
            f"latency_weight={normalized_weights['latency']:.2f}",
            f"priority_weight={normalized_weights['priority']:.2f}",
            f"request_risk={request_risk_score:.2f}",
            f"data_sensitivity={data_sensitivity}",
        ]
        if required_compliance:
            factors.append(
                "compliance_required=" + ",".join(sorted(_canonical_compliance_set(required_compliance)))
            )

        # Name the dimension that actually decided it, so the operator can see WHY.
        _dominant = max(normalized_weights, key=lambda k: normalized_weights[k])
        reason = (
            f"Deterministic weighted selection (risk={normalized_weights['risk']:.0%}, "
            f"cost={normalized_weights['cost']:.0%}, "
            f"latency={normalized_weights['latency']:.0%}, "
            f"priority={normalized_weights['priority']:.0%}); "
            f"{best_model.get('model_name', '')} ranked best among {len(scored)} eligible models."
        )
        policy = f"Deterministic weighted selection (dominant dimension: {_dominant})"

        for _deg in degradations or []:
            factors.append(_deg)
        if "latency_budget_unsatisfiable" in (degradations or []):
            policy = f"{policy} — latency budget relaxed"
            reason = (
                f"No model meets latency_budget_ms={latency_budget_ms}; "
                f"budget relaxed and best available selected. {reason}"
            )
        if "risk_floor_unsatisfiable" in (degradations or []):
            policy = f"{policy} — risk floor relaxed"

        if score_tie:
            factors.append("score_tie=true")
            factors.append("tie_break=priority,cost,latency,name")
            policy = f"{policy} (score tie broken deterministically)"

        return ModelSelection(
            model_name=best_model.get("model_name", ""),
            model_id=best_model.get("model_id", ""),
            score=best["score"],
            reason=reason,
            fallback_chain=fallbacks,
            # B-H2: the adjudicator used to stamp this on every return path. Without it
            # `_build_routing_metadata` reports original_model="auto" and rerouted=false
            # for a client-PINNED model, silently losing the Requested != Served contract.
            requested_model=(preferred_model or ""),
            decision_source="deterministic_weighted",
            evaluator_model="",
            policy_summary=policy,
            decision_factors=factors,
            candidate_count=len(scored),
            sensitivity_fallback=False,
            score_tie=score_tie,
            candidate_scores=self._candidate_score_preview(scored),
        )

