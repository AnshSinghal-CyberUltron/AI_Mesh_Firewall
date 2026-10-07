# Round-2 interim contradiction review, pass 1: OBSERVABILITY (reviewer-observability, 2026-09-24 ≈12:25–13:25Z)

**Scope.** I looked for places where metrics, logs, signals and the operator surface disagree with what actually happened on the wire. I checked round-2 measurements (RC1 claim runs, the r2-mig dry-run autoscaler, the r2-state RC2 FLUSHALL run) and the RC2 source.

**How the RC2 source was checked.** The tarball SP/evidence/r2-impl/rc2/rvproto2-rc2.tar.gz (sha256 eb9d4da2…, manifest 6093c8dd) was extracted to RO/r2/rc2src. This review is read-only: local scripts ran over evidence files only; no GCP resource or lane VM was touched.

**Paths.**
- RO = SP/evidence/reviewer-observability
- RAW = ~/rv-evidence-raw
- PY = gateway/.venv/bin/python

**New scripts.**
- RO/r2/r2_reconcile.py: whole-run server counters vs the wire.
- Round-1 scripts reused: RO/recompute.py, RO/gwsnap.py, RO/c4_*.py.

**Verdict legend.**
- CONFIRMED: shown by evidence and/or code.
- POSSIBLE: code-derived and not yet observed; a live check is given.
- RULED OUT: looked for and not found.

---

## M1. The gateway's /metrics cannot reproduce the client C4 or the SLO. The gateway-side proxy under-reads C4 p99 by 1–4.7 ms. CONFIRMED

**Code (RC2).**
- /metrics exports per-stage histograms (t_input, release_lag per piece, release_lag_max per stream, release_processing, t_finalize, t_total, provider_total).
- It has no per-request T_fw_addon or C4 series. The SLO unit is a per-request worst-chunk addon, and a p99 of a per-request max cannot be derived from stage histograms. This is the round-1 OBS-2 gap, still open.
- The histogram quantiles in /metrics are LIFETIME values: `prometheus(merge(workers))` → `summarize()` runs over cumulative, since-start buckets (runtime/metrics.py). Only `rv2_signal` is windowed. A latency regression after hours of uptime barely moves `rv_t_input_ns{stat="p99_ms"}`.

**Recomputed: gateway t_input p99 (window deltas) vs client C4 p99 (c4_client_all), same runs.** Source: RO/r2 reading of SP/evidence/r2-unit/runs/*/r2_summary.md.

| Run | Topology / rate | Gateway t_input p99 (ms) | Client C4 p99 (ms) | Gap (ms) |
|---|---|---|---|---|
| c38-1-200 | unit g2-24, RC1 @200 | 15.14 | 18.57 | −3.43 |
| c38-1-250 (the claimed knee) | @250 | 16.06 | 19.94 | −3.88 |
| c38-1-300 | @300 | 17.17 (reads as a pass) | 21.89 (FAIL; load-rule p99 20.32) | −4.72 |
| c10b-138 | split, RC1 | 10.16 | 11.17 | −1.01 |
| c10r2-b-150 | split, RC2 | 10.29 | 11.24 | −0.95 |

- At c38-1-300 the verdict flips if gateway data is used.
- This is a large improvement over round 1: the gap on frozen-1 was ≈ 8 ms at the knee, because loop lag p99.9 was 6–10 ms and is now ≤ 1.78 ms. It is still ≈ 20–25% of the budget at the all-in-one knee.

**Live check.** Lane: r2unit queue-a RC2 (c38r2-1-200 / -250).
1. At meas_start and meas_end, GET the node exporter /metrics.json (or the existing snap dumps).
2. Compute the windowed t_input p99 as a bucket delta, and the lifetime `rv_t_input_ns{stat="p99_ms"}` from /metrics.
3. Compare both with c4_client_all `--all` C4 p99 for the same window.
4. Also scrape /metrics twice, 5 min apart, at steady load. If the lifetime p99 moves by < 0.1 ms while the windowed p99 moves, the lifetime-quantile defect is settled.

Settles: a gap > 1 ms at the knee ⇒ v3 needs:
- a per-request addon histogram (max over chunks, holdback excluded) exported windowed;
- a black-box synthetic client + provider canary for production, which has no client clock.

## M2. Reason codes are lost or absent in metrics, and there are no per-event logs. CONFIRMED

**Code (RC2).**
- **Admission rejections are counted by status only.** `m.inc(f"rejected_{grant.status}")` (edge/chat.py:82): `rejected_503` merges `kill_switch_engaged` (an operator action) with `kill_switch_unavailable` / `shared_state_unavailable` (store outages); `rejected_401` / `403` / `429` likewise.
- **Plan outcomes are not counted at all.** `plan_unavailable` (503) and `plan_unknown_tenant` (403) return at chat.py:88–90 with NO counter.
- **FAIL_CLOSED looks like a policy block.** A block caused by an unavailable guard increments `disposition_BLOCK` (chat.py:114–115) and answers `403 blocked_by_policy`, exactly like a rule block. Only `guard_unavailable_findings` (per finding, mixed across FAIL_OPEN and FAIL_CLOSED tenants) and `sem:U` in x-rv-stages distinguish them.
- **FAIL_OPEN has no counter.** Requests served without the detector are counted as `disposition_ALLOW`.
- **Sheds do carry reasons.** Gateway: `shed{reason}` (admit/overload.py:57/98). Owner: `owner_shed_reason{reason}`.
- **Logs.** The gateway request path has no `_log` call for any 4xx/5xx or shed. The owner logs only CoDel state transitions (`guard_owner_overloaded` / `_cleared`, detect/guard/owner_queue.py:161/167). None of those appear in any captured round-2 log.

**Evidence: RC2 r2-state FLUSHALL run.** RAW/r2-state/runs/rc2-b-1 (verdict analysis/verdict.json; exporter gw/*/exporter_metrics.json).
- Client outcomes vs gateway counters:
  - `503:kill_switch_engaged` 9,241 + `503:shared_state_unavailable` 8 = **9,249**; `rejected_503` = 4,625 + 4,624 = **9,249**. The 8 store-outage 503s are indistinguishable from the kill switch.
  - `401` 9,232 = `rejected_401` 4,616 + 4,616.
  - `403 blocked_by_policy` 644 = `disposition_BLOCK` 288 + 356.
  - ALLOW 64,576 = provider calls.
