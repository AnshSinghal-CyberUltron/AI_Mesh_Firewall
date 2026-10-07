# d2-d1.patch: partition OOM (D2) and the RuntimeError 500 (D1)

Lane r2-fix-d2, for proto-builder (r2-impl). The patch is a unified diff from the base snapshot to the work tree.

- Base snapshot: `SP/rvproto2-d2base`, copied from `SP/rvproto2` at 2026-09-24T08:43:58Z. Its manifest is `SP/evidence/r2-fix-d2/d2base.MANIFEST.sha256` (sha256 `88dcf1fd…`, 165 files).
- Work tree: `SP/rvproto2-d2work`.
- Patch: `SP/evidence/r2-impl/patches/d2-d1.patch`, sha256 `13aa835ec518a15a0a357fa9469af8092ded647f686c12df367ce0b037eba51a`.

## Applying it

```bash
cd SP/rvproto2 && patch -p1 --dry-run < SP/evidence/r2-impl/patches/d2-d1.patch && patch -p1 < SP/evidence/r2-impl/patches/d2-d1.patch
```

- **On the base:** it applies cleanly. The base plus the patch is byte-identical to the work tree.
- **On the live tree (re-checked 10:11Z):** it dry-runs cleanly with no fuzz. That tree already has proto-builder's later edits to `deploy/images/mig-startup.sh`, `control/{rehydrate,writer}.py`, `detect/guard/owner.py`, `edge/exporter_http.py`, `runtime/exporter.py`, `tests/functional/{f_c43,f_drain}.py` and `tests/unit/{test_c36_state,test_v2_fixes}.py`. None of those files is touched by the patch.
- **Unit tests:** the live tree plus the patch passes **128/128**, including the GW00 gates and import-linter (`evidence/r2-fix-d2/unit/live-tree-plus-patch-unit-run.txt`).
- **Flags:** the fix is not behind a flag. It changes how store connections behave, not what any of the four fixes decides. `RV_FIX_C36=off`, the frozen-1 plan snapshot, uses the same listener.

## Root causes (proved by experiment)

### D2: redis-py 8.1.0 misreads a dead socket's `ETIMEDOUT` as "no message yet"

1. **The trigger.** redis-py 8.1 turns TCP keepalive on by default: 30 s idle, then 5 s × 3 probes. So an idle pub/sub socket dies with `ETIMEDOUT` about 45 s after its last traffic during a silent partition (host iptables DROP). This matches r2-chaos's "~25 s or more": 45 s minus the idle time before the fault.
2. **uvloop stores the error.** It calls `connection_lost(TimeoutError(110))`. The `StreamReader` keeps that exception and re-raises it at once on every later `read()`.
3. **redis-py swallows it.** `AbstractConnection.read_response` catches it as `asyncio.TimeoutError`, which is the same class as `TimeoutError` since Python 3.11. The PubSub read carries `timeout=math.inf`, which is not `None`, so redis-py takes the branch meant for a caller's timeout and returns `None` without disconnecting (`redis/asyncio/connection.py:1303-1306`).
4. **`listen()` spins.** It loops `parse_response` → `handle_message(None)` without ever yielding (`client.py:1604`). This is exactly r2-chaos's py-spy stack.
5. **Memory grows.** Every re-raise of the one stored exception grows its `__traceback__` chain. In the base probe the chain reached 758,011 frames and RSS grew about 150–400 MB/s until the OOM kill.
6. **Recovery is slow.** The spin never ends at a heal; only the OOM kill ends it. The launcher then exits on the worker's non-zero exit code, and the restarted gateway cannot start while the store is unreachable. That is why r2-chaos saw 40–80 s recoveries.

Evidence:
- `evidence/r2-fix-d2/rootcause/redis-py-8.1.0-lines.txt`.
- `rootcause/base-spin-probe.jsonl`: loop heartbeats go 20 → 0 per 100 ms; RSS 52 → 550 MB in 3 s.
- `repro/repro-orig`: the r2-chaos script on a real iptables partition. The keepalive gives up 45 s after the last traffic, the loop blocks, and RSS reaches 6.2 GB, still growing 80 s later.

