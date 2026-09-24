#!/usr/bin/env python3
"""Attribute py-spy non-idle samples (all gateway workers) to v1 cost buckets.

Step 1 (pipeline phase, ANY frame of the stack, priority order): scanning work is charged to the phase
that invoked it (patterns.py under secure_streaming._flush_buffer is output-guard cost, under
_redact_trace_text is trace cost, ...).
Step 2 (otherwise, innermost recognised frame, leaf -> root): infrastructure category.
Blocking waits py-spy did not mark idle are excluded and counted separately.
usage: pyspy_buckets.py DIR"""
import sys
from collections import Counter
from pathlib import Path
IDLE_LEAVES = ("_worker (concurrent/futures/thread.py:90)", "_loop (ai_mesh_gateway/policy_engine.py:304)",
               "run (asyncio/runners.py:118)")
WAIT_LEAVES = ("run (ai_mesh_gateway/policy_engine.py:313)",)   # caller parked on the policy worker's reply queue
PHASES = [
    ("output guard scan (secure_streaming._flush_buffer / output_guard.py)", ("_flush_buffer", "output_guard.py")),
    ("trace/telemetry building incl. its redaction", ("_redact_trace_text", "build_pipeline_trace", "telemetry.py", "_emit_telemetry", "trace_projection", "pipeline_trace.py")),
    ("policy engine (compiled regex/keyword rules)", ("policy_engine.py",)),
    ("input scan / PII+secret redaction (scanner.py, patterns.py)", ("scanner.py", "patterns.py", "typed_placeholder_redactor", "redaction.py")),
]
INFRA = [
    ("LiteLLM + OpenAI SDK + pydantic per-chunk objects + httpx", ("litellm/", "streaming_handler.py", "/router.py", "openai/", "pydantic", "httpx/", "httpcore/")),
    ("Starlette BaseHTTPMiddleware / anyio task-group plumbing", ("starlette/", "anyio/")),
    ("json (de)serialisation", ("json/", "orjson")),
    ("logging", ("logging/",)),
    ("redis client", ("redis/",)),
    ("uvicorn / h11 HTTP server", ("uvicorn/", "h11/")),
    ("gateway app code (main/stream_orchestration/llm_router/middleware ...)", ("ai_mesh_gateway/",)),
    ("asyncio core", ("asyncio/",)),
]
def main():
  b = Counter(); total = 0; idle = 0; waits = 0
  for f in Path(sys.argv[1]).glob("worker-*.txt"):
      for line in f.read_text().splitlines():
          if not line.strip():
              continue
          stack, _, n = line.rpartition(" ")
          n = int(n)
          frames = [x.strip() for x in stack.split(";")]
          if not frames[-1] or frames[-1] in IDLE_LEAVES:
              idle += n; continue
          if frames[-1] in WAIT_LEAVES:
              waits += n; continue
          total += n
          hit = None
          for name, keys in PHASES:
              if any(k in fr for fr in frames for k in keys):
                  hit = name; break
          if hit is None:
              for fr in reversed(frames):
                  for name, keys in INFRA:
                      if any(k in fr for k in keys):
                          hit = name; break
                  if hit:
                      break
          b[hit or "other"] += n
  print(f"non-idle, non-wait samples: {total} (idle-wait {idle}, policy reply-queue wait {waits})")
  for k, v in b.most_common():
      print(f"{100*v/total:6.2f}%  {k}")


if __name__ == "__main__":
    main()
