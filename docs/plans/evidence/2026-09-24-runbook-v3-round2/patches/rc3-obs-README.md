# rc3-obs-v1.patch: typed exposition, reset-safe windows and a windowed GPU-busy signal (M4a/M4b/M4c) for RC3

Lane r2-fix-d2, for proto-builder, who integrates RC3. The source is reviewer-observability M4 (`SP/evidence/reviewer-observability/r2-pass1.md` §M4). I handed M7 to proto-builder's r3-shm item; the section "M7" below explains why.

- **Base:** RC2 (manifest `6093c8dd10bd12f5d5058a963ac9db3ed9768985c4907d73d3a5272c01cff0fc`) plus `c36m-rc2.patch` (sha256 `6e44be471b97ae5a8cebd5214de786a1fa44528892ba2d90e436474617ff8a14`). This patch builds on c36m's shared renderer, including its label escaping.
- **Patch:** `SP/evidence/r2-impl/patches/rc3-obs-v1.patch`. Its sha256 is in `rc3-obs-v1.patch.sha256`. It touches 7 files: 4 modified and 3 new.
- **Evidence:** `SP/evidence/r2-fix-d2/rc3-obs/`.
- **Scope:** only `runtime/metrics.py` (the renderer), `runtime/exporter.py` (the signals), `runtime/monitoring.py` (the pushed list), the new `runtime/windows.py`, and tests. The patch does not touch `edge/ops.py`, `shm_metrics.py`, `control/*`, `admit/state_v2.py`, `detect/` or `egress/`. The worker's `/metrics` route becomes typed through `metrics.prometheus()`, which keeps its signature.

## Applying it

```bash
cd <tree> && patch -p1 --dry-run < SP/evidence/r2-impl/patches/rc3-obs-v1.patch && patch -p1 < SP/evidence/r2-impl/patches/rc3-obs-v1.patch
```

Where it was tried:

- **A fresh copy of RC2 + c36m-rc2.** It applies cleanly, and the result is identical to the tested tree `SP/rc3obs/work` (`apply-base.txt`).
- **The RC3 base `r3git@523ecb8`** (RC2 + C43 push coalescing), plus c36m-rc2, plus this patch. It applies with no offsets and no fuzz. The unit suite passes 161/161 and lint-imports keeps 2 contracts (`unit-rc3base+c36m+obs-pc0211.txt`).
- **The r3-shm branch (`5b59f9c`).** Five of six `exporter.py` hunks conflict. c36m-rc2 already conflicts there too: `ops.py` hunk 1 and both `exporter.py` hunks (`apply-rc3-and-shm.txt`). The three changes edit the same functions, so the RC3 merge of `exporter.py` has to be done by hand. See "Integration notes"; I can do that merge if you want.

## M4a: every family is typed

**Defect (RC2).** No `# TYPE` lines. prometheus_client parses every family as `unknown`, and GMP stores the series as `prometheus.googleapis.com/<name>/unknown`. The HPA asks for `rv2_signal|gauge`, so r2-gke's HPA was blind.

**Fix.** A new function, `metrics.render(parts, samples=...)`, emits valid text 0.0.4. Each family (all series sharing a metric name, from any part) is one contiguous group under one `# TYPE` line. The families are typed as follows:

| Family | `# TYPE` |
|---|---|
| `rv_<name>_total` (counters) | `counter` |
| `rv_<name>` (gauges, per-process gauges `worker="<i>"`) | `gauge` |
| `rv_<hist>{stat=...}` (histogram statistics) | `gauge` |
| `rv2_signal{kind,name}` | `gauge` |

- **Histogram statistics are gauges.** They are computed values, not `_bucket`/`_sum`/`_count` samples, so `histogram` or `summary` would be invalid for this layout.
- **One group per family.** Worker and owner series of the same name (owners carry `role="owner"`) now share one group. Before, they were two runs of the same name.
- **Dropped series are counted.** A series that repeats, that clashes with its family's type, or whose name or labels are invalid is left out. The drop is counted in the existing `# rvproto: N series left out` comment, so the scrape is never invalid.

`prometheus()` (the worker `/metrics` route) and `NodeExporter.render()` (the gateway, guard and unit exporters) both go through `render()`. There are no other producers of Prometheus text. I checked with `grep "prometheus(\|version=0.0.4"`.

## M4b: rates that survive a counter reset

**Defect (RC2).** RC2 computed a window's increase as the counter now minus the previous sample. Workers were matched as merged sums, and owners by segment name. Three things went wrong:

- An owner restart gave a negative window for `owner_requests_per_s`, `owner_shed_per_s` and `owner_queue_*`. The launcher restarts owners in place, under the same name.
- A worker restart gave a negative `requests_per_s`.
- An exporter or node restart published `requests_per_s = 0.0`, and also `cpu_cores_busy` and `cpu_utilization` = 0.0. To an autoscaler, that reads as an idle node.

