# rvproto-2 — how to run it

rvproto-2 is rvproto-frozen-1 plus the loop-isolation knobs (li, tree `8ace2294…`), with four fixes.
Each fix sits behind its own flag, **default ON**. Setting a flag to `off` restores the frozen-1 behaviour of
that area, so every fix has a negative control on the same build.

**Release candidates.** Deploy an RC by manifest, never the live tree (`SP/rvproto2` keeps changing). Record the RC manifest sha256 in every run summary (bulletin B17).

| RC | Manifest sha256 | Tarball | Images | Status |
|---|---|---|---|---|
| RC2 | `6093c8dd10bd12f5d5058a963ac9db3ed9768985c4907d73d3a5272c01cff0fc` (171 files) | `SP/evidence/r2-impl/rc2/rvproto2-rc2.tar.gz`, sha256 `eb9d4da2b0d9b4cc4a281b865e43c27edae011a4704da4430027fdb2ee0593a9` | gateway@`c4c63287b4eea4255312627143a8b595424f26d85cee6fed1c2b30ac1e77b5e2`, guard@`a7e6165a33bcfd3f474dba7f663a43419de89eb3f286a31bbe9b845d6c6cccd3` (tag `rvproto2-rc2`) | functional PASS with the limits in `functional/rc2/SUMMARY` |
| RC1 | `002598514a2bd4b494ad0df8e7e4ffaac589d79a26aa139ebd4fcf78280a25a0` (163 files) | `SP/evidence/r2-impl/rc1/rvproto2-rc1.tar.gz`, sha256 `7b669712…c7f5` | gateway@`69f56e8d…79b4`, guard@`2f2c292c…e4e7` | frozen by the controller (B27); functional PASS (no serving-code-path failure) |

- **What changed from RC1 to RC2**, and which proofs must be repeated: `SP/rvproto2/VERSION` lists the changed code paths.
- **Tarball layout:** the tree sits at the root, plus `VERSION`. Unpack it to `~/rv2/rvproto`.
- **vendor:** the vendored `gateway_v2` runtime (`RV_GATEWAY_V2_PATH`) is not in the RC tarballs. Take it from `devvm/rvproto2-dev.tar.gz` (`vendor/`); it has not changed since RC1.
- **Images:** pin by digest; full references and measurements are in `SP/evidence/r2-impl/IMAGES`.
  - `gateway`: 481 MB (110 MB compressed).
  - `guard`: 13.8 GB (5.9 GB compressed; nearly all of it is the pip CUDA 13 / cuDNN / TensorRT libraries).
- **Store and database connection details:** `SP/evidence/r2-infra/READY`.
- **VMs:** run rvproto-2 as a login user only with a logind drop-in `RemoveIPC=no` (bulletin B5). Otherwise logind deletes the `/dev/shm` metric segments at your last logout, and `/readyz` flaps. Containers are not affected.

| Flag | Fix | What it does | `off` restores |
|---|---|---|---|
| `RV_FIX_C38` | Loop isolation | Every process keeps its metrics in `/dev/shm` segments. An exporter thread in the launcher or guard supervisor renders dumps, Prometheus text and autoscaling signals, so workers never serialise metrics. PG2 tokenisation runs in a per-worker thread pool (`encode_batch`, which releases the GIL; `TOKENIZERS_PARALLELISM=false`). GC is `gc.freeze()`-ed after startup, with raised thresholds. `gc_pause_ns{gen}` is measured whether the flag is on or off. | Per-worker metrics dumps on the event loop; tokeniser on the loop |
| `RV_FIX_C10` | Owner-level admission | The owner sheds (503 + `Retry-After`) only on a standing queue (RC2 default: `RV_OWNER_ADMISSION=codel`, a CoDel-style detector) or above a hard cap of 60 ms of work, so ordinary bursts become a few late requests, not sheds. Admitted guard work is answered late, never abandoned: an abandoned guard call would become FAIL_OPEN or a false 403. `RV_OWNER_ADMISSION=bound` restores RC1's rule: a 12 ms instantaneous backlog bound plus a per-request budget shed. The per-worker guard share becomes advisory. Owner queue, input queue, guard deadline and store timeout are separate knobs, not derived from `AMF_TARGET_P99_MS`. | GW03 per-worker caps, and the owner cap, both derived from `AMF_TARGET_P99_MS` |
| `RV_FIX_C43` | Load-aware routing | Workers subscribe to every owner's backlog, which the owner pushes on each queue change. Each guard call goes to the owner with the least estimated backlog. A draining owner answers `REDIRECT`, and the worker re-sends to another owner with the same deadline. Owner queue wait / M/G/1 was 0.03 vs 0.34 round-robin in the functional test. | Per-worker round-robin |
| `RV_FIX_C36` | Durable state | Postgres (Cloud SQL) is the source of truth. The store holds versioned, HMAC-signed records plus a manifest per kind. A missing, forged, partial or regressed store answers 503 (never 401/403 for a valid key, never "switch off"). A re-hydrator restores the store from Postgres. | Frozen `rv:*` layout: a flushed store un-kills orgs and returns 401 for valid keys |