### D1: the pool hands out a pooled socket that died underneath it

1. The same keepalive death, on an idle **request-path** pooled connection, leaves `is_connected` true.
2. `ConnectionPool.ensure_connection → can_read()` treats it as clean: the reader holds an exception, not EOF.
3. The next command's `writelines` raises uvloop's `RuntimeError("... the handler is closed")`. That is not a `RedisError` or an `OSError`, so no `StoreError` clause catches it, and the client gets HTTP 500 `text/plain`.
4. Every dead pooled socket is a latent 500 **until it is next used**. In the base D1-variant run, 12 of them fired 42 s after the heal.

Evidence: `unit/base-child-d1_dead_pooled_connection.json` shows the exact error message, and `runs/d1-base-1/gw.log` holds 64 identical uvloop tracebacks.

## What the patch changes (13 files: 8 modified, 5 new)

| File | Change |
|---|---|
| `rvproto/runtime/storeconn.py` (new) | **The store client boundary.** `RvConnection` / `RvSSLConnection` / `RvUnixConnection` (a mixin over redis-py's asyncio connections):<br>(1) A dead transport (closed, or a stored reader error, or EOF) is disconnected and reconnected at checkout (`connect_check_health`) and before a send, never handed out.<br>(2) A builtin `TimeoutError` raised inside `_read_response_from_parser` is the socket's `ETIMEDOUT`, because a read deadline expires outside that frame. It becomes `redis.ConnectionError` instead of redis-py's "no message" `None`.<br>(3) A `RuntimeError` from the transport on write or read becomes `redis.ConnectionError` (a `StoreError`).<br>Pools are built from `parse_url()` because `from_url()` lets the URL's `connection_class` (rediss/unix) override ours. Each pool gets explicit op, connect and pool-wait timeouts, `health_check_interval=0`, `retry_on_timeout=False` and `Retry(NoBackoff(), 0)`. Clients own their pool (`Redis.from_pool`). Also `sync_client()` for the control plane. |
| `rvproto/runtime/push.py` (new) | `PushListener`, which replaces both `async for msg in ps.listen()` loops.<br>- It polls with `get_message(timeout=ping/4)`.<br>- Dead connection = any store/transport error, **or** 3 consecutive "no message" answers that returned without waiting (the spin signature, whatever the client library does), **or** 2× `RV_PUBSUB_PING_MS` of silence. A verified `PING` goes out after 1× of silence; its pong counts as traffic.<br>- A dead connection is closed (pool slot released) and re-subscribed after an equal-jitter exponential backoff (`RV_STORE_BACKOFF_MS`).<br>- After every (re)subscription there is one catch-up nudge. A nudge that fails is retried on every poll wakeup until it succeeds.<br>- At most `RV_PUBSUB_DRAIN_MAX` messages are taken per wakeup and coalesced into ONE reconcile. Nothing is buffered, and `sleep(0)` runs after every batch and every non-waiting answer. |
| `rvproto/runtime/store.py` | `connect()` (request path) and `connect_background()` go through `storeconn.client`. The background pool now has explicit `timeout_s`; the base relied on redis-py's 5 s op/connect and 20 s pool-wait defaults. Both accept `events=` (a metrics hook). Signatures are backward compatible. |
| `rvproto/plan/snapshot_v2.py`, `rvproto/plan/snapshot.py` | `_listen()` is now `PushListener.run(...)`. `_pushed()` returns success. Optional `push: PushKnobs = DEFAULT_PUSH`. |
| `rvproto/edge/state.py` | Wires `RV_STORE_BG_TIMEOUT_MS`, the push knobs and `events=metrics.inc` into the pools and the snapshots. |
| `rvproto/runtime/config_v2.py`, `rvproto/runtime/config.py` | New `store` knob group, validated. `RV_STORE_BG_TIMEOUT_MS ≤ RV_KS_STALE_MS/2`, like the refresh periods. |
| `rvproto/detect/guard/discovery.py` | The guard-node `OwnerRegistration` client goes through the boundary. The same 1 s op/connect bound applies (now `min(1 s, TTL/3)`). A transport `RuntimeError` there would have killed the heartbeat task and silently de-registered the owner. |
| `rvproto/control/__main__.py` | The writer/re-hydrator client is `storeconn.sync_client` (explicit connect timeout, 5 s as before). |
| `tests/unit/test_store_resilience.py` (new), `store_faults.py`, `store_fault_child.py` | 12 tests; see (a) below. |

### Connection audit (every Redis/Valkey client in rvproto)

| Client | Op / connect / pool-wait timeout | Before the patch |
|---|---|---|
| Request path: identity, lease Lua, `/v1/models` | `RV_STORE_TIMEOUT_MS` (25 ms) / same / same; no retry, no PING | same timeouts; dead pooled sockets reused (**D1**) |
| Background: ks refresh, plan reconcile, audit XADD, discovery watch, **pub/sub** | `RV_STORE_BG_TIMEOUT_MS` (1000 ms) / same / same | none set (redis-py 8.1 default 5 s / 5 s / 20 s); pub/sub `listen()` with no read timeout (**D2**) |
| Guard node `OwnerRegistration` | `min(1 s, TTL/3)` / same | 1 s / 1 s, default connection class |
| Control plane (writer, re-hydrator; sync) | 5 s / 5 s | 5 s / default |

A structural unit test keeps it that way: no `Redis(`, `.from_url(` or `ConnectionPool(` outside `runtime/storeconn.py`, and no pub/sub `listen()` loop in `rvproto/`.

**Retry and health-check policy.** Everywhere: none (`retry_on_timeout=False`, `health_check_interval=0`, `Retry(NoBackoff(), 0)`). The reasons:
- The request path's store budget is one `RV_STORE_TIMEOUT_MS`. A retry or a PING round trip would exceed it, and the answer to a store fault is the declared 503.
- Background loops already retry on their own period.
- A redis-level retry could duplicate a non-idempotent write (audit `XADD`).
- A dead pooled socket is found without I/O, and the one connection that can idle for ever (pub/sub) has its own verified liveness PING. redis-py's pub/sub health check sends a PING but filters the pong and never checks that it arrived.

## New env knobs (all in `V2_DEFAULTS`, logged at startup under `settings.v2.store`)

| Knob | Default | Meaning |
|---|---|---|
| `RV_STORE_BG_TIMEOUT_MS` | 1000 | Background store op / connect / pool-wait timeout. Must be ≤ `RV_KS_STALE_MS/2`. It also bounds closing a dead pub/sub connection. |
| `RV_PUBSUB_PING_MS` | 5000 | Pub/sub liveness: PING after this much silence; the connection is dead after 2× (then reconnect). Poll period = 1/4 of it. |
| `RV_STORE_BACKOFF_MS` | `100,2000` | Pub/sub re-subscribe backoff `first,cap`: equal-jitter exponential, uniform in [d/2, d]. It resets after a subscription that lived ≥ 1 ping period. |
| `RV_PUBSUB_DRAIN_MAX` | 64 | Hard cap on pub/sub messages taken per wakeup (coalesced into one nudge; the rest waits in the socket). |

`DEFAULT_PUSH` in `runtime/push.py` equals these defaults; a unit test keeps them equal. Readers built without knobs (tests, tools) still get the listener.

## New metrics (worker counters, exported as `rv_<name>_total`)

| Counter | Meaning |
|---|---|
| `store_reconnects` | A pooled connection re-established its socket after having had one |
| `pubsub_dead` | A pub/sub connection was declared dead (log `plan_pubsub_dead` with `reason` = `error` / `silent` / `no_wait`) |
| `store_dead_transports` | A dead socket found at checkout, before a send, or as a read `ETIMEDOUT` (each one a D1 500 averted on the request path) |
| `store_transport_errors` | A transport `RuntimeError` mapped to `ConnectionError` |
| `plan_pubsub_subscribed` | Successful (re)subscriptions |
| `plan_nudge_failures` | A nudge raised a non-store exception |

`plan_listen_errors` is kept and now counts dead pub/sub connections. `plan_push_applied` and `plan_reconcile_errors` are unchanged.

Logs now carry `Type: message`, because redis-py 8 exceptions `repr()` only as their category (`network:ConnectionError`). The other pre-existing rvproto logs still use `repr(exc)` and so lose the message. That is a follow-up.

## Test results

### (a) Unit tests
Command: `cd <tree> && RV_GATEWAY_V2_PATH=<repo>/gateway_v2 RV_GUARD_TOKENIZER=<tokenizer-22M.json> .venv/bin/python -m pytest tests/unit`.

The scenario tests run in a child process on **uvloop**, against a real TCP store (fakeredis). A spinning event loop therefore cannot hang pytest, and the child's watchdog thread reports what it saw.

| Suite | Base | Work (base+patch) |
|---|---|---|
| Full unit suite, incl. GW00 gates + import-linter | 112 passed | **124 passed** (112 + 12 new) |
| Live tree 09:42Z + patch | — | **128 passed** |

Per-scenario results; the same child runs on either tree:

| Scenario | Base | Work |
|---|---|---|
| `pubsub_etimedout` (the subscribed socket dies with `ETIMEDOUT`) | **FAIL**: loop stalled 13.1 s, RSS +1,602 MB, update never delivered | push applied 43 ms after publish, RSS +0.3 MB, max loop gap 5.7 ms |
| `pubsub_partition` (silent partition that outlives the TCP connection) | **FAIL**: never detected (0 dead, 1 pub/sub); update never arrived | 4 dead in 2 s, recovered **0.78 s** after heal, loop gap 5.7 ms |
| `listener_client_bugs` (a client that never waits; a message flood) | n/a (no listener) | 29 dead / 29 re-subscriptions / 29 catch-ups, loop gap 6.6 ms. Flood: 2.39 M messages → 298 k nudges (8 per nudge), loop gap 5.0 ms |
| `d1_dead_pooled_connection` | **FAIL**: `RuntimeError: unable to perform operation on <TCPTransport closed=True ...>; the handler is closed` | principal returned; `store_dead_transports=1`, `store_reconnects=1` |
| `d1_admission_faults` (7 transport faults into `Admission.admit`) | **FAIL**: `RuntimeError` escapes `admit()` for `write_uvloop_closed`, `read_uvloop_closed` and `keepalive_death` (HTTP 500) | every fault → `503 shared_state_unavailable`, except `keepalive_death`, which is reconnected and served (grant) |

Files: `evidence/r2-fix-d2/unit/{base,work}-child-*.json`, `*-unit-run*.txt`.

### (b) r2-chaos standalone repro against the fixed pattern
Host: VM `rv-r2fixd2-sut-1`, running Valkey 8 in Docker, uvloop 0.22.1, redis-py 8.1.0, hiredis 3.4.2, Python 3.12.3. Each run subscribed, then a **real host iptables DROP** of 60 s was applied 20 s after start, and the run continued 40 s past the heal.

| | `pubsub_repro.py` (r2-chaos, unchanged) | `pubsub_repro_fixed.py` (boundary + `PushListener`) |
|---|---|---|
| RSS | 32 MB → **6,237 MB**, still growing 80 s after the spin began | 33.9 MB → **33.9 MB** |
| Event loop | blocked 24.1 s into the partition (45 s after the last traffic); never recovers | alive throughout; max lag 2.4 ms |
| After the heal | spinning | re-subscribed 1.1 s after heal; first message 2.1 s after heal |
| Reconnects | — | 24 `pubsub_dead`: silence found at 10 s, then 1 s connect timeouts with ≤ 2 s jittered backoff |

Files: `evidence/r2-fix-d2/repro/{repro-orig,repro-fixed}/`, `repro/analysis.txt` (from `vm/analyze_repro.py`).

### (c) Functional test on GCP
Setup:
- **VM:** `rv-r2fixd2-sut-1`, c4d-highcpu-16, asia-south1-c.
- **Stores:** Valkey 8 in Docker on the bridge; the gateway dials the container IP, so host iptables can partition it. Postgres 16 in Docker for the C36 control plane.
- **Gateway:** a systemd unit with `Restart=always`, as in r2-chaos. `WEB_CONCURRENCY=4`, with 1 CPU guard owner (`RV_GUARD_TOPOLOGY=owner`, `local_cpu`, 10 threads, `RV_SLO_MS=1000`, `RV_OWNER_QUEUE_MS=3000`), on CPUs 0–11 with `MemoryMax=20G`.
- **Harness:** READY `olg` and `synthprov` on CPUs 12–15 of the same VM (a functional test, not a latency claim). Load: 50 RPS constant, 70 % SSE.
- **Canaries:** 2 RPS each of a revoked key and a key of a kill-switched org.
- **Monitoring:** per-process RSS and CPU every 1 s, and exporter counters every 5 s.
- **Protocol:** 60 s steady, then **3 × [60 s host iptables DROP gateway↔store, heal, 60 s]**.
- **Analysis:** `vm/analyze_partition.py` recomputes every verdict from the raw files.

**Corpus** (`rv-evidence-raw/r2-fix-d2/corpora/headline-short128-nofp-22M.jsonl`, sha256 `c1f7934b…`: HEADLINE-22M lines with `tokens_in < 128`, minus the ids in `runs/corpus-removed-fp-ids.json`). One CPU owner measures 6,383 tokens/s. That is about 4× short of HEADLINE at 50 RPS: a smoke run gave 2,485 × 503 `overloaded` (`runs/smoke-work-headline`). The runs therefore use the HEADLINE prompts under 128 tokens, minus 3 prompts that the detectors block deterministically (`runs/corpus-removed-fp-ids.json`). That leaves 153 benign prompts, and the pre-fault baseline is 100 % 200.

| Run (tree sha) | RSS/worker (max Δ) | Worker deaths | Gap codes ⊆ USAGE §4 | HTTP 500 | Time to normal after heal (3 cycles) | Verdict |
|---|---|---|---|---|---|---|
| `part-base-1` (base `61d10b32…`) | **+10,126 MB** | **5 unit restarts, 35 OOM-kill lines, 20 of 24 workers gone** | **no**: 5,053 cut streams, 705 connect errors, 112 timeouts, canary read errors | 0¹ | 1.9 / 4.5 / 0.8 s² | **FAIL** |
| `part-work-2` (work `a66285d1…`, = the patch) | **+6.4 MB** | **0** | **yes** | **0** | **0.87 / 0.85 / 0.81 s** | **PASS** |
| `part-work-1` (work before `Redis.from_pool`) | +7.3 MB | 0 | yes | 0 | 0.82 / 0.79 / 0.74 s | PASS |

Gap codes in the fixed runs, per cycle:
- **main:** 200 until the RAM snapshots age past `RV_KS_STALE_MS` (104–241 per cycle), then 503 `kill_switch_unavailable` (~2,755), plus 503 `shared_state_unavailable` on lease refills (0–144) and 503 `plan_unavailable` (0–5).
- **revoked key:** 503 `shared_state_unavailable`, then 503 `kill_switch_unavailable`.
- **killed org:** 503 `kill_switch_engaged`, then 503 `kill_switch_unavailable`.
- **Never** 401 for a valid key, 403 `plan_unknown_tenant`, a 200 for a revoked key or killed org, or a 500.

After the heal: the last `kill_switch_unavailable` is sent 0.40–0.43 s after the heal, and the last non-steady response is `plan_unavailable` at 0.81–0.87 s (the next plan reconcile, `RV_PLAN_RECONCILE_MS` = 1 s).

Fix-side counters in `part-work-2`: `pubsub_dead` 286, `plan_pubsub_subscribed` 16 (4 + 4 × 3), `store_dead_transports` 6, `store_reconnects` 42.

The base fails in both ways r2-chaos saw:
- Each partition OOM-kills workers 32–50 s in (cgroup OOM at 5.0–10.7 GB anon RSS per worker, `runs/part-base-1/kernel-oom.txt`); the launcher exits; the restarted gateway crash-loops (exit 3) until the store is back.
- The traffic that hit spinning or restarting workers got cut streams and connection errors instead of the declared 503s.

¹ The base shows no 500 here because every worker was OOM-killed and restarted with fresh pools, so D1 had nothing to fire on. The D1 variant below shows D1 at gateway level.
² The base's short time to normal is an artifact of `MemoryMax=20G`: the OOM kill, which is the only thing that ends the spin, landed inside each 60 s partition. Without a cap (r2-chaos) it can land after the heal, hence their 40–80 s recoveries.

**D1 variant** (`vm/gw_run.sh` with `BURST_N=64 CHURN_S=1`, 3 × 36 s partitions):
- **Pool setup:** 64 concurrent revoked-key reads 15 s before each injection deepen every worker's request-path pool.
- **Keeping the pub/sub alive:** a plan nudge every 1 s from inside the Valkey container keeps the pub/sub connection busy, so it survives a 36 s partition while the idle pooled sockets die by keepalive.
- **Check:** 64 concurrent reads 2 s after each heal check those sockets out again.

| Run | Burst results | HTTP 500 | Deaths | Verdict |
|---|---|---|---|---|
| `d1-base-1` (base) | post-1: 36 × **500** · pre-2 (42 s after heal 1): 12 × **500** · post-2: 1 × **500** · post-3: 15 × **500**; the rest 401 | **64** (64 uvloop `handler is closed` tracebacks in `gw.log`) | 0 | **FAIL** |
| `d1-work-1` (work `a66285d1…`, = the patch) | every burst 64 × 401 (384/384) | **0** (0 tracebacks; `store_dead_transports` = **67**: the dead pooled sockets, replaced at checkout) | 0 (RSS +6.3 MB; time to normal 0.35 / 0.41 / 0.43 s) | **PASS** |

## Notes for proto-builder

- **USAGE.md §2:** add the four knobs above. **§4:** a partition longer than keepalive detection no longer affects a worker's health. The gateway serves from RAM up to `RV_KS_STALE_MS`, then returns the declared 503s. It is normal within about 1 s of the heal (ks refresh ≤ 0.5 s, plan reconcile ≤ 1 s; the pub/sub re-subscribes within `RV_STORE_BACKOFF_MS` cap plus one connect). **§5:** add the counters above.
- **Out of scope, noted:** `KillSwitch.run` / `VersionedKillSwitch.run` / `AuditSink.run` / `discovery.watch` still catch only `StoreError`. A non-store exception, such as an undecodable record, ends the task, and the snapshot then stays stale until a restart. After this patch no transport fault can do that.

## Evidence index (`SP/evidence/r2-fix-d2/`)

- `d2base.INFO`, `d2base.MANIFEST.sha256`: the base snapshot.
- `rootcause/`: redis-py lines, and the uvloop spin probe on base vs work.
- `unit/`: unit runs and per-scenario child outputs, base and work.
- `repro/`: test (b), raw files plus `analysis.txt`.
- `runs/<run>/`: test (c). Raw `olg/requests.jsonl`, `canary.jsonl`, `burst.jsonl`, `partition.jsonl`, `procmon.jsonl`, `gw.log`, `exporter-*.txt`, `kernel-oom.txt`, `unit-*.txt`, `tree-sha.txt`, and `analysis.json`. Bulky raw files live in `/home/contact_cyberultron_com/rv-evidence-raw/r2-fix-d2/runs`; `runs` is a symlink to it.
- `vm/`: every script used on the VM (`vmsetup.sh`, `gw_run.sh`, `repro_run.sh`, `partition.sh`, `procmon.py`, `canary.py`, `burst.py`, `pubsub_repro_fixed.py`) and the two analyzers. `tarballs.sha256` records the tarballs deployed.
- `vm-ledger.jsonl`: the VM ledger.
