"""P0.0 Counted_Sample classifier. Product gateway is not imported."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

NINE_STAGES: tuple[str, ...] = (
    "auth",
    "kill_switch",
    "rate_limit",
    "policy",
    "input_scan",
    "model_routing",
    "model_input",
    "model_output",
    "output_guardrail",
)
TOKEN_FLOOR = 32
STUB_ID = "chatcmpl-loadtest-stub"
CELL_A_PROMPT_PREFIX = "Reply OK."
MAX_PROMPT_BYTES = 256


@dataclass(frozen=True)
class SampleClass:
    class_name: str
    counted: bool


def _as_dict(obj: Any) -> dict:
    return obj if isinstance(obj, dict) else {}


def _trace(body: dict) -> dict:
    tr = body.get("pipeline_trace")
    if isinstance(tr, dict):
        return tr
    zs = body.get("zeroshield")
    if isinstance(zs, dict) and isinstance(zs.get("pipeline_trace"), dict):
        return zs["pipeline_trace"]
    return {}


def _stage_map(trace: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for s in trace.get("stages") or []:
        if not isinstance(s, dict):
            continue
        name = s.get("name") or s.get("stage")
        if isinstance(name, str) and name:
            out[name] = s
    return out


def _completion_tokens(body: dict) -> int | None:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    raw = usage.get("completion_tokens")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def firewall_tax_ms(trace: dict | None, *, stream: bool = False) -> float | None:
    if stream or not isinstance(trace, dict):
        return None
    if trace.get("ttft_ms") is not None:
        return None
    try:
        total = float(trace["total_latency_ms"])
    except (KeyError, TypeError, ValueError):
        return None
    stages = _stage_map(trace)
    model_out = 0.0
    mo = stages.get("model_output") or {}
    if mo.get("latency_ms") is not None:
        try:
            model_out = float(mo["latency_ms"]) or 0.0
        except (TypeError, ValueError):
            model_out = 0.0
    return round(max(0.0, total - model_out), 3)


def classify_sample(status: int, body: Any, *, stream: bool = False) -> SampleClass:
    body = _as_dict(body)
    if stream:
        return SampleClass("stream", False)
    if status == 0:
        return SampleClass("status_0", False)
    if status == 401:
        return SampleClass("http_401", False)
    if status == 403:
        return SampleClass("http_403", False)
    if status == 429:
        return SampleClass("http_429", False)
    if status == 503:
        return SampleClass("http_503", False)
    if status == 400:
        return SampleClass("http_400", False)
    if status != 200:
        return SampleClass("http_not_200", False)

    zs = _as_dict(body.get("zeroshield"))
    if zs.get("scan_only") is True:
        return SampleClass("scan_only", False)

    cid = body.get("id")
    if cid != STUB_ID:
        return SampleClass("scan_only" if isinstance(cid, str) and "scan" in cid else "not_stub", False)

    trace = _trace(body)
    if trace.get("ttft_ms") is not None:
        return SampleClass("stream", False)

    action = str(trace.get("final_action") or "").strip().lower()
    if action == "redact":
        return SampleClass("redact", False)
    if action in ("flag", "rewrite"):
        return SampleClass("flag_or_rewrite", False)
    if action != "allow":
        return SampleClass("not_allow", False)

    tokens = _completion_tokens(body)
    if tokens is None or tokens < TOKEN_FLOOR:
        return SampleClass("short_tokens", False)

    stages = _stage_map(trace)
    for name in NINE_STAGES:
        st = stages.get(name)
        if not isinstance(st, dict):
            return SampleClass("skip_model", False)
        if str(st.get("action") or "").strip().lower() == "skip":
            return SampleClass("skip_model", False)

    return SampleClass("counted", True)


def build_chat_body(nonce: str, *, prompt_prefix: str = CELL_A_PROMPT_PREFIX) -> dict:
    content = f"{prompt_prefix} {nonce}"
    if len(content.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError("Cell_A prompt exceeds byte cap")
    return {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": TOKEN_FLOOR,
        "stream": False,
        "enable_routing": False,
    }


def prompt_fingerprint(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