## 1. Processes

The entrypoint is the same in the images and on a VM with the tree at `~/rv2/rvproto`: `python -m rvproto.serve …`.
In the images use `docker run IMAGE <mode>`:

| Mode | What runs | Ports |
|---|---|---|
| `gateway` (gateway image) | Launcher + `WEB_CONCURRENCY` (or contract-derived) HTTP workers (`SO_REUSEPORT`), plus the node exporter thread | `RV_PORT` 8400; exporter `RV_EXPORTER_PORT` 9464 |
| `guard` (guard image) | Guard node: one owner per visible GPU on `tcp://<ip>:7070+i`; registers itself in the store; exporter thread (GPU util etc.) | 7070+i; 9464 |
| `unit` (guard image) | All-in-one: owners + workers on one GPU host (`RV_GUARD_TOPOLOGY=owner`) | 8400; 9464 |
| `control …` (either image) | Control-plane writer / CLI (C36): Postgres first, then the store | — |
| `rehydrator` (either image) | The C36 re-hydrator (loop) | — |
| `prebuild` (guard image) | Builds or verifies the TensorRT engine cache for the visible GPUs, then exits | — |

### Minimal split deployment against the r2 stores

```bash
# control plane, once: schema + seed (the test keys sk-rv-org-{a,b,q}-0001 = the READY harness keys)
docker run --rm --network host -e RV_DATABASE_URL -e RV_REDIS_URL -e RV_STATE_HMAC_KEY $GW control init-db
docker run --rm --network host -e RV_DATABASE_URL -e RV_REDIS_URL -e RV_STATE_HMAC_KEY $GW control seed
# re-hydrator (one or more; each check is idempotent)
docker run -d --network host -e RV_DATABASE_URL -e RV_REDIS_URL -e RV_STATE_HMAC_KEY $GW rehydrator
# guard node (GPU host with driver >= 580 and nvidia-container-toolkit)
docker run -d --gpus all --network host --shm-size 1g -e RV_REDIS_URL $GUARD guard
# gateway node(s); owners are discovered through the store
docker run -d --network host --shm-size 512m --ulimit nofile=1048576:1048576 \
  -e RV_REDIS_URL -e RV_STATE_HMAC_KEY -e RV_PROVIDER_URL=http://<provider>:8080 $GW gateway
```

Where:

- `RV_REDIS_URL=redis://10.61.0.3:6379/0` (Valkey primary).
- `RV_DATABASE_URL=postgresql://amf_app:<pw>@10.60.0.2:5432/amf?sslmode=require`. The password is in Secret Manager `rv-r2-pg-app-password`.
- `RV_STATE_HMAC_KEY` is the record-signing key shared by the control plane and the gateways. Generate it once, e.g. `head -c 32 /dev/urandom | base64`, and keep it in Secret Manager. It is never printed.

Other notes:

- `AMF_TARGET_P99_MS` only drives the GW03 worker count, lease and audit bounds.
- Every guard bound comes from the knobs in §2.
- Guards need no Postgres, and no HMAC key.

### On a VM without containers

This is how the dev VM ran:

- `source ~/rv2/env.sh` (template in `evidence/r2-impl/devvm/env.sh`), then `python -m rvproto.serve [--guard-node]`.
- `python -m rvproto.control …`.
- GPU hosts need `deploy/gpuenv.sh` with `RV=~/rv2`, which puts the pip CUDA/TensorRT libraries on the loader path.

### Managed instance groups

Put `deploy/images/mig-startup.sh` in the instance template's `startup-script` and `mig-shutdown.sh` in its `shutdown-script`.

Metadata:

- `rv-role`: `gateway` | `guard`.
- `rv-image`: the digest reference.
- `rv-env`: `KEY=VALUE` lines.
- `rv-hmac-secret` (optional): the Secret Manager version name. Needs `roles/secretmanager.secretAccessor` on the instance SA.

Validated on a real GCE VM (rv-r2impl-dev-1, `rv-proto-unit` family) with instance metadata, for both roles; COS was not exercised:

- **Guard:** startup script to owner registered in 19.3 s (image already local). The shutdown script drained and exited 0 in 2.4 s, and the owner deregistered.
- **Gateway, with `rv-hmac-secret`:** the secret is fetched with the instance token and written to `/run/rv2.env` (mode 600). Ready in 4.7 s with a guard already running, then a request returned 200 through the remote owner. The shutdown script drained and exited 0 in 2.7 s.
- **Bug found by this test:** the first version parsed the API's pretty-printed JSON wrongly and exited 1. Every gateway with `rv-hmac-secret` would have failed to start.
- **Mixed-key record sets fail closed.** Records published under one HMAC key can't be verified with another. The kill switch goes stale, so the gateway stays not ready. Use one `RV_STATE_HMAC_KEY` for the whole deployment.

Host images and tags:

