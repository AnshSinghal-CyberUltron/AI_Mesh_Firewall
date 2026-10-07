# Round 2 spec — prove the fixes, then test the whole system end to end (autoscaling, managed stores, chaos, console)

SP = /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
Everything in SP/GCP.md still applies (project `ai-mesh-firewall` ONLY — never touch `aisecshield-prod`; VM naming,
labels, SSH key SP/gcp/rv_ed25519 via instance metadata, per-lane vm-ledger, delete what you create, measurement hygiene).
Round-2 lanes use the prefix `r2-` (VM names `rv-r2<lane>-<role>-<n>`, label `lane=r2<lane>`), evidence in
SP/evidence/r2-<lane>/, large raw data in /home/contact_cyberultron_com/rv-evidence-raw/r2-<lane>/. Security hygiene
(secret rotation) is out of scope; never print tokens/keys.

## Owner decisions (2026-09-24)
- Managed stores: Cloud SQL for PostgreSQL (HA, private IP) + Memorystore (HA; Valkey if available in asia-south1, else Redis Standard).
- System: the v2 prototype (rvproto) WITH the four fixes, plus a minimal real control-plane path (config write → Cloud SQL → plan push via Memorystore → gateways).
- Autoscaling: test BOTH (a) regional Managed Instance Groups behind a regional external Application Load Balancer and (b) GKE (HPA for gateways, GPU node pool with cluster autoscaler for guards) — compare, choose the best.
- Autoscaling pass bar: 3× load step; time-to-capacity measured; C4 p99 < 20 ms once scale-out completes; infra errors ≤ 0.1% throughout; zero dropped streams on scale-in (connection draining).
- Failures: FULL chaos under load — Memorystore failover, Cloud SQL failover, zone loss, guard VM/node kill, LB backend drain mid-stream, gateway↔store network partition, provider outage.
- Frontend: FULL console regression (every page/workflow; Chromium, Firefox, WebKit; desktop/tablet/mobile) — v1 baseline, then the v2 data path where supported (gaps become the GW14b backlog).
- Region: asia-south1 first; on L4 stock-out fall back to another region and label results.
- Budget: no cap (still delete everything as soon as a lane no longer needs it).
- Signed product decisions for v3: streaming holdback bound ≤ 3 upstream tokens per stream (reported separately from the 20 ms processing SLO); internet egress reported separately (outside the $5k envelope); final plan = new v3 runbook.

## Phase A — the four fixes (implement behind flags in a new prototype version `rvproto-2`; prove each on GCP)
| Fix | Pass criterion (3 repeats at the claim, negative control on the unfixed build) |
|---|---|
| C38 loop isolation (no periodic metrics dump on serving loops; tokenizer `encode_batch` in a thread pool with TOKENIZERS_PARALLELISM=false; metrics exposition off-loop) | one g2-standard-24 all-in-one: C4 p99 < 20 ms at ≥ 200 RPS (3/3); CPU-burst injection (e.g. 50 ms burst in one worker every second) keeps OTHER workers' streams' C4 p99 < 20 ms |
| C10 owner-level admission (remove the GW03 per-worker guard cap; separate knobs for owner queue bound, input queue and guard deadline; owner SHED → 503 + Retry-After; guard deadline derived from the budget, not equal to the SLO) | Poisson arrivals at 90% of the constant-arrival C4 knee: infra ≤ 0.1%; 3× overload: sheds happen at the owner with 503 + Retry-After, 0 FAIL_OPEN, admitted-request p99 bounded; 1→2→4 units efficiency ≥ 0.95 |
| C43 load-aware guard routing (route each guard call to the owner with the least outstanding work using shared/owner-reported state, not per-worker round-robin) | 3 gateways + 4 guards: owner queue-wait / M/G/1 ratio falls well below 0.65–0.71 (tools: SP/evidence/proto-bench-split/tools/poisson_check.py) and the per-guard C4 knee rises above 130 RPS (target ≥ 170), 3/3 |
| C36 durable state (Cloud SQL = source of truth for plans, keys/epochs, kill switches; explicit OFF records; versions = (epoch, seq) + content hash, monotonic; missing store data ⇒ PLAN_UNAVAILABLE 503 / fail-closed, never "unknown tenant"/401; re-hydrator restores Memorystore from Cloud SQL) | under load on managed Memorystore + Cloud SQL: (a) Memorystore failover and (b) FLUSHALL with an engaged kill switch and a revoked key → 0 requests admitted for the killed org, 0 accepted with the revoked key, plans re-hydrated within a declared bound (measure it), 503 (not 401/403) during the gap; negative control reproduces the v2 defects |

