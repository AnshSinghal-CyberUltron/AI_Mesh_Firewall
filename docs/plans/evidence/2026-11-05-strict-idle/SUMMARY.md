# Live check: strict-withhold-idle-timeout fix (task 5, 5 Nov 2026)

Branch `bugfix-f1-strict-idle`. Tasks 1–4 are done (offline gate green). This task runs the fix
against a REAL booted gateway-v2 and compares it to the recorded unfixed behaviour.

Goal: prove on a real stack that a healthy long STRICT_WITHHOLD stream (a D-20-promoted org) is no
longer cut with a false 408 `stream_idle_timeout`, and that the preservation cases still hold.

## Stacks

- **AFTER = `amff1`** (compose project `amff1`, gateway-v2 at `127.0.0.1:33301`), built and run
  from THIS worktree. The fix (`withholds_until_decision` + `StreamTimeouts.upstream_liveness` +
  the `ChatStream._controls` wiring) is in the working tree (uncommitted — task 7 commits) and the
  `docker build` COPYs the working tree, so the amff1 image IS the fixed code. Verified: the three
  changed files (`edge/stream_timeouts.py`, `edge/chat_stream.py`, `egress/output_guard.py`) differ
  from HEAD; `egress/strict.py` and the trace builder are untouched by F1.
  Minimal services: gateway-v2, control, postgres, pgbouncer, redis, rabbitmq, the two state
  services (signed state), the v1 gateway + mcp-broker/mcp-stub, and the fake OpenAI upstream test
  double. `AMF_AUDIT_RETENTION_SWEEP` was never set. Frontend not started.
- **BEFORE = the F1 integration artifact** `docs/plans/evidence/2026-11-04-integration/l34-python-
  sdk-strict-withhold.jsonl` (see check a). `amfint` is read-only here and is NOT strict-promoted,
  so it is not forced into the before role.

### Port note

Base **+25000** (gateway-v2 → 33301, control → 33100, v1 gateway → 33300, redis → 31379, postgres
→ 30432, pgbouncer → 31432, rabbitmq → 30672/30673, mcp-broker → 33311, mcp-stub → 34999). Every
mapped port ≤ 65535, all bound to 127.0.0.1, all distinct from `amfint` (+30000) and
`ai_mesh_firewall` (+20000). `ss -ltn` checked before boot — no collisions. Full map:
`stack-ports.txt`. (The F2 run used +55000 because +60000 overflowed 65535; +25000 as asked here is
well under the max and collision-free.)

### How the test org was D-20 promoted (documented exactly)

The dev gateway-v2 runs the DEV PROFILE with a static signed plan at `AMF_DEV_PLAN_PATH`. D-20 lives
in the control-plane publisher, which the dev profile does not use; the equivalent promotion is done
in the dev plan file itself, exactly the shape D-20 produces:

- `docker-compose.amff1.yml` mounts `plan.strict.json` as `AMF_DEV_PLAN_PATH` for the PRIMARY org
  `dev-org`. That plan sets `"streaming_mode": "strict_withhold"` AND carries an enforcing OUTPUT
  rule `fw-ip-leak-output` (category `ip_leak`, scope `output`, mode `enforce`, action `block`,
  condition `kind=detector detector_class=output.ip_leak`). `enforcing_output_rules(plan)` is then
  non-empty and `withholds_until_decision(plan)` is True — the exact bug condition.
- The PEER org `org-b` is the INCREMENTAL control. `up.sh` publishes `plan.incremental.json` (no
  enforcing output rule, `incremental`) to `{rv2}:devplan:org-b`; the dev plan watch compiles and
  applies it off the request path.

Verified via the live boot log (`gateway-v2-boot-promotion.txt`): `org=dev-org
streaming=strict_withhold; rules=[... fw-ip-leak-output:ip_leak->block/enforce ...]` and
`dev: firewall plan applied version=...` for org-b. So the gateway really resolves `strict` for the
test org and `incremental` for the control org.