- Gateways: `cos-stable`.
- Guards: the `rv-proto-unit` image family, which has driver 580, docker and the nvidia runtime.
- Both need tag `rv-r2-lb-backend` when behind the ALB (READY §2).

Guard boot cost (measured on the dev VM, g2-standard-8, asia-south1-b):

- Cold `docker pull` of the guard image from Artifact Registry: **107 s**.
- Container start to owner ready with the built-in sm_89 engine: ~15–19 s (TensorRT session 9 s + warmup).
- To take the pull out of guard scale-out, pre-pull the image into the guard VM's boot image (a custom image made from the `rv-proto-unit` family), or use image streaming on GKE.
- A cold pull of the gateway image is a few seconds (3.7 s when the base layers were already present).

Shutdown:

- `docker stop -t 85` sends SIGTERM, which starts the drain (§4). In both images `tini` forwards SIGTERM to the launcher or guard supervisor only (no `-g`), so the launcher runs the drain sequence itself: workers first turn not-ready (`/readyz` 503), and only later stop accepting. With `-g` every worker got SIGTERM at once and skipped the accept window.
- Keep `RV_DRAIN_ACCEPT_S + RV_DRAIN_S` ≤ ~80 s so the drain fits the GCE shutdown window.
- Set the backend service's connection draining ≥ `RV_DRAIN_ACCEPT_S`.

### GKE

Use the same images. Things to set:

- Gateway `Deployment`: `terminationGracePeriodSeconds: 90`. SIGTERM does the drain itself, so no preStop hook is needed.
- `readinessProbe` on `/readyz` (it turns 503 immediately on SIGTERM and while any worker is not ready).
- Guard pods: `RV_GUARD_ADVERTISE=$(POD_IP):7070` via the downward API.
- Driver: the GPU node pool needs a driver ≥ 580 (CUDA 13), e.g. `gpu-driver-version=latest`, which is verified on the node.
- `RV_MONITORING_RESOURCE=k8s_pod`, plus `RV_K8S_{PROJECT,LOCATION,CLUSTER,NAMESPACE,POD}`, for custom-metric HPA through the Stackdriver adapter.

## 2. Knobs (env)

Every value is logged at startup in the `launcher` / `startup` event (settings→`v2`). Secrets show as `<set>`.

### C38

| Knob | Default | Meaning |
|---|---|---|
| `RV_FIX_C38` | on | See table above |
| `RV_TOKENIZE_IN_THREAD` / `RV_TOKENIZE_THREADS` | follows C38 / 2 | Tokeniser thread pool per worker (explicit setting wins) |
| `RV_GC_FREEZE` / `RV_GC_THRESHOLD` | on / `50000,50,100` | `gc.freeze()` after startup; `gc.set_threshold` |
| `RV_EXPORTER_PORT` / `RV_EXPORTER_HOST` | 9464 / 0.0.0.0 | Node exporter HTTP (`0` = off). Serves `/metrics`, `/metrics.json`, `/signals`, `/dump` |
| `RV_METRICS_DIR` / `RV_METRICS_DUMP_S` | (images: `/dev/shm/rv2-metrics`) / 1 | Frozen-1-format dump files written by the exporter. SIGUSR1 to any worker/owner (or the launcher) = a fresh dump within ~50 ms; the process only stamps one word, and harness samplers work unchanged |
| `RV_SHM_SLOTS` | `96,512,512` | Histogram / counter / gauge slots per process (overflow degrades to process-local and is counted) |
| `RV_INJECT_CPU_BURST` | — | Test fault: `<worker>:<ms>:<period_s>` busy-loops that worker's event loop |

### C10

| Knob | Default | Meaning |
|---|---|---|
| `RV_FIX_C10` | on | |
| `RV_OWNER_ADMISSION` | codel | `codel` (RC2): shed only on a standing queue. `bound`: RC1's rule. Set it identically on gateways and guards |
| `RV_OWNER_TARGET_MS` / `RV_OWNER_INTERVAL_MS` | 5 / 100 | codel. The owner enters "overloaded" once every request that started over one interval waited at least the target (the minimum sojourn over the interval stayed above it). While overloaded it admits a request only when the backlog is under the target. It leaves that state after a full interval with no shed |
| `RV_OWNER_QUEUE_MS` | 60 (codel) / 12 (bound) | Hard backlog cap, in ms of work at the owner's measured rate. A request arriving into an empty queue always runs |
| `RV_OWNER_BUDGET_SHED` | off (codel) / on (bound) | Also shed a request whose predicted wait plus own execution exceeds its budget |
| `RV_GUARD_WAIT_MS` | max(100, cap + 40) | codel without budget shed: the budget a guard call carries, which also bounds how long the worker waits. It must be ≥ the cap, so admitted work is never abandoned |
| `RV_SLO_MS` / `RV_GUARD_DEADLINE_MARGIN_MS` / `RV_GUARD_DEADLINE_MIN_MS` / `RV_GUARD_DEADLINE_MAX_MS` / `RV_GUARD_DEADLINE_WINDOW_S` | 20 / 2 / 4 / = SLO / 10 | Derived deadline = clamp(SLO − p99(non-guard input work over the last window) − margin, MIN, MAX). It is the guard budget in bound mode, or with the budget shed on. Gauge `guard_deadline_ms`; each change is logged as `guard_deadline_changed` (from, to, clamped, non-guard p99) |
| `RV_GUARD_DEADLINE_MS` | — | Pins the guard budget (operator override, both modes) |
| `RV_INPUT_QUEUE_CAP` | none | Per-worker input-phase bound (gauge −1 = unbounded) |
| `RV_STORE_TIMEOUT_MS` | 25 | Request-path store op / connect / pool-wait timeout |
| `RV_INJECT_OWNER_DELAY_MS` | 0 | Test fault: slower engine (sleep per inference call) |

