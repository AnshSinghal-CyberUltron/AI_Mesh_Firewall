"""UEBA v2 LLM SOC analyst overlay via Bedrock."""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

from module2.ueba_behavior_profile import fetch_first_n_snippets
from module2.ueba_scoring import apply_llm_adjustment, apply_llm_blend, clamp

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
Use the behavior_profile (expected use case from first prompts) to judge deviation.
Be conservative: document scanners with steady high block rates are often benign.
Escalate when current behavior deviates from the established profile or threat patterns suggest compromise."""

UEBA_BOOTSTRAP_SYSTEM_PROMPT = """You are a SOC analyst establishing a behavioral baseline for an API key from its first redacted prompts.
Given prompt samples and aggregate stats (no secrets), output ONLY valid JSON:
{
  "expected_use_case": "1-2 sentence description of intended usage",
  "behavior_class": "prod_app|scanner|dev_test|unknown",
  "risk_prediction": "1-2 sentences on expected risk posture going forward",
  "confidence": 0.0-1.0,
  "score_adjustment": number between -0.2 and 0.2
}
Be conservative. Security scanners probing injection payloads are often benign with high block rates."""


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


def parse_llm_bootstrap_response(raw: str) -> dict[str, Any] | None:
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
    behavior_class = str(parsed.get("behavior_class", "unknown")).lower()
    if behavior_class not in ("prod_app", "scanner", "dev_test", "unknown"):
        behavior_class = "unknown"
    confidence = clamp(float(parsed.get("confidence", 0.5)))
    adjustment = clamp(float(parsed.get("score_adjustment", 0.0)), -0.2, 0.2)
    return {
        "expected_use_case": str(parsed.get("expected_use_case", "")).strip()[:4000],
        "behavior_class": behavior_class,
        "risk_prediction": str(parsed.get("risk_prediction", "")).strip()[:4000],
        "confidence": confidence,
        "score_adjustment": adjustment,
    }


def _behavior_profile_context(profile) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "expected_use_case": profile.expected_use_case or "",
        "behavior_class": profile.behavior_class or "unknown",
        "risk_prediction": profile.risk_prediction or "",
        "sample_count": int(profile.sample_count or 0),
        "baseline_metrics": dict(profile.baseline_metrics or {}),
        "profile_built_at": (
            profile.profile_built_at.isoformat() if profile.profile_built_at else None
        ),
    }


def build_triage_context(
    key,
    metric: dict,
    traditional_score: float,
    breakdown: dict,
    org_settings,
    active_kill_switches: list | None = None,
    behavior_profile=None,
    **_legacy_kwargs,
) -> dict[str, Any]:
    total = metric.get("total", 0)
    block_rate = (metric.get("blocked", 0) / total) if total else 0.0
    redact_rate = (metric.get("redacted", 0) / total) if total else 0.0
    top_threats = sorted(
        (metric.get("threat_types") or {}).items(),
        key=lambda x: -x[1],
    )[:5]
    return {
        "key": {
            "prefix": key.prefix,
            "name": key.name,
            "project_id": key.project_id,
            "mode": "traditional",
            "lifetime_requests": key.ueba_lifetime_request_count,
        },
        "current": {
            "request_count": total,
            "block_rate": round(block_rate, 4),
            "redact_rate": round(redact_rate, 4),
            "models": list(metric.get("models") or []),
            "top_threats": top_threats,
        },
        "behavior_profile": _behavior_profile_context(behavior_profile),
        "traditional_score": traditional_score,
        "score_breakdown": breakdown,
        "anomaly_flags": breakdown.get("anomaly_flags", []),
        "active_kill_switches": active_kill_switches or [],
    }


def build_bootstrap_context(key, metric: dict, profile, org_settings=None) -> dict[str, Any]:
    total = metric.get("total", 0)
    block_rate = (metric.get("blocked", 0) / total) if total else 0.0
    redact_rate = (metric.get("redacted", 0) / total) if total else 0.0
    samples = fetch_first_n_snippets(profile, org_settings=org_settings)
    return {
        "key": {
            "prefix": key.prefix,
            "name": key.name,
            "project_id": key.project_id,
            "lifetime_requests": key.ueba_lifetime_request_count,
        },
        "aggregate": {
            "sample_count": len(samples),
            "block_rate": round(block_rate, 4),
            "redact_rate": round(redact_rate, 4),
            "models": list(metric.get("models") or []),
            "top_threats": sorted(
                (metric.get("threat_types") or {}).items(),
                key=lambda x: -x[1],
            )[:5],
        },
        "prompt_samples": samples,
    }


def should_run_llm_triage(
    traditional_score: float,
    breakdown: dict,
    org_settings,
    *,
    profile_ready: bool = True,
) -> bool:
    if not profile_ready:
        return False
    if org_settings is None or not org_settings.llm_triage_enabled:
        return False
    if traditional_score >= org_settings.llm_triage_min_traditional_score:
        return True
    return bool(breakdown.get("anomaly_flags"))


def _llm_mock_enabled() -> bool:
    return os.getenv("MODULE2_UEBA_LLM_MOCK", "").lower() in ("1", "true", "yes", "on")


def _mock_triage(context: dict) -> dict[str, Any]:
    score = float(context.get("traditional_score", 0))
    adj = 0.05 if score >= 0.5 else -0.02
    return {
        "verdict": "suspicious" if score >= 0.5 else "benign",
        "confidence": 0.7,
        "reasoning": "Mock LLM triage for test/CI.",
        "recommended_action": "monitor",
        "score_adjustment": adj,
        "llm_score": apply_llm_adjustment(score, adj),
        "degraded": False,
    }


def _mock_bootstrap(_context: dict) -> dict[str, Any]:
    return {
        "expected_use_case": "Mock established use case for test/CI.",
        "behavior_class": "scanner",
        "risk_prediction": "Elevated block rate expected for scanner workloads.",
        "confidence": 0.75,
        "score_adjustment": -0.05,
        "degraded": False,
    }


def _call_bedrock(system_prompt: str, context: dict, client=None) -> dict[str, Any]:
    if _llm_mock_enabled():
        return {"mock": True}
    if client is None:
        from security_engines.bedrock_client import default_bedrock_client

        client = default_bedrock_client()
    user_content = json.dumps(context, default=str)
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": int(os.getenv("BEDROCK_MAX_TOKENS", "512")),
        "temperature": 0.2,
    }
    model = os.getenv("BEDROCK_MODEL", "openai.gpt-oss-120b-1:0")
    resp = client.scan_prompt(model=model, prompt_payload=payload)
    from security_engines.bedrock_scanner import BedrockScanner

    content = BedrockScanner._get_content_str(resp)
    return {"content": content}


def _call_bedrock_triage(context: dict, client=None) -> dict[str, Any]:
    if _llm_mock_enabled():
        return _mock_triage(context)
    result = _call_bedrock(UEBA_ANALYST_SYSTEM_PROMPT, context, client)
    if result.get("mock"):
        return _mock_triage(context)
    parsed = parse_llm_triage_response(result.get("content", ""))
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


def _call_bedrock_bootstrap(context: dict, client=None) -> dict[str, Any]:
    if _llm_mock_enabled():
        return _mock_bootstrap(context)
    result = _call_bedrock(UEBA_BOOTSTRAP_SYSTEM_PROMPT, context, client)
    if result.get("mock"):
        return _mock_bootstrap(context)
    parsed = parse_llm_bootstrap_response(result.get("content", ""))
    if not parsed:
        LOG.warning("UEBA LLM bootstrap: unparseable response")
        return {"unparseable": True}
    return {**parsed, "degraded": False}


def run_llm_triage(context: dict, client=None, timeout_sec: float | None = None) -> dict[str, Any]:
    """Call Bedrock for SOC triage. Returns degraded skip payload on failure or timeout.

    Timeout defaults to MODULE2_UEBA_LLM_TIMEOUT_SEC (30). A hung model must not
    block Celery forever — degraded → assess_api_key keeps traditional score.
    Rate admission is MODULE2_UEBA_LLM_MAX_PER_MIN (callers use _llm_rate_limit_ok).
    """
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
        from django.conf import settings as django_settings

        timeout_sec = float(getattr(django_settings, "MODULE2_UEBA_LLM_TIMEOUT_SEC", 30))
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


def run_llm_behavior_bootstrap(context: dict, client=None, timeout_sec: float | None = None) -> dict[str, Any]:
    """Call Bedrock to bootstrap a behavior profile.

    Same timeout / degrade contract as triage (MODULE2_UEBA_LLM_TIMEOUT_SEC).
    Rate admission: MODULE2_UEBA_LLM_MAX_PER_MIN via caller _llm_rate_limit_ok.
    Not invoked per gateway request — only during UEBA assess when profile needs bootstrap.
    """
    degraded = {
        "expected_use_case": "",
        "behavior_class": "unknown",
        "risk_prediction": "",
        "confidence": None,
        "score_adjustment": 0.0,
        "degraded": True,
    }
    if timeout_sec is None:
        from django.conf import settings as django_settings

        timeout_sec = float(getattr(django_settings, "MODULE2_UEBA_LLM_TIMEOUT_SEC", 30))
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_call_bedrock_bootstrap, context, client)
            result = future.result(timeout=timeout_sec)
        if result.get("unparseable"):
            return degraded
        return result
    except FuturesTimeoutError:
        LOG.warning("UEBA LLM bootstrap timed out after %.1fs", timeout_sec)
        return degraded
    except Exception:
        LOG.exception("UEBA LLM bootstrap failed")
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
    verdict = llm_result.get("verdict", "skipped")
    return traditional, llm_score, final, verdict, weighted_delta