- Gateway log for the whole run (RO/r2/gwlogs-rc2-b-1/logs/gateway.log, 464 lines):
  - 403 × `guard_deadline_changed` (≈ 64/min per gateway, noise);
  - 12 × `killswitch_refresh_error` ("ks manifest missing (store empty, flushed…)" — the only trace of the flush);
  - startup lines.
  - Nothing for the 8 × 503 or the audit loss (M3).
- RC1 runs (RO/r2/r2_reconcile_unit.json) reconcile exactly:
  - c10b-150: owner_shed 1,861 = client 503 1,861 = gateway `shed{reason="guard_owner_queue"}`.
  - c38-1-250: 57 budget sheds.
  - The owner reason (`queue_bound` / `budget`) exists only in the OWNER's metrics. The client body says `server_overloaded / overloaded` for both.

**Live check.** Lanes: r2state C36 gap variant on RC2, and r2chaos f3 (owner SIGKILL) + f5 (partition).
1. After each run, diff every client `err_detail` class against exporter counters.
2. `grep -c` the gateway/owner logs for each class.

Settles: CONFIRMED-for-v3 if any client class (`plan_unavailable`, `plan_unknown_tenant`, `shared_state_unavailable`, `kill_switch_unavailable`, FAIL_CLOSED 403) has no dedicated counter or log line.

v3 needs:
- `rejected{status,reason}`;
- plan outcome counters;
- `posture_block` / `posture_open` counters separate from `disposition_BLOCK`;
- a rate-limited structured event log per reason.

## M3. Audit loss is invisible: completeness reads 1.0 while 36% of records were erased, and sheds are never audited. CONFIRMED

**Evidence: rc2-b-1 (RC2, FLUSHALL).**
- verdict.json: `audit.admitted` 64,576, `tail_records` 65,220, `final_records` 41,630. **`admitted_missing_from_final_store` 23,369 (36%)** — records acknowledged by Valkey, then erased.
- Both gateways at the end of the same run: `audit_enqueued` = `audit_written` (65,082 / 64,714), `audit_dropped` 0, **`audit_completeness_ratio` 1.0 on every worker**.

**Code (RC2).**
- `completeness = written / produced` (audit/sink.py:75), where "written" = XADD acknowledged.
- There is no XLEN / last-id read-back and no durability check anywhere in rvproto (grep: no xlen/xinfo).
- MAXLEN trimming (`RV_AUDIT_STREAM_MAXLEN` 2,000,000, approximate), volatile-lru eviction, FLUSHALL and data-loss failover all erase records after they are counted as written.