**Worked example of codel admission.** On 2× sustained overload the owner detects the standing queue after one interval. Admitted waits are bounded by the hard cap (60 ms) during that onset. After the onset, admitted waits stay ≤ target + one request's execution, and the excess is shed with `Retry-After` = backlog drain time. At ρ 0.45 with Poisson bursts it sheds nothing. On the same arrivals, RC1's bound sheds ordinary bursts. These three behaviours are unit and property tested on a virtual clock: `tests/unit/test_v2_fixes.py`, `test_codel_*`.

### C43, discovery and drain

| Knob | Default | Meaning |
|---|---|---|
| `RV_FIX_C43` | on | |
| `RV_GUARD_ROUTE_EPS_US` | 250 | Owners within this estimated backlog of the minimum count as a tie (random pick, no herding) |
| `RV_GUARD_DISCOVERY` | `store` if remote with no `RV_GUARD_OWNER_ADDRS`, else `static` | Workers poll `{rv2}:guard:owners` every `RV_GUARD_DISCOVERY_MS` (1000) and add/retire owners live |
| `RV_GUARD_REGISTER_TTL_MS` / `RV_GUARD_ADVERTISE` | 3000 / listen address | Owner registration TTL (heartbeat every TTL/3) / the host:port gateways should dial |
| `RV_GUARD_DRAIN_S` | 30 | Owner drain bound after SIGTERM |
| `RV_DRAIN_ACCEPT_S` / `RV_DRAIN_S` | 15 / 60 | Gateway SIGTERM sequence (§4). `RV_DRAIN_ACCEPT_S=0` = frozen-1 immediate stop |
| `RV_ATTRIBUTION` | header | `off` \| `header` \| `stages` (§5) |

Pre-existing knobs, unchanged: `RV_GUARD_OWNER_ADDRS` (static owners), `RV_GUARD_OWNER_LISTEN`, `RV_GUARD_FLEET_WORKERS` (now advisory under C10).

### C36

| Knob | Default | Meaning |
|---|---|---|
| `RV_FIX_C36` | on | Gateways refuse to start without `RV_STATE_HMAC_KEY` |
| `RV_STATE_HMAC_KEY` | — | Record-signing key (control plane + gateways) |
| `RV_DATABASE_URL` | — | Postgres DSN (writer and re-hydrator only) |
| `RV_REHYDRATE_PERIOD_MS` / `RV_BUDGET_CHECKPOINT_S` | 1000 / 5 | Re-hydration check period; budget-counter checkpoint period (post-flush budget overshoot ≤ one period of consumption) |
| `RV_REHYDRATE_STALE_GRACE_MS` | 250 | A store *older* than Postgres is re-checked after this grace before it is repaired. A writer publishes right after its commit, so that in-flight publish is not logged as a fault |

### Signals

| Knob | Default | Meaning |
|---|---|---|
| `RV_EXPORT_S` | 10 | Signal window |
| `RV_CLOUD_MONITORING` | off | `on` = push to Cloud Monitoring (instance/pod SA token from the metadata server). A failed push is counted and never stops the exporter |
| `RV_MONITORING_RESOURCE` / `RV_MONITORING_PREFIX` | `gce_instance` / `custom.googleapis.com/rv2` | |

### Store links (D2/D1, RC2)

Every Redis/Valkey connection is built in `runtime/storeconn.py`. A socket that died underneath a pooled connection is replaced at checkout, never handed out. Transport faults (uvloop `RuntimeError`, a socket `ETIMEDOUT`) become `ConnectionError`, which is a StoreError: the client gets a declared 503, never a 500. No redis-level retries and no PING health checks, except on pub/sub.

The plan push listener (`runtime/push.py`) never uses `PubSub.listen()`:
- It polls with `get_message(timeout=…)`.
- It treats three immediate "no message" answers in a row, any error, or 2 × `RV_PUBSUB_PING_MS` of silence as a dead connection.
- It re-subscribes with equal-jitter exponential backoff and runs one catch-up reconcile after every subscription.
- It takes at most `RV_PUBSUB_DRAIN_MAX` messages per wakeup.

