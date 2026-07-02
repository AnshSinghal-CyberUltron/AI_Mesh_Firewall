# P9.29 (Cursor angle) — Sustained load: sandbox reuse/pooling, pids-cgroup limits, no orphan/503 storm

**Item:** P9 #29 — "Load: sustained calls — sandbox reuse/pooling holds, no exhaustion,
no 503 storms, resource limits respected."

**Cursor scope (per `docs/mcp/PARALLEL_CLAIMS.md`):** the broker / `docker_manager` /
sandbox-agent side of #29. The gateway/control reliability *ceiling* (the intermittent
`-32000` errors documented in p9-28) is Claude-owned (`scripts/mcp_load_live.py`,
`claude-mcp-item29-P9.29.claim`). This file proves the **pooling/reuse/limit** invariants
that live in Cursor-owned code.

**Verdict: GREEN 3× consecutively.** Zero duplicate containers, zero orphan spawns, pid
cgroup flat and far under the limit throughout, zero HTTP-503 (no storm), full recovery
after every run.

## What the harness now does (Phase 3.6, opt-in `SUSTAINED=1`)

`scripts/mcp_multi_org_harness.py` gained a **sustained-load** phase (Cursor-owned):

- Holds a **steady `SUSTAINED_INFLIGHT` (=90) in-flight concurrency** across all 15
  (org×server) targets for `SUSTAINED_SECONDS` (=90s), **continuously refilling** as calls
  complete — a true sustained stream, not a single burst. 90-wide sits *below* the
  ~180-wide control/gateway saturation ceiling from p9-28, so the Cursor-owned broker path
  is exercised in its clean regime.
- Alternates `echo` (unique per-target canary naming its owning `(org,server)`) and
  `get-sum` (org+server-salted operands) so the item-#28 isolation classes still apply
  under sustained pressure: `dropped / id_mismatch / mixed / cross_target`.
- Splits reliability into **HTTP-503** (broker/gateway backpressure) vs **JSON-RPC error
  body** (correct id, no result). Buckets 503 completions per wall-clock second →
  `max_503_per_sec` is the **503-storm** metric.
- Optional **overload micro-burst** (`SUSTAINED_OVERLOAD=1`, 300-wide single wave) after
  the steady window = the 503/retry probe, followed by a **recovery probe** (every target
  must serve `tools/list` again → proves no lasting exhaustion / wedged pool).

Gate (`sustained_report["pass"]`): `isolation_violations==0` AND `err_rate ≤ 2%` AND
`rate_503 ≤ 1%` AND `max_503_per_sec ≤ 20` AND `recovery == all targets`.

Default (`SUSTAINED` unset) behaviour is unchanged — the #28 storm gate still runs as-is.

Re-run gate:
```
for i in 1 2 3; do
  SUSTAINED=1 SUSTAINED_SECONDS=90 SUSTAINED_INFLIGHT=90 SUSTAINED_WORKERS=120 \
  SUSTAINED_OVERLOAD=1 SUSTAINED_OVERLOAD_WIDTH=300 \
  REPORT_PATH=mcp-parallel/findings/p9-29/CURSOR_sustained_run$i.json \
  python3 scripts/mcp_multi_org_harness.py | grep -E 'sustained:|HARNESS:'
done
```

## Graded results — GREEN 3× (inflight 90, 90s steady + 300-wide overload + recovery)

| run | steady calls | passed | errored (jsonrpc) | http_503 | iso viol | err% | max 503/s | rps | p50 ms | p99 ms | overload | recovery |
|----:|-------------:|-------:|------------------:|---------:|---------:|-----:|----------:|----:|-------:|-------:|---------:|---------:|
| 1 | 3283 | 3266 | 17 | 0 | 0 | 0.518% | 0 | 35.0 | 1953 | 4794 | 300/300 | 15/15 |
| 2 | 2454 | 2454 | 0  | 0 | 0 | 0.0%   | 0 | 26.8 | 3404 | 5086 | 300/300 | 15/15 |
| 3 | 4318 | 4318 | 0  | 0 | 0 | 0.0%   | 0 | 47.1 | 1844 | 3960 | 300/300 | 15/15 |

- **10,055 sustained calls** total → **17** transient JSON-RPC errors (0.17% aggregate),
  **0** HTTP-503, **0** isolation violations, **0** drops/mixes/cross-target.
- The 17 errors are the p9-28 **control-plane** blips (`tool_not_registered` 403 / DRF 500
  → gateway `-32000`), not a broker fault — they vanish entirely in runs 2–3 and never
  appear as a 503 or a drop/mix. rps varied 27–47 (a concurrent Claude load session shares
  the control plane); latency degrades gracefully (queueing), never errors out.
- **Overload 300-wide** each run: **300/300 passed, 0 × 503** — the fleet absorbs a 2×
  over-ceiling burst by *raising latency* (p50 ~5.4s), not by 503-storming or dropping.

## Sandbox reuse / pooling — no duplicate containers, no orphan storm

Docker sampled every 3s for the whole run-1 window
(`CURSOR_docker_samples_run1.txt`, 33 samples over ~100s):

- `sandbox_containers=5` **constant** the entire window (3 provisioned orgs + 2 pre-existing
  unhealthy orphans from an earlier session). **No new container was ever spawned under
  load** → the per-org sandbox is reused for every call across all 5 of its servers.
- Container **IDs identical** at baseline and post-load:
  `zeroshield=ce651a98198c`, `org-a=2047af4aa278`, `org-b=3c2353b834fa` (uptime advanced
  continuously, never restarted) → one-sandbox-per-org **pooling holds**.

## Resource limits respected — pid cgroup flat, no fork/mem exhaustion

- `pids.current` = **123 / max 256** on all three sandboxes, **flat for the entire
  sustained window** (a single transient blip to 125 on org-b) → tool calls reuse the
  already-running stdio server processes; there is **no per-call fork growth**, so the
  `pids_limit` cgroup cap is never approached. Post-load pids returned to 123 on all three.
- `memory.current` stable at ~590 MB (zeroshield) / ~427 MB (org-a) / ~428 MB (org-b),
  well under the 2 GB `mem_limit` — no memory leak/creep under sustained load.
- Inspected limits (live): `pids_limit=256`, `mem=2G` (`memswap=2G`, no swap-bypass),
  `nano_cpus=1e9` (1 CPU), `read_only=true`, `cap_drop=[ALL]` on every sandbox.

## Recovery — no wedged pool

Every run's post-load recovery probe returned **15/15** targets serving `tools/list`, and
broker health stayed `healthy` / gateway responsive (401=up) throughout. No sandbox needed
recreation; no reaper thrash.

## Conclusion (Cursor-owned invariants for #29)

Under sustained (90s × 90-in-flight, ×3) and 2×-over-ceiling burst load, the Cursor-owned
broker path is clean: **sandbox reuse/pooling holds (1/org, same IDs, no dup), pids/mem
cgroup limits are respected with wide headroom, no orphan sandboxes are spawned, there is
no 503 storm (0 × 503), and the fleet fully recovers.** The only load-facing degradation is
the intermittent control/gateway `-32000` (Claude-owned ceiling, 0.17% here, bounded well
under gate), which manifests as latency, never as a broker drop/mix/leak/exhaustion.

Evidence: `CURSOR_sustained_run{1,2,3}.json`, `CURSOR_docker_samples_run1.txt`.