### Containers before/after

`docker-ps-BEFORE.txt` vs `docker-ps-AFTER.txt`: every non-`amff1` container (all `amfint-*` and all
`ai_mesh_firewall-*`) is unchanged — same name and same status word before and after. Only `amff1-*`
resources were created. (The `ai_mesh_firewall-state-*` services were already in a pre-existing
crash-loop BEFORE this run and remain so — unrelated to this task.)

## Results

PASS = executed on real HTTP/SSE wire traffic and the criterion was met. NOT RUN = not executed,
reason given. The metric named for the idle criterion is
`amf_posture_refusals_stream_idle_timeout_total`.

| # | Check | Result | Evidence / reason |
|---|---|---|---|
| a | BEFORE (unfixed): strict org + SLOWSTREAM returns 408 `stream_idle_timeout` before any byte | **PASS (recorded BEFORE, cited)** | `amfint` is read-only and currently runs the dev `plan.example.json` (`incremental`, no enforcing output rule) — it is NOT strict-promoted, and I did not modify it. Per the task, the recorded BEFORE is the F1 integration artifact `../2026-11-04-integration/l34-python-sdk-strict-withhold.jsonl`, whose `cancellation` cell is `{"ok": false, "detail": "APIStatusError: Error code: 408 - The downstream client went idle and the stream was terminated."}` — the exact false 408 on a strict SLOWSTREAM, on unfixed code. Reproducing it on amff1 is impossible because amff1 carries the fix; reproducing it on amfint would require promoting amfint (forbidden). |
| b | AFTER (fixed): strict org + SLOWSTREAM → 200, full content after ~6 s, zero 408, idle counter unchanged | **PASS** | `after-checks.jsonl` (`b_strict_slowstream_after`). HTTP **200**, `content-type: text/event-stream`, time-to-first-byte **6.15 s** (withheld until the whole 6 s response was decided), full content `word0 … word39` (270 chars), `done_on_wire: true`, `output_guardrail action=allow`. SDK: `ok: true`. **Zero 408.** `amf_posture_refusals_stream_idle_timeout_total` = **0 before AND after** (`idle_counter_unchanged: true`). Upstream log: the `slow` script ran to completion (`aborted: false`) — the gateway consumed all 40 chunks and did not cut. NB on the metric: in the dev profile the `amf_posture_refusals_stream_*` counters are not fed by the stream path (they read 0 for every code, including the `killed`/`output_blocked` cuts below), so the authoritative proof is the 200 + full content on the wire; the idle counter staying 0 is a confirming, not sole, signal. |
| c | AFTER: strict org + a payload tripping the enforcing rule → terminal `output_blocked`, no content before it | **PASS** | `after-checks.jsonl` (`c_strict_slowleak_after`). The `SLOWLEAK` upstream emits an internal IP (`10.1.2.3`) mid-stream. Result: HTTP **403**, header `x-zeroshield-action: block`, **content_len 0** (zero content before the block), SDK code `output_blocked` ("The response was withheld by the output guard."). The pipeline trace shows `output_guardrail action=block deciding_rule=fw-ip-leak-output`. So the enforcing `output.ip_leak` rule blocked the withheld response terminally with nothing released first. |
| d | AFTER: a stall variant (upstream pauses mid-stream) → still cut by the upstream-stall guard, NOT a false idle cut | **PASS (substance); code spelling is the pre-existing strict `killed`)** | `after-checks.jsonl` (`d_strict_slowstall_after`) + `d2-incremental-slowstall.jsonl` + `fake-openai-upstream.log`. The `SLOWSTALL` upstream sends 3 chunks then pauses 1.5 s (> the routed 0.8 s inter-chunk gap bound). The stream is cut at **~1.26 s** (= 3×0.15 s + 0.8 s) and the upstream is `aborted: true` — the stall guard fired, the stream was NOT held open, and it is **NOT** a `stream_idle_timeout` (idle counter 0, `idle_unchanged: true`). The client-facing code is the pre-existing strict kill rendering, not the literal `stream_inter_chunk_timeout`: on the STRICT path nothing is written before the decision, so `StrictWithholdPipeline._cut_on_kill` (untouched by F1) renders the generic `killed` (HTTP 500 `stream_killed`); on the INCREMENTAL control the first byte is already out, so it is a 200 + mid-stream error frame whose trace reason is `upstream_timeout`. Both are the stall guard, both pre-date this fix (F1 touches only the three listed files), and neither is the false idle cut this fix targets. The `amf_posture_refusals_stream_inter_chunk_timeout_total` counter did not increment (same dev-profile limitation as b). |
| e | AFTER: INCREMENTAL org + SLOWSTREAM unchanged (streams each chunk) | **PASS** | `after-checks.jsonl` (`e_incremental_slowstream_after`). HTTP **200**, time-to-first-byte **0.307 s**, **42 data frames** (each chunk streamed as it arrived — the incremental write-based model), full content, `done_on_wire: true`, `output_guardrail action=allow`. Unchanged by the fix (`upstream_liveness=False` for this org). |
| e-stall | AFTER: an INCREMENTAL client that stops reading is still cut with idle | **NOT RUN** | Attempted (`client_stall.py`, `e-client-stall.jsonl`): a raw socket read the headers + first frame then stopped reading for 8 s (> the 3 s idle bound) with `SO_RCVBUF=2048`. The server never back-pressured — the whole ~9.7 KB echo stream (40 short chunks) fit in the OS/uvicorn socket buffers, so `send()` never blocked past the idle bound and no idle cut occurred (HTTP 200, full `allow` trace at 11.9 s). Building real downstream back-pressure needs a much larger stream or a socket-level throttle this harness cannot impose over the Docker loopback. Covered offline instead: Property 3 and the `StreamTimeouts` unit tests assert an INCREMENTAL inter-write gap over `idle_timeout_s` is cut with `stream_idle_timeout` before the write (requirement 3.1), and task-2 preservation recorded the same on unfixed code. |