| Knob | Default | Meaning |
|---|---|---|
| `RV_STORE_BG_TIMEOUT_MS` | 1000 | Background store op / connect / pool-wait timeout (kill-switch refresh, plan reconcile, audit, discovery, pub/sub close). Must be ≤ `RV_KS_STALE_MS`/2 |
| `RV_PUBSUB_PING_MS` | 5000 | Pub/sub liveness: PING after this much silence; dead after 2× |
| `RV_STORE_BACKOFF_MS` | `100,2000` | Pub/sub re-subscribe backoff `first,cap` |
| `RV_PUBSUB_DRAIN_MAX` | 64 | Messages per wakeup, coalesced into one reconcile |

### Edge connections (RC2)

| Knob | Default | Meaning |
|---|---|---|
| `RV_CONN_MAX_REQUESTS` | 0 (off) | The response to the Nth request on a keep-alive connection carries `connection: close` |
| `RV_CONN_MAX_AGE_S` | 0 (off) | ... the first response after the connection is T s old |
| `RV_KEEPALIVE_S` | 75 | uvicorn keep-alive timeout |

- Nothing is cut. An SSE stream only gets the header on its response, and the connection closes after that response. This is the same mechanism as the drain.
- Use it behind an L4 passthrough NLB (bulletin B22): it balances per connection, so keep-alive clients would stay pinned to the old backends after a scale-out.
- Counters: `conn_closed_max_requests`, `conn_closed_max_age`.

### Negative controls: what each one flips

