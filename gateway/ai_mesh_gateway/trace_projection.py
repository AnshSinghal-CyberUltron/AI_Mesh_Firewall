"""Project a ``pipeline_trace`` down to metrics — task 6.

MEASURED on one allowed non-streaming request with a 3.5 KB prompt: the response body is
38,225 B and the trace is 35,572 B of it — **91%**. Inside the trace, ``stages`` is
28,354 B, and 23,538 B of that is echoed text: the prompt appears in ``prompt_submitted``,
``prompt_preview`` and ``input_text`` at the root and again inside each stage's
``content`` / ``prompt_in`` / ``prompt_out``.

Reduced to name/action/latency the stages are 587 B and the whole trace is 684 B — a
**52x** reduction — while keeping every field the perf harness reads.

``_scrub_trace_for_client`` is a different tool for a different job: it strips *evidence*
from a blocked response and deep-copies to do it. This projection strips *bulk*, and in
``full`` mode does not copy at all.
"""
from __future__ import annotations

import os

# What the perf harness must still be able to read. `drive.py` / `load.py` assert that
# all nine stages ran — the check that stops a SHORTER pipeline being reported as a full
# one — and attribute the tail using per-stage latencies. Dropping any of these would
# disable an honesty check while still appearing to work.
HARNESS_REQUIRED_STAGE_KEYS = ("name", "action", "latency_ms")
HARNESS_REQUIRED_ROOT_KEYS = (
    "t_addon_pre_ms", "t_addon_post_ms", "total_ms", "overhead_ms",
)

# Kept because they are small and are what makes a trace diagnostically useful.
_KEEP_STAGE_KEYS = HARNESS_REQUIRED_STAGE_KEYS + ("threat_type", "category")
_KEEP_ROOT_KEYS = HARNESS_REQUIRED_ROOT_KEYS + (
    "request_id", "final_action", "guard_summary", "latency_breakdown",
)

# The duplication that is 98% of the bytes.
TEXT_PAYLOAD_KEYS = (
    "content", "prompt_in", "prompt_out", "prompt_submitted", "prompt_preview",
    "input_text", "input_text_before", "input_text_after",
    "output_text", "final_response",
)

_MODE_ENV = "GATEWAY_PIPELINE_TRACE_MODE"


def trace_mode() -> str:
    """`full` (default — today's behaviour, unchanged) or `metrics`."""
    return (os.environ.get(_MODE_ENV) or "full").strip().lower()


def project_pipeline_trace(trace, *, mode: str | None = None):
    """Return ``trace`` projected for the client.

    ``full`` returns the SAME OBJECT — not a copy — so the default path costs one env
    lookup and nothing else. An unrecognised mode also returns it unchanged: an operator
    typo must not silently strip diagnostics.

    Never raises. A trace is diagnostic data; failing to project one must not be able to
    fail the request it describes.
    """
    if (mode or trace_mode()) != "metrics":
        return trace
    if not isinstance(trace, dict):
        return trace
    try:
        out = {k: v for k, v in trace.items() if k in _KEEP_ROOT_KEYS}
        stages = trace.get("stages")
        if isinstance(stages, list):
            out["stages"] = [
                {k: v for k, v in s.items() if k in _KEEP_STAGE_KEYS}
                for s in stages if isinstance(s, dict)
            ]
        elif "stages" in trace:
            out["stages"] = stages
        return out
    except Exception:  # noqa: BLE001 — diagnostics must never break a response
        return trace