**Counts: 5 PASS (a cited-before, b, c, d, e), 1 NOT RUN (e-stall client back-pressure).**

## What this proves about the fix

- The exact bug (a healthy 6 s withheld strict stream returning 408 before any byte — the F1
  artifact's `cancellation` cell) is GONE on the fixed stack: the same strict SLOWSTREAM returns 200
  with the full content and the idle counter never moves (check b vs a).
- The decision path is intact: the enforcing `output.ip_leak` rule still blocks terminally with zero
  content before the block (check c), the upstream-stall guard still cuts a stalled upstream within
  its bound without a false idle cut (check d), and INCREMENTAL streaming is byte-for-byte unchanged
  (check e). These are the preservation requirements 2.3, 3.1, 3.2, 3.6, 3.7.

## Reproduce / teardown

- Bring up: `bash docs/plans/evidence/2026-11-05-strict-idle/up.sh` (project `amff1`; needs `./.env`
  at the worktree root — a gitignored copy of the shared dev `AMF_STATE_HMAC_KEY` /
  `POLICY_SIGNING_KEY`, taken from the integration worktree).
- Drivers: `driver.py` (checks b–e, run in a `python:3.12-slim` container on `amff1_default`),
  `client_stall.py` (the e-stall attempt). Fake upstream: `fake_openai_upstream.py`
  (SLOWSTREAM / SLOWLEAK / SLOWSTALL).
- Teardown (ONLY amff1 resources): `COMPOSE_PROJECT_NAME=amff1 docker compose -f docker-compose.yml
  -f docker-compose.override.yml -f docs/plans/evidence/2026-11-05-strict-idle/docker-compose.amff1.yml
  --profile services down -v` then `docker rm -f amff1-fake-openai`.
