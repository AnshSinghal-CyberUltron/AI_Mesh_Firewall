"""
Dedicated Bedrock logging — clean, structured, separate log files.

Provides a production-grade rotating file logger for all Bedrock operations
(invoke_model, scan, parse, health checks) so they don't get buried in the
main gateway log stream.

Usage:
    from bedrock_logger import bedrock_log, log_bedrock_request, log_bedrock_response

Environment variables:
    BEDROCK_LOG_DIR      — directory for log files  (default: /var/log/bedrock)
    BEDROCK_LOG_LEVEL    — minimum level            (default: DEBUG)
    BEDROCK_LOG_MAX_MB   — max size per file in MB  (default: 50)
    BEDROCK_LOG_BACKUPS  — rotated backup count     (default: 10)
    BEDROCK_LOG_JSON     — emit JSON lines          (default: true)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
import uuid
from collections import deque
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional

# ── Configuration ────────────────────────────────────────────────────────────

LOG_DIR = os.getenv("BEDROCK_LOG_DIR", "/var/log/bedrock")
LOG_LEVEL = os.getenv("BEDROCK_LOG_LEVEL", "DEBUG").upper()
LOG_MAX_BYTES = int(os.getenv("BEDROCK_LOG_MAX_MB", "50")) * 1024 * 1024
LOG_BACKUPS = int(os.getenv("BEDROCK_LOG_BACKUPS", "10"))
LOG_JSON = os.getenv("BEDROCK_LOG_JSON", "true").lower() in ("true", "1", "yes")
LOG_PREVIEW_CHARS = int(os.getenv("BEDROCK_LOG_PREVIEW_CHARS", "1200"))
LOG_PROMPT_PREVIEW = os.getenv("BEDROCK_LOG_PROMPT_PREVIEW", "true").lower() in ("true", "1", "yes")
LOG_OUTPUT_PREVIEW = os.getenv("BEDROCK_LOG_OUTPUT_PREVIEW", "true").lower() in ("true", "1", "yes")

# In-memory ring buffer for the /v1/admin/bedrock-logs API
_RING_BUFFER_SIZE = int(os.getenv("BEDROCK_LOG_RING_SIZE", "500"))


# ── JSON Formatter ───────────────────────────────────────────────────────────

class _JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = traceback.format_exception(*record.exc_info)
        if hasattr(record, "bedrock_data"):
            entry["data"] = record.bedrock_data
        return json.dumps(entry, default=str)


class _ReadableFormatter(logging.Formatter):
    """Human-readable formatter for non-JSON mode."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


# ── In-memory ring buffer ───────────────────────────────────────────────────

class BedrockLogEntry:
    """Lightweight structured log entry kept in memory."""
    __slots__ = ("timestamp", "level", "message", "data")

    def __init__(self, timestamp: str, level: str, message: str, data: Optional[Dict] = None):
        self.timestamp = timestamp
        self.level = level
        self.message = message
        self.data = data

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
        }
        if self.data:
            d["data"] = self.data
        return d


class BedrockLogRing:
    """Thread-safe circular buffer of recent Bedrock log entries."""

    def __init__(self, max_size: int = _RING_BUFFER_SIZE) -> None:
        self._buf: deque[BedrockLogEntry] = deque(maxlen=max_size)
        self._counter: int = 0

    def push(self, entry: BedrockLogEntry) -> None:
        self._buf.append(entry)
        self._counter += 1

    @property
    def counter(self) -> int:
        return self._counter

    def recent(self, limit: int = 100, level_filter: Optional[str] = None) -> List[Dict]:
        items = list(self._buf)
        if level_filter:
            priority = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
            min_p = priority.get(level_filter.upper(), 0)
            items = [e for e in items if priority.get(e.level, 0) >= min_p]
        return [e.to_dict() for e in items[-limit:]]


BEDROCK_LOG_RING = BedrockLogRing()