**Fix.** The new `runtime/windows.py` uses Prometheus counter semantics, per process. A process is identified by its segment name, pid and start time (header word 9). Each process contributes to a window as follows:

| Process | Contributes to the window |
|---|---|
| Same process as the previous sample | `cur - prev`; `cur` if the counter went down (a reset) |
| Started after the previous sample (a new or restarted worker or owner) | all of `cur` |
| First seen, but started before the previous sample (no baseline) | nothing |

- **Missing, not zero.** A signal with no contributing process is left out, never 0.0. That covers the exporter's first window and a node without workers.
- **Histograms.** Loop-lag buckets and exec-time sums use the same rule.
- **CPU.** CPU ticks follow it too, for consistency (see "Other signal changes").

## M4c: `gpu_busy_pct`, measured over the window

**Signal.** `gpu_busy_pct` = 100 × (increase over the window of the owners' `guard_exec_ns` sum) / (`window_s` × owners). It is capped at 100 and published for guard and unit nodes. The window is `window_s`, which is RV_EXPORT_S as measured: 10 s by default.

- **What `guard_exec_ns` measures.** It is recorded once per batch, by the owner's batcher, around the engine's `infer` (`batcher.py:208`). For one owner per GPU, it is the GPU's busy time as the owner sees it, including the H2D and D2H copies.
- **Worker-side `guard_exec_ns` is not used.** Workers also record it, per request in IPC mode, which counts each batch several times.
- **Old signal kept, marked deprecated.** `gpu_utilization` (one nvidia-smi sample per window, as a fraction) stays in /signals, /metrics and the push. Lane tooling reads it: r2-mig `monitoring_pull.py` and `platform_timeline.py`, r2-chaos `m4b_signals.py`, r2-gke `prom_pull.py`, and `f_split150.py`. Keeping it lets RC3 runs show both CVs on the same windows. Dropping it is a one-line change if the controller prefers.
- **Now pushed to Cloud Monitoring.** `gpu_busy_pct` and `window_s` are added to the pushed list (`PUSHED`).

## Other signal changes

- **`cpu_cores_busy` / `cpu_utilization`.** In the first window they are left out instead of 0.0. A process that started inside the window now counts all of its CPU ticks; RC2 counted 0 for it.
- **`requests_per_s`.** In the first window it is left out instead of 0.0.

## M7: handed to r3-shm (team-lead informed ~13:25Z)

proto-builder's r3-shm item (the controller's RemoveIPC item, `r3/shm`, now commit `5b59f9c`) already implements M7, and more:

- `ops.node_readiness` fails closed on a missing node file (`node_file_missing`).
- Segment self-check and re-publish.
- In the exporter: `rv_shm_lost_total`, `rv_shm_segments_lost`, `rv_shm_node_file_rewrites_total`, and readiness reasons `shm_segment_lost` / `workers_missing`.
- Its spec requires the deletion test.

It edits exactly the lines M7 would edit. I removed my M7 edits so the two patches would not conflict line by line.

## Tests: they fail on RC2 and pass with the patch

| Test (new) | RC2 + c36m, pc 0.21.1 / 0.25.0 | Patched, 0.21.1 / 0.25.0 |
|---|---|---|
| `test_prometheus_types.py::test_node_exporter_families_are_all_typed[gateway,guard,unit]` (over HTTP, parsed by prometheus_client, plus a text-level check: one TYPE per family, no split, no sample outside its group) | FAIL: `rv_gc_pause_ns parsed as 'unknown'` | pass |
| `…::test_worker_metrics_route_families_are_all_typed[c38-off,c38-on]` (GET /metrics through `ops.handle`) | FAIL: `unknown` | pass |
| `…::test_a_type_clash_or_a_repeat_is_left_out_and_counted_never_a_second_type` | FAIL: no `render` | pass |
| `test_signal_windows.py::test_a_first_window_leaves_the_rates_out_never_zero` (real processes, exporter restart) | FAIL: `{'requests_per_s': 0.0, 'cpu_cores_busy': 0.0, 'cpu_utilization': 0.0}` | pass |
| `…::test_a_worker_or_owner_restart_never_gives_a_negative_window` (reset injection: SIGKILL, then the same index restarted from zero) | FAIL: `requests_per_s -1047`, `owner_requests_per_s -5023`, `owner_shed_per_s -39` | pass (exact increases 3 / 7 / 1) |
| `…::test_gpu_busy_pct_is_owner_execution_time_over_the_window` (steady, restart, idle = 0.0, cap 100) | FAIL: `KeyError 'gpu_busy_pct'` | pass |
| `…::test_window_counts_each_process_against_its_own_previous_sample`, `…histograms_reset_per_process` (pure) | FAIL: no module | pass |

Evidence files: `newtests-{base,patched}-pc{0211,0250}.txt`. Full suite:

