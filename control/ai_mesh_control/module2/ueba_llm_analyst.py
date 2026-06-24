"""UEBA v2 LLM SOC analyst overlay via Bedrock."""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

from module2.ueba_scoring import apply_llm_adjustment, apply_llm_blend, clamp, resolve_ueba_mode

LOG = logging.getLogger(__name__)

UEBA_ANALYST_SYSTEM_PROMPT = """You are a SOC analyst reviewing API key behavioral risk for an AI security gateway.
Given structured telemetry (no secrets), output ONLY valid JSON with this schema:
{
  "verdict": "benign|suspicious|malicious",
  "confidence": 0.0-1.0,
  "reasoning": "2-4 sentences for a human analyst",
  "recommended_action": "monitor|investigate|contain",
  "score_adjustment": number between -0.3 and 0.3
}
Be conservative: document scanners with steady high block rates are often benign.
Escalate when deviation from baseline or threat patterns suggest compromise."""


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_llm_triage_response(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    verdict = str(parsed.get("verdict", "suspicious")).lower()
    if verdict not in ("benign", "suspicious", "malicious"):
        verdict = "suspicious"
    action = str(parsed.get("recommended_action", "monitor")).lower()
    if action not in ("monitor", "investigate", "contain"):
        action = "monitor"
    confidence = clamp(float(parsed.get("confidence", 0.5)))
    adjustment = clamp(float(parsed.get("score_adjustment", 0.0)), -0.3, 0.3)
    reasoning = str(parsed.get("reasoning", "")).strip()[:2000]
    return {
        "verdict": verdict,
        "confidence": confidence,
        "reasoning": reasoning,
        "recommended_action": action,
        "score_adjustment": adjustment,
    }


def build_triage_context(
    key,
    metric: dict,
    baseline: Any | None,
    traditional_score: float,
    breakdown: dict,
    org_settings,
    active_kill_switches: list | None = None,
) -> dict[str, Any]:
    total = metric.get("total", 0)
    block_rate = (metric.get("blocked", 0) / total) if total else 0.0
    redact_rate = (metric.get("redacted", 0) / total) if total else 0.0
    top_threats = sorted(
        (metric.get("threat_types") or {}).items(),
        key=lambda x: -x[1],
    )[:5]
    baseline_payload = None
    if baseline is not None:
        baseline_payload = {
            "avg_block_rate": baseline.avg_block_rate,
            "avg_redact_rate": baseline.avg_redact_rate,
            "avg_requests_per_hour": baseline.avg_requests_per_hour,
            "typical_models": baseline.typical_models,
            "typical_threat_types": baseline.typical_threat_types,
        }
    return {
        "key": {
            "prefix": key.prefix,
            "name": key.name,
            "project_id": key.project_id,
            "purpose": key.key_purpose,
            "mode": resolve_ueba_mode(key, org_settings) if org_settings else key.ueba_mode,
            "lifetime_requests": key.ueba_lifetime_request_count,
        },
        "current": {
            "request_count": total,
            "block_rate": round(block_rate, 4),
            "redact_rate": round(redact_rate, 4),
            "models": list(metric.get("models") or []),
            "top_threats": top_threats,
        },
        "baseline": baseline_payload,
        "traditional_score": traditional_score,
        "score_breakdown": breakdown,
        "anomaly_flags": breakdown.get("anomaly_flags", []),
        "active_kill_switches": active_kill_switches or [],
    }


def should_run_llm_triage(
    traditional_score: float,
    breakdown: dict,
    org_settings,
) -> bool:
    if org_settings is None or not org_settings.llm_triage_enabled:
        return False
    if traditional_score >= org_settings.llm_triage_min_traditional_score:
        return True
    return bool(breakdown.get("anomaly_flags"))


def _call_bedrock_triage(context: dict, client=None) -> dict[str, Any]:
    if client is None:
        from security_engines.bedrock_client import default_bedrock_client

        client = default_bedrock_client()
    user_content = json.dumps(context, default=str)
    payload = {
        "messages": [
            {"role": "system", "content": UEBA_ANALYST_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": int(os.getenv("BEDROCK_MAX_TOKENS", "512")),
        "temperature": 0.2,
    }
    model = os.getenv("BEDROCK_MODEL", "openai.gpt-oss-120b-1:0")
    resp = client.scan_prompt(model=model, prompt_payload=payload)
    from security_engines.bedrock_scanner import BedrockScanner

    content = BedrockScanner._get_content_str(resp)
    parsed = parse_llm_triage_response(content)
    if not parsed:
        LOG.warning("UEBA LLM triage: unparseable response")
        return {"unparseable": True}
    traditional = float(context.get("traditional_score", 0))
    llm_score = apply_llm_adjustment(traditional, parsed["score_adjustment"])
    return {
        **parsed,
        "llm_score": llm_score,
        "degraded": False,
    }


def run_llm_triage(context: dict, client=None, timeout_sec: float | None = None) -> dict[str, Any]:
    """Call Bedrock for SOC triage. Returns degraded skip payload on failure or timeout."""
    degraded = {
        "verdict": "skipped",
        "confidence": None,
        "reasoning": "",
        "recommended_action": "",
        "score_adjustment": 0.0,
        "llm_score": None,
        "degraded": True,
    }
    if timeout_sec is None:
        from django.conf import settings

        timeout_sec = float(getattr(settings, "MODULE2_UEBA_LLM_TIMEOUT_SEC", 30))
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_call_bedrock_triage, context, client)
            result = future.result(timeout=timeout_sec)
        if result.get("unparseable"):
            return degraded
        return result
    except FuturesTimeoutError:
        LOG.warning("UEBA LLM triage timed out after %.1fs", timeout_sec)
        return degraded
    except Exception:
        LOG.exception("UEBA LLM triage failed; using traditional score only")
        return degraded


def finalize_assessment_scores(
    traditional_score: float,
    llm_result: dict[str, Any] | None,
) -> tuple[float, float | None, float, str, float | None]:
    if not llm_result or llm_result.get("verdict") == "skipped":
        return traditional_score, None, traditional_score, "skipped", None
    adjustment = llm_result.get("score_adjustment")
    if adjustment is None and llm_result.get("llm_score") is not None:
        adjustment = float(llm_result["llm_score"]) - traditional_score
    if adjustment is None:
        return traditional_score, None, traditional_score, "skipped", None
    traditional, llm_score, final, weighted_delta = apply_llm_blend(traditional_score, float(adjustment))
    return traditional, llm_score, final, llm_result.get("verdict", "skipped"), weighted_delta