class _RingHandler(logging.Handler):
    """Push records into the in-memory ring buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            ts += f".{int(record.msecs):03d}Z"
            data = getattr(record, "bedrock_data", None)
            entry = BedrockLogEntry(
                timestamp=ts,
                level=record.levelname,
                message=record.getMessage(),
                data=data,
            )
            BEDROCK_LOG_RING.push(entry)
        except Exception:
            self.handleError(record)


# ── Logger setup ─────────────────────────────────────────────────────────────

def _setup_bedrock_logger() -> logging.Logger:
    """Create and configure the dedicated Bedrock logger."""
    logger = logging.getLogger("bedrock")
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.DEBUG))
    logger.propagate = False  # don't duplicate into root gateway logs

    if logger.handlers:
        return logger  # already configured

    formatter = _JSONFormatter() if LOG_JSON else _ReadableFormatter()

    # 1) Rotating file handler
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fh = RotatingFileHandler(
            os.path.join(LOG_DIR, "bedrock.log"),
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUPS,
            encoding="utf-8",
        )
        fh.setLevel(getattr(logging, LOG_LEVEL, logging.DEBUG))
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except OSError as exc:
        # Fall back to stderr-only if log dir is not writable
        sys.stderr.write(f"[bedrock_logger] Cannot write to {LOG_DIR}: {exc}\n")

    # 2) Stderr handler (always present for docker logs)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(getattr(logging, LOG_LEVEL, logging.DEBUG))
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    # 3) In-memory ring buffer handler
    rh = _RingHandler()
    rh.setLevel(getattr(logging, LOG_LEVEL, logging.DEBUG))
    logger.addHandler(rh)

    return logger


bedrock_log = _setup_bedrock_logger()


# ── Convenience helpers ──────────────────────────────────────────────────────

def _log_with_data(level: int, msg: str, data: Optional[Dict] = None, **kwargs: Any) -> None:
    """Log a message with optional structured data attached."""
    record = bedrock_log.makeRecord(
        bedrock_log.name, level, "(bedrock)", 0, msg, (), None,
    )
    if data:
        record.bedrock_data = data  # type: ignore[attr-defined]
    bedrock_log.handle(record)


def _normalize_text(value: str) -> str:
    return " ".join((value or "").replace("\r", " ").replace("\n", " ").split())


def _preview_text(value: str, max_chars: int = LOG_PREVIEW_CHARS) -> str:
    normalized = _normalize_text(value)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars] + "..."


def new_request_id() -> str:
    """Generate a short unique ID for correlating request/response pairs."""
    return uuid.uuid4().hex[:12]


def log_bedrock_request(
    *,
    request_id: str,
    model: str,
    region: str,
    payload_bytes: int,
    prompt_len: int,
    truncated_len: int,
    has_context: bool = False,
    max_tokens: int = 512,
    deployment_path: Optional[str] = None,
    prompt_preview: Optional[str] = None,
) -> None:
    """Log an outgoing Bedrock invoke_model request."""
    data = {
        "event": "bedrock_request",
        "request_id": request_id,
        "model": model,
        "region": region,
        "payload_bytes": payload_bytes,
        "prompt_len": prompt_len,
        "truncated_len": truncated_len,
        "has_context": has_context,
        "max_tokens": max_tokens,
    }
    if deployment_path:
        data["deployment_path"] = deployment_path
    if prompt_preview is not None and LOG_PROMPT_PREVIEW:
        data["prompt_preview"] = _preview_text(prompt_preview)
    _log_with_data(
        logging.INFO,
        f"BEDROCK REQUEST  | reqid={request_id} model={model} region={region} "
        f"payload={payload_bytes}B prompt={prompt_len}→{truncated_len} "
        f"max_tokens={max_tokens}",
        data,
    )
    if prompt_preview is not None and LOG_PROMPT_PREVIEW:
        _log_with_data(
            logging.INFO,
            f"BEDROCK PROMPT   | reqid={request_id} text={_preview_text(prompt_preview)}",
            {
                "event": "bedrock_prompt",
                "request_id": request_id,
                "model": model,
                "preview": _preview_text(prompt_preview),
            },
        )


def log_bedrock_response(
    *,
    request_id: str,
    model: str,
    region: str,
    elapsed_s: float,
    tokens_in: int,
    tokens_out: int,
    success: bool = True,
    response_keys: Optional[List[str]] = None,
    http_status: Optional[int] = None,
    output_preview: Optional[str] = None,
) -> None:
    """Log a Bedrock invoke_model response."""
    data = {
        "event": "bedrock_response",
        "request_id": request_id,
        "model": model,
        "region": region,
        "elapsed_s": round(elapsed_s, 3),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "success": success,
    }
    if response_keys:
        data["response_keys"] = response_keys
    if http_status:
        data["http_status"] = http_status
    if output_preview is not None and LOG_OUTPUT_PREVIEW:
        data["output_preview"] = _preview_text(output_preview)
    level = logging.INFO if success else logging.ERROR
    status = "OK" if success else "FAILED"
    _log_with_data(
        level,
        f"BEDROCK RESPONSE | reqid={request_id} status={status} elapsed={elapsed_s:.3f}s "
        f"tokens_in={tokens_in} tokens_out={tokens_out} model={model}",
        data,
    )
    if output_preview is not None and LOG_OUTPUT_PREVIEW:
        _log_with_data(
            logging.INFO,
            f"BEDROCK OUTPUT   | reqid={request_id} text={_preview_text(output_preview)}",
            {
                "event": "bedrock_output",
                "request_id": request_id,
                "model": model,
                "preview": _preview_text(output_preview),
            },
        )


def log_bedrock_error(
    *,
    request_id: str,
    model: str,
    region: str,
    elapsed_s: float,
    error: str,
    error_type: Optional[str] = None,
    payload_bytes: int = 0,
) -> None:
    """Log a Bedrock invocation error."""
    data = {
        "event": "bedrock_error",
        "request_id": request_id,
        "model": model,
        "region": region,
        "elapsed_s": round(elapsed_s, 3),
        "error": error,
        "error_type": error_type or type(error).__name__,
        "payload_bytes": payload_bytes,
    }
    _log_with_data(
        logging.ERROR,
        f"BEDROCK ERROR    | reqid={request_id} model={model} elapsed={elapsed_s:.3f}s "
        f"error={error}",
        data,
    )


def log_scan_start(
    *,
    request_id: str,
    prompt_len: int,
    truncated_len: int,
    has_context: bool,
    model: str,
    max_tokens: int,
) -> None:
    """Log a BedrockScanner.scan start."""
    data = {
        "event": "scan_start",
        "request_id": request_id,
        "prompt_len": prompt_len,
        "truncated_len": truncated_len,
        "has_context": has_context,
        "model": model,
        "max_tokens": max_tokens,
    }
    _log_with_data(
        logging.INFO,
        f"BEDROCK SCAN START  | reqid={request_id} prompt={prompt_len}→{truncated_len} "
        f"context={has_context} model={model} max_tokens={max_tokens}",
        data,
    )


def log_scan_result(
    *,
    request_id: str,
    risk_score: Any,
    action: str,
    findings_count: int,
    findings_categories: Optional[List[str]] = None,
    llm_guard_score: float = 0.0,
    degraded: bool = False,
    elapsed_s: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    """Log a BedrockScanner.scan result."""
    data = {
        "event": "scan_result",
        "request_id": request_id,
        "risk_score": risk_score,
        "action": action,
        "findings_count": findings_count,
        "findings_categories": findings_categories or [],
        "llm_guard_score": llm_guard_score,
        "degraded": degraded,
        "elapsed_s": round(elapsed_s, 3),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    _log_with_data(
        logging.INFO,
        f"BEDROCK SCAN RESULT | reqid={request_id} risk={risk_score} action={action} "
        f"findings={findings_count} llm_guard={llm_guard_score:.2f} "
        f"degraded={degraded} elapsed={elapsed_s:.3f}s",
        data,
    )


def log_scan_parse_failed(
    *,
    request_id: str,
    content_len: int,
    content_snippet: str,
    reason: str = "json_parse_error",
) -> None:
    """Log a Bedrock scan response parse failure."""
    data = {
        "event": "scan_parse_failed",
        "request_id": request_id,
        "content_len": content_len,
        "reason": reason,
        "content_snippet": content_snippet[:300],
    }
    _log_with_data(
        logging.WARNING,
        f"BEDROCK PARSE FAIL  | reqid={request_id} reason={reason} "
        f"content_len={content_len} snippet={content_snippet[:120]}",
        data,
    )


def log_scan_refusal(*, request_id: str, reason: str = "model_refusal") -> None:
    """Log when the Bedrock model refuses to analyze input."""
    data = {"event": "scan_refusal", "request_id": request_id, "reason": reason}
    _log_with_data(
        logging.WARNING,
        f"BEDROCK REFUSAL     | reqid={request_id} reason={reason}",
        data,
    )


def log_health_check(*, region: str, success: bool, elapsed_s: float = 0.0, error: str = "") -> None:
    """Log a Bedrock health check result."""
    data = {
        "event": "health_check",
        "region": region,
        "success": success,
        "elapsed_s": round(elapsed_s, 3),
    }
    if error:
        data["error"] = error
    level = logging.INFO if success else logging.WARNING
    status = "PASS" if success else "FAIL"
    _log_with_data(
        level,
        f"BEDROCK HEALTH      | region={region} status={status} elapsed={elapsed_s:.3f}s"
        + (f" error={error}" if error else ""),
        data,
    )


def log_metrics(
    *,
    method: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    elapsed_s: float,
    success: bool = True,
) -> None:
    """Log Bedrock call metrics (replaces metrics.record_bedrock_call)."""
    data = {
        "event": "metrics",
        "method": method,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "elapsed_s": round(elapsed_s, 3),
        "success": success,
    }
    _log_with_data(
        logging.INFO,
        f"BEDROCK METRICS     | method={method} model={model} "
        f"tokens={tokens_in}→{tokens_out} elapsed={elapsed_s:.3f}s ok={success}",
        data,
    )


def log_rule_hit(*, rule_id: str, severity: str, request_id: str = "", source: str = "bedrock") -> None:
    """Log a security rule hit from Bedrock analysis."""
    data = {
        "event": "rule_hit",
        "rule_id": rule_id,
        "severity": severity,
        "source": source,
        "request_id": request_id,
    }
    _log_with_data(
        logging.WARNING,
        f"BEDROCK RULE HIT    | rule={rule_id} severity={severity} "
        f"reqid={request_id} source={source}",
        data,
    )
