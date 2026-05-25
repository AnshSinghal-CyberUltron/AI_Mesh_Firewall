"""Lightweight metrics emitter (logging-first).

This module provides simple functions to record Bedrock call metrics.
All Bedrock metrics are also forwarded to the dedicated bedrock_logger
for clean, separate log files in production.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

LOG = logging.getLogger("gateway.metrics")

try:
    from bedrock_logger import log_metrics as _blog_metrics, log_rule_hit as _blog_rule_hit
except Exception:
    _blog_metrics = None  # type: ignore[assignment]
    _blog_rule_hit = None  # type: ignore[assignment]


def record_bedrock_call(method: str, model: str, tokens_in: int, tokens_out: int, elapsed_s: float, success: bool = True) -> None:
    payload: Dict[str, Any] = {
        "method": method,
        "model": model,
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "elapsed_s": float(elapsed_s),
        "success": bool(success),
    }
    LOG.info("bedrock_call_metrics %s", payload)
    if _blog_metrics:
        _blog_metrics(
            method=method, model=model,
            tokens_in=int(tokens_in), tokens_out=int(tokens_out),
            elapsed_s=float(elapsed_s), success=bool(success),
        )


def record_rule_hit(rule_id: str, severity: str, source: str = "bedrock") -> None:
    LOG.info("rule_hit %s", {"rule_id": rule_id, "severity": severity, "source": source})
    if _blog_rule_hit:
        _blog_rule_hit(rule_id=rule_id, severity=severity, source=source)