- Patched: 158/158 under both 0.21.1 and 0.25.0 (`unit-work-pc0211.txt`, `unit-work-pc0250.txt`).
- Base: 147/147 (`unit-base-pc0211.txt`).
- lint-imports keeps 2 contracts, and the GW00 gates pass (longest new function 34 lines; no module-level mutable state).

In one existing test (`test_v2_fixes._parse_prometheus`), the line count now skips `#` lines.

## Live smoke: a real owner process and the real exporter threads (local only)

`smoke_owner_restart.py` sets up:

- The owner: `tests/functional/fake_engine_owner.py`, which is the real owner, queue, batcher and C38 segment, with the engine simulated at 2.2 ms per window.
- The real `NodeExporter` threads, with RV_EXPORT_S = 3 s.
- A load of 100 req/s through the real `OwnerClientBackend`.
- The fault: right after a tick, the owner is SIGKILLed and restarted in place.

Full table: `smoke-summary.txt`.

| Tree | Restart window `owner_requests_per_s` | Steady `gpu_busy_pct` | Families |
|---|---|---|---|
| RC2 + c36m | **-305.2** | none | 22 × `unknown` |
| patched | 70.3 (the new owner's true count) | 23.4 %, CV 0.005 (steady `owner_requests_per_s` CV 0.000) | 7 counter + 15 gauge |

On RC2 this is the negative window the reviewer predicted but no lane had captured. The restart window's `cpu_cores_busy` was 0.0 on RC2; with the patch it is 0.566, the new owner's start-up CPU.

## Integration notes (proto-builder)

0. **The base differs from your RC3 base.** This patch is cut against RC2 + c36m-rc2 (RC2 manifest `6093c8dd…`, c36m `6e44be47…`), as the controller specified. Your RC3 base is `r3git rc3@523ecb8`, which is RC2 + C43 push coalescing and does not include c36m. The order that works is: 523ecb8, then c36m-rc2.patch, then this patch. That applies with no offsets and no fuzz, and passes 161/161 (`apply-rc3-and-shm.txt`, `unit-rc3base+c36m+obs-pc0211.txt`). Applied straight onto 523ecb8 without c36m, 3 hunks fail (checked): `exporter.py` #2 and #6, and `metrics.py` #2. `render()` builds on c36m's `_series`/`label` escaping.
1. **Untyped lines from r3-shm.** r3-shm's exporter `render()` appends three raw lines: `rv_shm_lost_total`, `rv_shm_segments_lost`, `rv_shm_node_file_rewrites_total`. After this patch, pass them as `samples=[(name, "counter"|"gauge"|"counter", number(v))]` to `render()`. `test_prometheus_types` fails on any untyped family.
2. **Signal-tick conflict.** r3-shm adds `sig["shm_lost_total"]` in `_signal_tick`. This patch replaces that function's body (`procs` / `Window` / `born`), so keep both.
3. **Segment accessors.** `Segment` gains `started_ns` here and `incarnation` / `generation` in r3-shm. These are independent.

## USAGE.md snippet (replaces the signal bullets in §5 Observability)

- **Exporter `/metrics`** is typed Prometheus text (RC3). Each family has one `# TYPE` line followed by all of its samples:
  - counters `rv_<name>_total`: `counter`;
  - gauges, per-process gauges and histogram statistics `rv_<hist>{stat=...}`: `gauge`;
  - `rv2_signal{kind,name}`: `gauge`.
  Under GMP, series are stored as `prometheus.googleapis.com/<name>/{counter,gauge}`, never `/unknown`.
- **Window.** Every rate and average in `rv2_signal` covers one exporter window. `window_s` (RV_EXPORT_S as measured, 10 s by default) is published beside them and pushed to Cloud Monitoring.
- **Reset-safe rates.** Rates are safe across worker, owner and exporter restarts:
  - each window reports its true increase, never negative;
  - a restarted process counts from its start;
  - a signal without a baseline (the exporter's first window) is absent, not 0.0.
- **`gpu_busy_pct`** (guard / unit) is the owners' engine execution time over the window, in percent, capped at 100. Scale on it or on `owner_requests_per_s`.
- **`gpu_utilization`** (one nvidia-smi sample, a fraction) is DEPRECATED: comparison only.

## Open items

- **Histogram statistics stay gauge-typed statistics.** `stat="n"` is cumulative inside a gauge family. PromQL `rate()` still works on it, but GMP stores it as a gauge. A native Prometheus histogram layout (`le` buckets, which would give windowed quantiles server-side: M1) would be a new, higher-cardinality layout, so I left it out.
- **Two limits of `gpu_busy_pct`.**
  - An owner that starts inside a window is averaged over the whole window, which slightly under-reads that one window.
  - With more than one owner per GPU, the value is per owner, not per GPU.
- **GMP typing needs a live check.** It has to be confirmed on r2-gke with an RC3 image: the descriptors must show `/gauge` for `rv2_signal`, and the HPA's `currentMetrics` must be populated.