| Negative control | Set | Everything else |
|---|---|---|
| C38 off | `RV_FIX_C38=off` (+ `RV_METRICS_DUMP_S=1` for round 1's 1 s dump) | defaults |
| C10 off (frozen-1) | `RV_FIX_C10=off` (+ `RV_GUARD_DEADLINE_MS=20` and `AMF_TARGET_P99_MS=1000` to reproduce round 1's unit lane) | defaults |
| C10: RC1's admission rule | `RV_OWNER_ADMISSION=bound` (12 ms bound + budget shed) | defaults |
| C43 off | `RV_FIX_C43=off` (per-worker round-robin) | defaults |
| C36 off | `RV_FIX_C36=off`, seeded with `tools/seed.py`. After a flush nothing restores the store: re-seed with `tools/seed.py` by hand | defaults |

## 3. Control plane (C36)

`python -m rvproto.control` subcommands (§1 lists the env): `init-db`, `seed`, `plan-set FILE.json`, `plan-update ORG VERSION [--rule R --mode M --action A]`, `plan-delete ORG` (explicit OFF = offboarded), `key-add KEY ORG`, `key-revoke KEY`, `killswitch global|org|model [NAME] on|off`, `budget-set ORG TOKENS`, `rollback LOG_ID`, `rehydrate [--once]`, `verify`.

- **Keys:** outputs name a key by `sha256[:12]`. API keys are never printed.
- **Every write prints its outcome** (RC2) as one JSON line: `status`, `state_version`, `epoch`, `seq`, and `hash` (the record body's sha256).
  - `ok` (exit 0): committed and published.
  - `ok_publish_pending` (exit 0): committed to Postgres; the store publish failed (a Postgres failover right after the commit, a store outage...); the re-hydrator will publish it. The write is in effect once published.
  - `unknown` (exit 3): the connection failed during COMMIT, so the write may or may not be durable. `verify` or `rv2_log` tells which.
  - `error` (exit 1): not committed.
  - RC1 reported committed writes as failures when Postgres flapped after the commit (r2-state: 22 of 149). F-C36-WRITER checks the fix: a Postgres restart mid-stream, then every `ok` must be in Postgres and no `error` may be.
- **Write path:** one Postgres transaction locks the kind's version row, writes the record at (epoch, seq+1), appends to `rv2_log` and bumps `rv2_version`. It then publishes the kind's complete signed record set plus its manifest in one MULTI/EXEC, and a pub/sub nudge on `{rv2}:updates`. If the publish fails, the write is still durable and the re-hydrator republishes it.
- **Publishes are consistent and never go backwards:**
  - Every publish (writer or re-hydrator) reads the version and the record set as one Postgres snapshot. The version row is read `FOR SHARE`, which waits for a writer holding it. So a manifest always describes exactly the records it ships.
  - The MULTI/EXEC runs under `WATCH` of the manifest, and is skipped (`state_publish_skipped`) when the store already holds a newer verified manifest.
  - Without these, concurrent writers produced manifests labelled with a version whose content they did not match: 380 of 694 sampled manifests in F-C36-RACE on the dev1 image. A gateway could then cache a revoked key as valid under the new auth epoch.
- **Rollback:** `rollback LOG_ID` re-writes that log entry's content as a new signed version at a new epoch. Versions never go backwards.
- **Re-hydrator:** every period, per kind (`plan`, `key`, `ks`, `budget`), it compares the store's manifest with Postgres (version, count, digest). If the store's copy is missing, invalid, stale or has a different digest, it republishes the kind. If the store is *ahead* of Postgres, it first moves Postgres to a new epoch, so no gateway ever sees a regression.
  - **Budget counters.** Every live counter `{rv2}:budget:<org>` is tagged, in the same MSET, with the version of the budget record it counts down (`{rv2}:budget_gen:<org>`). Checkpoints to Postgres carry that version.
    - A vanished counter is restored from the checkpoint of the *current* budget version, else from the limit. So a checkpoint taken before a `budget-set` is never restored over the new budget.
    - A counter tagged with an older budget (a `budget-set` whose counter write was lost) is reset to the limit.
    - An untagged counter is adopted: only the tag is written.
    - Repairs are a compare-and-set on the tag, so they never overwrite a concurrent `budget-set`.
    - Before this fix, a flush after a budget reset restored the old budget's checkpoint: the container smoke got 429 `insufficient_quota` for org-q, whose checkpoint was 0 from an earlier test.
  - It logs `{"event":"rehydrated","kind","reason","detected_at","restored_at","took_ms","records"}`, which is the re-hydration bound.
  - Safe to run several copies.
- **Store layout:** all rv2 keys carry the `{rv2}` hash tag (one slot even on a cluster-mode store).
  - `{rv2}:meta:<kind>` (manifest)
  - `{rv2}:plan:<org>` + `{rv2}:plan_index`
  - `{rv2}:keys`
  - `{rv2}:ks`
  - `{rv2}:budget:<org>` (remaining tokens) + `{rv2}:budget_gen:<org>` (its budget version) + `{rv2}:budget_meta`
  - `{rv2}:guard:owners` + `{rv2}:guard:owner:<id>` (discovery)
  - Audit streams keep the frozen `rv:audit:<org>`.
- **C36 off (negative control):** seed with `tools/seed.py` (frozen `rv:*` layout) and use `tools/killswitch.py`, `tools/plan_update.py`, `tools/quota.py`.

## 4. Failure semantics (C36 / C10 / drain)

| Fault | What a client sees |
|---|---|
| Store unreachable or slow (> `RV_STORE_TIMEOUT_MS`) on a cache miss | 503 `shared_state_unavailable` + `Retry-After` |
| Gateway↔store network partition (RC2, D2/D1) | Cached keys keep being served from RAM while the kill-switch snapshot is younger than `RV_KS_STALE_MS` (5 s). After that, 503 `kill_switch_unavailable` + `Retry-After` for everyone. Uncached keys: 503 `shared_state_unavailable`. Never a 500, never a spinning or growing worker. On heal, dead pooled sockets are replaced at their next use, and pub/sub re-subscribes within `RV_STORE_BACKOFF_MS` (≤ 2 s) plus one catch-up reconcile. **Declared recovery bound: ≤ 5 s after heal**, measured by F-PARTITION |
| Store flushed / partial / forged / regressed (C36) | Refreshes fail like an outage. RAM snapshots keep serving up to `RV_KS_STALE_MS` (killed orgs stay killed, revoked keys stay revoked). Then 503 `kill_switch_unavailable` / `plan_unavailable` for everyone until the re-hydrator restores. Never 401/403 for a valid key, never "unknown tenant". Uncached keys get 503 during the gap. A vanished budget counter gives 503, not 429. **Every gap 503 carries `Retry-After`** (RC2) = max(1 s, `RV_REHYDRATE_PERIOD_MS` + max(`RV_KS_REFRESH_MS`, `RV_PLAN_RECONCILE_MS`)), 2 s at defaults. **Declared re-hydration bound:** detection ≤ `RV_REHYDRATE_PERIOD_MS` (1 s), plus the restore (`took_ms`, 40–80 ms measured), plus one gateway refresh (≤ 1 s): ≈ 2.1 s at defaults. **Staleness bound:** `RV_KS_STALE_MS` (5 s) |
| Key never issued (manifest present) / revoked | 401 `invalid_api_key` |
| Kill switch engaged (global, org or model) | 503 `kill_switch_engaged` |
| Org offboarded (explicit OFF plan) / never onboarded (verified complete index) | 403 `plan_unknown_tenant` |
| Owner overloaded: standing queue (codel) or hard cap; bound mode: 12 ms bound or budget | 503 `overloaded` + `Retry-After` (s, ≥ 1) + `retry-after-ms` (drain time, ≥ own execution). Never an UNAVAILABLE finding. Admitted work is answered, late if a burst made it wait, within `RV_GUARD_WAIT_MS` |
| Owner dies / unreachable / silent (> 2× budget) | The guard finding is UNAVAILABLE, then the plan's posture applies: FAIL_CLOSED → 403 `blocked_by_policy`, FAIL_OPEN → 200 with `sem:U` (recorded in audit `unavailable_detectors`). A dead connection found at write time is re-routed first, never a 500 |
| Owner draining (SIGTERM) | New work is re-routed (REDIRECT, same deadline). Queued work finishes. The owner exits when idle or at `RV_GUARD_DRAIN_S` |
| Store flushed or failed over: owner registrations gone | Gateways keep every owner whose connection is up and not draining (`guard_owner_kept_unregistered`). Owners re-register within `RV_GUARD_REGISTER_TTL_MS`/3. An unregistered owner is dropped only once its connection is gone: TCP keepalive finds a vanished peer in ~4 s, and an overdue request with no answer closes the connection sooner. Before this fix, a flush dropped the whole fleet for ~1–2 s: requests went out with an UNAVAILABLE guard, so FAIL_CLOSED tenants got 403 `blocked_by_policy` and FAIL_OPEN tenants skipped the detector |
| Gateway draining (SIGTERM) | `/readyz` 503 immediately and `connection: close` on responses for `RV_DRAIN_ACCEPT_S`. Then it stops accepting; in-flight streams finish within `RV_DRAIN_S`; exit 0 |
| Any worker not ready | `/readyz` 503 on every worker (node readiness = all workers ready; `SO_REUSEPORT` spreads connections over all of them) |
| Node exporter loop error | Counted (`exporter_loop_errors` signal) and logged at most every 10 s (`exporter_loop_error`). The loop keeps running, so node readiness and signals keep updating. The exporter reads a private copy of every shared-memory histogram. Before this fix, a live-buffer read killed the dump thread, and `/readyz` stayed 503 while the node served traffic |

## 5. Observability

- **`x-rv-attr` per request** (`RV_ATTRIBUTION=header`):
  - `wk=` gateway worker, `tok=` tokenise ms, `dl=` the guard budget applied, in ms (codel: `RV_GUARD_WAIT_MS`, e.g. 100.0; bound: the derived deadline).
  - `gq=` / `ge=` owner queue / execution ms, `grtt=` worker↔owner round trip ms, `go=` owner `host:port` (or socket).
  - `RV_ATTRIBUTION=stages` also appends `attr:<same>` to `x-rv-stages`, which the READY `olg` records per request (`stages`), so per-worker and per-owner analyses need no harness change.
- **Exporter `/metrics`** is valid Prometheus text (RC2; RC1 wrote two label sets per series and a Prometheus scrape rejected it). A unit test parses it with `prometheus_client`. It contains:
  - Worker histograms: `rv_<hist>{<own labels>,stat="n|max_ms|p50_ms|p90_ms|p99_ms|p99.9_ms|mean_ms"}`.
  - Counters: `rv_<name>_total{<own labels>}`.
  - Per-process gauges: `rv_<name>{<own labels>,worker="<i>"}`.
  - Guard owners' series: the same shapes plus `role="owner"`.
  - `rv2_signal{kind,name}`.
  - `loop_lag_p99_ms`, `loop_lag_p999_ms` (window), `cpu_cores_busy`, `cpu_utilization`, `requests_per_s`, `active_streams`, `inflight_requests`, `draining`.
  - Owners: `owner_queue_ms_avg`, `owner_queue_windows_avg` (time-averaged from the owners' backlog integrals), `owner_queue_ms_now_max`, `owner_requests_per_s`, `owner_shed_per_s`.
  - Guard / unit: `gpu_utilization`.
  - Exporter health: `exporter_loop_errors`, `monitoring_push_errors` (cumulative).
- **Cloud Monitoring:** `custom.googleapis.com/rv2/<gateway|guard|unit>/<signal>`, gauges, one point per `RV_EXPORT_S`.
- **New worker/owner metrics:**
  - `gc_pause_ns{gen}`, `guard_deadline_ms`, `input_nonguard_p99_ms`, `worker_ready`, `guard_owners_routable`.
  - `guard_owner_{added,removed,kept_unregistered,redirects,drain_seen,write_errors}`.
  - `owner_shed_reason{reason="standing_queue|hard_cap|budget|queue_bound"}`, `owner_backlog_ns`, `owner_state_pushes`.
  - Owners (RC2): `owner_sojourn_ns` (histogram: wait before execution), `owner_overloaded` (0/1), `owner_overload_entered`. Logs: `guard_owner_overloaded` / `guard_owner_overload_cleared`.
  - Store links (RC2): `store_reconnects`, `store_dead_transports`, `store_transport_errors`, `pubsub_dead`, `plan_pubsub_subscribed`, `plan_nudge_failures`, `plan_listen_errors`. Logs: `plan_pubsub_dead` (reason `error|silent|no_wait`).
  - Edge (RC2): `conn_closed_max_requests`, `conn_closed_max_age`, `conn_tracked`.
  - `state_unavailable_refreshes`, `lease_budget_missing`.

## 6. Tests

- **Unit** (controller): `cd SP/rvproto2 && RV_GATEWAY_V2_PATH=<vendor> RV_GUARD_TOKENIZER=<tokenizer-22M.json> .venv/bin/python -m pytest tests/unit`. RC2: 136 tests, including property tests (hypothesis), plus `lint-imports` (2 contracts) and the repo GW00 gates.
- **Functional** (dev VM with Valkey + Postgres containers, the L4, READY `synthprov`/`olg`):
  - `tests/functional/f_{c38,c10,c43,c36,drain}.py`, `f_c36_race.py` and `e2e_vm.sh`.
  - RC2 adds `f_partition.py` (60 s gateway↔store iptables partition), `f_writer_failover.py` (Postgres restart during writes), and `f_c10.py codel|bound|off overload|poisson45`.
  - `F_RATE` / `F_WORKERS` scale the loads to the VM; RC2's suite ran on a g2-standard-4.
  - All of them in sequence: `evidence/r2-impl/devvm/run_all.sh`.
  - Container smoke of the images: `evidence/r2-impl/devvm/cont_smoke.sh TAG`.
  - `f_c36_race.py` is self-contained, so it also runs inside any gateway image: mount it over `tests/functional/` and use `shell -c "python tests/functional/f_c36_race.py"`.
  - Results: `SP/evidence/r2-impl/functional/`.

## 7. Answers for the proof, chaos and platform lanes (RC2 status)

- **Worker identity on every response** (r2-state 1): `x-rv-attr wk=` is on admitted responses only. `x-rv-worker` on every response, 401/403/429/503 included, is an RC3 item (attribution record). Until then, attribute rejected requests through `/readyz` (`worker`).
- **Plan version** (r2-state 2): `x-rv-plan-version` carries the plan document version (`a-1`). The state version (epoch, seq) and a hash prefix are RC3.
- **Retry-After and codes** (r2-state 3): see §4.
  - `plan_unavailable`, `kill_switch_engaged`, `kill_switch_unavailable` and `shared_state_unavailable` are 503, with `Retry-After` from RC2 on.
  - A revoked key and a never-issued key both answer 401 `invalid_api_key`. Distinct codes are RC3.
- **Audit location** (r2-state 4, r2-chaos 3): Memorystore streams `rv:audit:<org>`, field `r` = JSON with `request_id` (the `x-request-id`), `org_id`, `key_id`, `plan_version`, disposition and phase.
  - They are volatile: FLUSHALL erases them, and a store outage drops the batch in flight. That is the known audit-durability gap; RC3 adds a local spool with replay.
  - Tail them to disk yourself.
- **Re-hydrator log** (r2-state 5): `rehydrated` with wall-clock `detected_at`, `restored_at`, `took_ms` and `records`. Budget repairs log as `rehydrated_budget_counters`. The bounds are in §4.
- **Per-worker state endpoint** (r2-state 6): `/readyz` reports `plans_loaded`, `plans_fresh`, `killswitch` (ok | engaged | stale) and `worker`. A per-org served-version endpoint is RC3.
- **Control-plane writes** (r2-state 7, r2-chaos 2, r2-console 2): the CLI in §3 is the whole write surface; there is no HTTP writer API. Every write prints its (epoch, seq, hash). A signed rollback is `rollback LOG_ID`.
  - A stale write or an unsigned regression has no CLI command yet. To inject one, write the store directly: an older manifest copied back, or a record with its `sig` edited. Gateways must answer 503 (`state_unavailable_refreshes`).
- **C36 off** (r2-state 8): after a flush nothing restores; re-seed by hand with `tools/seed.py`.
- **Deployment and supervisors** (r2-chaos 1):
  - On VMs, start the processes from the tarball (§1). Restart after a crash is yours: systemd `Restart=always`, or the MIG script's docker `--restart=always`.
  - Inside a node, the launcher restarts a dead guard owner (owner topology). A dead gateway worker makes the launcher drain and exit, so the supervisor restarts the node.
- **Provider timeouts** (r2-chaos 4): aiohttp `sock_connect` = `sock_read` (first byte and inter-chunk idle) = `RV_PROVIDER_TIMEOUT_S` (120 s); no total.
- **RV_NAMESPACE, DB index on every connection, RV_AUDIT_MAXLEN, PGDATABASE** (controller, r2console): RC3. RC2 still uses the fixed `{rv2}:` prefix and the `{rv2}:updates` channel. On the shared store, lanes must not run plan publishers concurrently until RC3 (bulletin B18).
- **COS firewall and Secret Manager parsing in `mig-startup.sh`** (r2-mig): fixed in RC2.
- **`tini` without `-g`** (r2-gke): in every image since RC1.
- **`/metrics` validity** (r2-gke): fixed in RC2.
- **Connection max-age / max-requests** (r2-mig, r2-gke): in RC2 (§2).
- **RC3 list:**
  - RV_NAMESPACE (keys + channels) / DB index / RV_AUDIT_MAXLEN + export / PGDATABASE.
  - RemoveIPC hardening (a service-owned shm dir, segment self-check, `preflight`).
  - Drain 300 s by default + a terminal SSE error event.
  - C43 push min-interval + the 36/120/240-subscriber microbench.
  - Owner-death re-dispatch (503, not a FAIL_CLOSED 403).
  - Durable audit spool.
  - Attribution record + `x-rv-worker` / `x-rv-owner` headers.
  - Test fault knobs `RV_TEST_BURST_*` / `RV_TEST_GUARD_DELAY_MS`. RC2 has `RV_INJECT_CPU_BURST` and `RV_INJECT_OWNER_DELAY_MS`.
  - HMAC key from a file or Secret Manager path, with a key id.
