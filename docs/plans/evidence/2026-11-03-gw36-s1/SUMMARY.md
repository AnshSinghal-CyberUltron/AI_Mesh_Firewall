# GW36 S1 — production upstream provider: evidence (3 November 2026)

Plan and review outcome: `docs/plans/2026-11-03-gw36-s1-upstream-provider.md`.

## Offline

- Full gateway_v2 suite in a `python:3.12-slim` container (repo mounted, deps from
  `pyproject.toml` `[dev,conformance,serve]`): 2845 passed, 108 skipped, 1 xfailed, 1 failed —
  the one failure is `tests/gates/test_secret_headers.py::test_current_tracked_tree_yields_zero_hits`,
  which needs `git` (absent from the slim image); on the host it passes (24/24).
  `full_offline_suite_container.txt`.
- `tests/edge` re-run after the streamed-trace fix: 784 passed, 5 skipped.
  `edge_rerun_after_trace_fix.txt`.
- Host gates: `mypy --strict` 214 files, ruff (package + tests), import-linter 3/3, capacity /
  module-mutable / frozen / http-outside-edge / sizes / tenant-scale / dispatch-provenance AST gates
  exit 0.
- Why a container: on 3 November, after a host reboot, this agent's processes got EPERM opening
  some files (for example `.venv/.../numpy/lib/_histograms_impl.py` and some test files) even
  though they are 664 and owned by the user, with no AppArmor denial logged. Docker reads the same
  files, so the suite runs in a container over the same tree.

## Live (`run_live_s1.py`, `live_s1.json`, `live_s1_output.txt`)

Topology: a second dev-profile gateway `gw36-s1-gateway` (image built from this tree) on
127.0.0.1:8302 with `AMF_DEV_PROVIDER_URL=http://gw36-t02-recorder:8080/v1`; the T02 recorder
`gw36-t02-recorder` (`staging/t02/recorder.py`) with its admin API on 127.0.0.1:18081. The user's
:8301 gateway stayed on the echo provider. 15/15 checks passed:

| Check | Observed |
|---|---|
| roles + mask | recorder saw roles `system, user, assistant, user`, `contains_ssn` false, prompt `my ssn is [REDACTED]`, text parts joined `and go`, `max_tokens` 4096 injected |
| budget rule | `max_tokens` 100 forwarded; `max_completion_tokens` 50 forwarded with no `max_tokens` |
| legacy completions | `prompt` with an SSN → one `user` turn, `ssn [REDACTED]` |
| stop | `["x [REDACTED]", "END"]` |
| tools / image part / injection | 400 `unsupported_parameter` / `unsupported_content_part` / `input_blocked`, 0 recorder calls each |
| stream | 200 SSE with the recorder content and `[DONE]` |
| recorder faults | 429 → 429 `upstream_rate_limited`; 500 → 502 `upstream_error`; 401 → 502 `upstream_auth_error`; 200 non-SSE → 502 `stream_malformed_upstream`; disconnect → 502 `upstream_unavailable`; streamed 429 → SSE error frame `upstream_rate_limited`, trace `final_action` block (was `allow` before the fix) |
| no raw SSN | 12 recorded calls, `contains_ssn` false on all |

Found during the live run and fixed: (1) a stream cut by an upstream failure reported
`final_action: allow` in its `amf.trace` (the pass-through loop ends normally after the latch
cut); `ChatRoute._finish_stream` now records `block` for any non-kill-switch cut. (2) The
recorder's `disconnect` fault only called `close()`, which leaves the socket open through the
handler's file objects, so the client saw a stall; it now shuts the socket down.

Recorded, not fixed: a stalled upstream (no bytes) holds a non-streaming request until the httpx
read timeout (`max_stream_duration_s`, 300 s) because the non-streaming path has no edge bound;
plan-declared per-provider/model timeouts (C27) are S5 / GW28. An upstream failure shows
`output_guardrail: block` in the trace; an honest `error` action needs the console schema (GW35).