## Measurement rules (unchanged from round 1)
READY harness only (synthprov 1ee6bb5f, olg d22d2607, analyze.py 3874eaac; SP/harness/USAGE.md; run deploy/prep_host.sh on loadgens/providers).
HEADLINE corpus unless stated; TTFT 150 ms, ITL 20 ms, 70/30 SSE/JSON; 30 s ramp + 60 s warm-up + 300 s measurement; open-loop
constant arrivals unless stated (Poisson where required, ×1.5 loadgen headroom); `-sample-mod 1` on olg AND synthprov.
PASS = C4 p99 < 20 ms over all offered requests (infra = +inf; policy blocks a separate stratum) AND infra ≤ 0.1% AND 0 drops.
C4 = SP/evidence/proto-bench-fleet/scripts/c4_client_all.py <raw run dir> --all (holdback excluded; report strict and load-rule too).
Report per run: qualified RPS, C4 p99 (SSE/JSON), infra %, CPU-ms/request, GPU %, loop lag p99/p99.9, owner queue p99.

## Evidence
Every claim cites a raw file. Keep raw per-request data; recompute summaries from raw with a script that ships with the evidence.
Final message to the controller: table of runs + pass/fail per criterion + evidence paths + VM ledger confirmation (list empty).

## Lane map (controller, 2026-09-24 ~05:30Z)
| Lane (label) | Agent | Job | Store set | L4 cap |
|---|---|---|---|---|
| r2-impl | proto-builder | build rvproto-2 (4 fixes behind flags, attribution records, test fault knobs, failure semantics, autoscaling signals), images in AR `rv-r2`, USAGE.md | dev only | — |
| r2infra | r2-infra | networking + `shared` store set (rv-r2-pg Cloud SQL PG16 HA + rv-r2-valkey Valkey 8.0 HA), drills, RTT baselines, READY; TLS+IAM Valkey latency smoke | — | 0 |
| r2unit | proto-bench-unit | C38 (g2-24 all-in-one ≥ 200 RPS ×3, burst injection, negative control) + C10 (Poisson @90% knee on g2-24 and split unit, 3× overload, knob separation) | `shared` (if needed) | 3 |
| r2fleet | proto-bench-fleet | C43 (3 gw + 4 guards, M/G/1 ratio, per-guard knee ≥ 170) + C10 scaling 1→2→4 split units (eff ≥ 0.95) | `shared` | 4 |
| r2state | r2-state | C36 (Memorystore failover + FLUSHALL with kill switch + revoked key, re-hydration bound, monotonic versions, Cloud SQL failover during writes) | `state` = own rv-r2-state-pg / -kv (Valkey) / -redis (Redis STANDARD_HA for force-data-loss failover) | 1 |
| r2mig | r2-mig | MIG + regional external ALB platform; steady state + 3× step autoscaling ×3 | `shared` | 2 → 6 after PROVEN |
| r2gke | r2-gke | GKE (HPA + GPU pool CA) + Gateway API regional L7; same protocol | `shared` | 2 → 6 after PROVEN |
| r2chaos | r2-chaos | Part 1 fixed-fleet faults (store failovers, guard/gateway kill, partition, provider outage); Part 2 zone loss + LB drain on the chosen platform | `chaos` = own rv-r2-chaos-pg / -kv / -redis (Part 1), `shared` (Part 2) | 3 |
| r2console | frontend-verifier | full console regression (3 browsers × 3 viewports) on a v1 stack deployed on GCP; v2 writer coverage → GW14b backlog | own DB; `shared` for v2 path | ≤ 1 |

GATE: measured platform (r2mig/r2gke) and chaos runs start only after the controller names the PROVEN build digest (all four fix proofs PASS on the same digest).
Region: asia-south1 first (16 L4 quota); fall back to asia-northeast1 / asia-southeast1 (16 each) on stock-out and label results.

## Shared-store tenancy (controller, 2026-09-24 06:15Z) — `shared` set = rv-r2-valkey + rv-r2-pg
| Lane | Valkey DB (redis://10.61.0.3:6379/<db>) | Cloud SQL database | RV_NAMESPACE |
|---|---|---|---|
| (unused) | 0 | — | — |
| r2mig | 1 | rv2_r2mig | r2mig |
| r2gke | 2 | rv2_r2gke | r2gke |
| r2fleet | 3 | rv2_r2fleet | r2fleet |
| r2unit (only if it runs in Mumbai) | 4 | rv2_r2unit | r2unit |
| r2console | 5 | rv2_r2console | r2console |
Rules: RV_NAMESPACE prefixes EVERY key and pub/sub channel (pub/sub is global across DB indexes); guard discovery only within the namespace (verify owner IPs before the first run); export audit streams (XRANGE → file) after each run then UNLINK them (2.89 KB/record; volatile-lru never evicts streams; maxmemory 10.4 GiB); never FLUSHALL/FLUSHDB on the shared set.