**Unaudited sheds (whole-run counters).** `audit_enqueued` = 2×ALLOW + BLOCK exactly, so every 503 shed has NO record:
- c10b-150: 1,861 (3.3% of admitted)
- c38-1-250: 57
- round-1 fleet: up to 12.5%
- Cumulative per process at snap-post (round 1): 1.5–2.6%, with ratio 1.0.

**Live check.** Lane: r2state RC2, (b) FLUSHALL and (a2) Redis force-data-loss.
1. Before and after each fault, record:
   - each gateway's `audit_written` and `audit_completeness_ratio` (exporter /metrics.json);
   - the stream lengths (read-only `XLEN rv:audit:<org>` on the lane's own set, or its audit export count).
2. Also join the client's 503 rids against exported audit records (expect 0).

Settles: ratio 1.0 while XLEN drops ⇒ an operator cannot detect audit loss.

v3 needs a durable sink with a read-back high-water mark (acked id vs durable id), a `records_lost` counter, and audit records for sheds and admission rejects.

## M4. Autoscaling signals: the lag is mostly downstream of the exporter, the group mean hides a hot instance, and gpu_utilization is noisy and in a different unit. CONFIRMED (lag, mean, GPU) / POSSIBLE (counter-reset artefacts, GKE chain)

**Evidence: r2-mig dry-as-1** (RC1, not a measured run; the signal code is unchanged in RC2).
Files: RAW/r2-mig/runs/dry-as-1/platform/inst/*.log (per-instance /signals polls every 5 s) and SP/evidence/r2-mig/runs/dry-as-1/platform_timeline.json.

- **Exporter windowing.** RV_EXPORT_S = 10 s (a trailing window mean). The step was 20 → 40 RPS at 09:39:12.55Z.
  - Gateway `requests_per_s` 26.17 (> target 25) at 09:39:18.89Z (+6.3 s).
  - Full 40.01 at +16.4 s.
  - Guard `owner_requests_per_s` 22.55 at +4.6 s, 39.90 at +14.7 s.
- **Lag split.** The autoscaler decision was at 09:40:05.16Z (+52.6 s). So ≈ 46 s of the 53 s lies in Cloud Monitoring ingestion plus the autoscaler's evaluation, not in the exporter window. Shortening RV_EXPORT_S buys ≤ 10 s.
- **The mean hides a hot instance.** After scale-out:
  - The new gateway (rv-r2mig-gw-8gpw) reported 0.0–0.2 req/s and the old one 39.8–40.0, so the group mean = 20 = 80% of target and the autoscaler is "satisfied".
  - Meanwhile the old gateway ran at 160% of target.
  - Clients saw 503s in every step minute (4–12/min, 0.17–0.50%; minutes.csv), including all 5 full minutes after the new gateway was healthy (09:41–09:45Z: 10/7/4/12/6).
  - The per-instance signal is correct; the utilisation-target mean hides the imbalance (L4 connection pinning).
- **gpu_utilization.** It is a FRACTION (0–1; exporter.py:340 divides by 100) while nvidia-smi, dmon and the lanes' tables use percent. It is one instantaneous nvidia-smi sample per window, not a window mean.
  - At steady 40 req/s (39 distinct windows): mean 0.119, CV 0.20.
  - `owner_requests_per_s` over the same windows: CV 0.0022, i.e. 90× steadier.
  - The r2-mig table rounds the signal to 1 decimal ("0.1"), which hides the value.
- **Counter resets (POSSIBLE).**
  - Owner counters are per process, and the launcher restarts a dead owner in place (serve.py:139–146). The exporter's previous sample is keyed by segment NAME (exporter.py:351–356).
  - ⇒ After an owner restart, one window of `owner_requests_per_s` / `owner_shed_per_s` / `owner_queue_ms_avg` is NEGATIVE: new cumulative count minus the old process's count.
  - After a node or exporter restart, `requests_per_s` = 0.0 for the first window (prev None, exporter.py:331), and owner signals are omitted.
  - None was observed: no evidence run captured signals across an owner restart (searched RAW/r2-chaos, r2-mig, r2-fleet: 0 negative values).
- **GKE chain (POSSIBLE, unmeasured).**
  - The chain: exporter window 10 s + PodMonitoring interval 5 s (20-sut.rendered.yaml:148) + GMP ingestion + stackdriver adapter + HPA sync (default 15 s).
  - RC2's exposition is UNTYPED (no `# TYPE` lines; metrics.py docstring). GMP ingests untyped series as `prometheus.googleapis.com/<name>/unknown` (+ `unknown:counter`).
  - The HPA template asks for `prometheus.googleapis.com|rv2_signal|gauge` (30-hpa.yaml.tmpl). If the untyped ingestion rule applies, the HPA finds no metric.
  - No RC2 HPA status exists in evidence yet (the RC1 scrape was rejected wholesale).

**Live checks.**
- **(a) r2mig S1/S2/E on RC2** (RV_CONN_MAX_AGE_S=30):
  - poll each instance's /signals every 5 s;
  - `timeSeries.list` raw points (ALIGN_NONE) for rv2/gateway/requests_per_s and rv2/guard/owner_requests_per_s;
  - the autoscaler status every 5 s.
  - Report the exporter→Monitoring visibility delay, the Monitoring→decision delay, and per-instance max/target while the group mean < target.
- **(b) r2chaos f3 (owner SIGKILL) and f4 (gateway SIGKILL):** poll /signals on the node every 5 s. One negative or zero window settles the counter-reset item.
- **(c) r2gke, before its step tests:**
  - `gcloud monitoring metrics-descriptors list --filter='metric.type=starts_with("prometheus.googleapis.com/rv2_signal")'` (read-only);
  - `kubectl get hpa rv-gateway -o json` → `status.conditions` / `currentMetrics`.
  - Only `/unknown` present, or `FailedGetPodsMetric`, settles the type mismatch.
  - During the step, poll the HPA JSON every 2 s with pod /signals for the lag split.

## M5. Label values are not escaped: one tenant id can make every gateway's scrape invalid (GKE HPA blind fleet-wide). CONFIRMED (code; independently r2-fix-c36m)

**Code (RC2).**
- Raw f-strings insert tenant- and operator-controlled strings as Prometheus label values:
  - `quota_admitted_tokens{org="{org}"}`, `lease_granted_tokens{org=…}`, `quota_rejected{org=…}` (admit/quota.py:50/66/70/73);
  - `plan_version_info{org="…",version="…"}`, `killswitch_engaged{scope="org"|"model",key="…"}`, `lease_held_tokens{org=…}` (edge/ops.py:54–56/70).
- No id validation exists in rvproto/control (no regex / fullmatch).
- **Blast radius:** every gateway exports every org's `plan_version_info` and `killswitch_engaged` gauges. One org id, plan version or model name containing `"`, `\` or a newline makes EVERY gateway's /metrics invalid. GMP then drops the whole scrape and the HPA loses `rv2_signal` everywhere.
- The MIG path is unaffected (the JSON push to Cloud Monitoring).
- Cardinality: per-org series × workers (round-1 E16: 50k tenants → 50,003 series per worker).
- Independent verification: SP/evidence/r2-fix-c36m/rc2-verify/VERDICT.json `item5_valid_metrics.gaps`. prometheus_client 0.21.1 even accepts RC1's `}{stat` line, so parse-only tests are insufficient.

**Live check** (no shared-store mutation). On a throwaway store, e.g. the r2-impl dev VM when RC3 needs one:
1. Seed one org `x"y`.
2. `curl :9464/metrics | promtool check metrics`.
3. On GKE, read GMP target status (`up`) for that pod.

Settles: an invalid scrape ⇒ RC3 must escape label values (Prometheus rules: `\\`, `\"`, `\n`) and validate ids at the writer.

## M6. Honest writer: the CLI statuses mostly hold, and the operator UI surfaces none of them. CONFIRMED (evidence)

- **What holds.** RC2 statuses ok / ok_publish_pending / unknown / error hold under faults: 0 false failures, CLI status = exit code 720/720 (VERDICT.json `item4_honest_writer`).
- **Gaps that remain:**
  - `budget-set` reports "ok" when its counter write failed (false SUCCESS).
  - A lost COMMIT on the pre-write read prints no status (exit 3) and leaves a stale library status.
  - `seed` prints no status.
  - "unknown" is frequent under connection loss: 90 in 60 s, 86 of them rolled back, each needing a manual rv2_log check.
  - USAGE wrongly says `verify` resolves unknown writes.
- **UI.** No v2 operation has an HTTP API (r2console GW14b: 2 full / 10 partial / 13 none), so the console cannot show any writer status.

**Live check.** Lane: r2state (e) Cloud SQL failover on RC2 with the CLI.
1. Tally statuses vs rv2_log rows.
2. Force a budget-set during a Valkey outage and check "ok" vs "ok_publish_pending".

## M7. Verdict metrics that could silently read zero. Mostly RULED OUT; two product-side items POSSIBLE

- **RULED OUT: r2unit C38 loop-lag claim.** All 11 claim runs (c38-1-150 … c38r2-1-200-r1, c10b-138, c10r2-b-150) have complete worker snapshots:
  - 18/18 or 12/12 workers at both meas_start and meas_end;
  - 0 PID changes;
  - every window 299.8–300.3 s;
  - loop_lag n > 0 for every worker.
  - No worker silently missing from the merged histogram (RO/r2 check, command in the transcript).
- **RULED OUT: controller idle watchdog** (tools/idle_vm_watch.py).
  - A missing CPU series is skipped (`if c is None: continue`), not treated as idle; VMs started < 30 min ago are skipped.
  - The real defect was "idle ≠ unused" (re-hydrator VM stopped at 1.1% CPU), fixed by idle_exempt.txt.
- **RULED OUT (harmless): 0-byte captures in r2fleet.** Edge `exporter_metrics.post.json` and guard `metrics_all.post.json` (endpoints absent on those roles) are not verdict inputs; round-1 reconciliation over all 44 fleet runs was exact.
- **LOW: r2-mig tooling.** platform_timeline.py hard-codes `gw_active_streams_sum = None` (line 166); the signal itself exists in the instance logs.
- **POSSIBLE: shm loss on VM deployments (RemoveIPC) blinds the exporter while /readyz stays 200.**
  - ops.py `node_readiness()` returns None when `/dev/shm/rv2-node-<tag>.json` is missing, and readiness = own-worker readiness (`node is None ⇒ ready`).
  - The exporter's `refresh()` then finds no segments: `requests_per_s` and owner signals are omitted, `cpu_utilization` is pushed as 0.0, and /metrics carries only `rv2_signal`.
  - The node file is rewritten only on a readiness state change.
  - Lanes are protected by B5 preflight; the product has no shm-loss self-check until RC3.
  - Live check (r2chaos, own VMs): delete `rv2m-*` + `rv2-node-*` on one gateway, then read /readyz, /signals and Cloud Monitoring. Expected on RC2: /readyz 200, requests_per_s absent, cpu_utilization 0.
- **POSSIBLE: first-window zero.** `requests_per_s` = 0.0 after any node/exporter restart, and `gpu_utilization` is omitted when nvidia-smi fails. Neither is a round-2 verdict input; both could mislead the MIG autoscaler during recovery (check folded into M4 (b)).

---

## Top 5 by impact on v3

1. **M1: no per-request C4/SLO metric, and lifetime (not windowed) quantiles.**
   - The gateway proxy under-reads the client C4 p99 by 0.95–4.7 ms and flips the verdict at 300 RPS (17.17 vs 21.89 ms).
   - v3 must specify:
     - a windowed per-request addon histogram (holdback excluded, C4 definition);
     - socket-level arrival timestamps;
     - a production synthetic canary, the only client clock available in production.
2. **M3: audit durability invisible.**
   - Completeness 1.0 while 36% of records were erased (rc2-b-1).
   - Sheds and admission rejects never audited (up to 3.3% in the unit, 12.5% in the fleet).
   - v3 must specify:
     - a durable audit sink with an acked-vs-durable high-water mark;
     - a records_lost counter;
     - records for every 4xx/5xx outcome.
3. **M4: autoscaler signal semantics.**
   - The per-instance utilisation mean reads "satisfied" (80%) while one pinned gateway runs at 160% and sheds.
   - ≈ 46 of the 53 s decision time is Monitoring + autoscaler, not the exporter.
   - gpu_utilization is a noisy instantaneous fraction.
   - Counter resets can produce negative or zero windows.
   - On GKE the untyped exposition may not match the HPA's `|gauge` name.
   - v3 must specify:
     - max- or percentile-based per-instance signals (or connection recycling + per-instance alerts);
     - windowed GPU busy from owner exec-time counters;
     - reset-safe rate computation;
     - `# TYPE` lines.
4. **M2: reason codes.**
   - `rejected_503` merges an operator kill switch with store outages.
   - `plan_unavailable` / `plan_unknown_tenant` are uncounted.
   - FAIL_CLOSED is counted as a policy BLOCK; FAIL_OPEN is uncounted.
   - No per-event logs.
   - An operator cannot tell an incident from policy. v3: `rejected{status,reason}`, posture counters, a rate-limited event log.
5. **M5 + M7 (shm): one bad label value or a lost /dev/shm silently blinds monitoring fleet-wide while readiness stays green.**
   - v3 must specify:
     - label escaping plus id validation at the writer;
     - the node-readiness check must fail closed when the node file is missing;
     - an shm-loss self-check counter (RC3 items, now with the blast radius stated).

(M6, the honest writer, is operator-UX: the CLI is mostly right and there is no UI surface. It is tracked in the RC3 list.)
