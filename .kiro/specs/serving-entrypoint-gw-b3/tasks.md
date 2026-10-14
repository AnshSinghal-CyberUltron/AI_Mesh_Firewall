# Implementation Plan: Serving Entrypoint — real ASGI host + worker model (B3)

## Overview

Convert the B3 design into a series of code-generation steps that stand up the **Serving_Entrypoint**
— the runnable process that constructs the injected production dependencies, calls the shipped
`gateway_v2.edge.app.build_app(...)`, and **hosts** the returned ASGI 3.0 application under
**uvicorn** — built **bottom-up along the import-linter layer contract**
(`runtime → admit → edge`), the same shape as the shipped GW15/GW16 cards (source before its tests;
property/unit/integration tests; a checkpoint; a final gate). B3 **hosts** `build_app`; it does not
rewrite it (`edge/app.py` stays untouched at 794 lines).

The plan lands, in order: the `runtime/serving_config.py` config surface (the `load_contract` /
`load_holdback_config` env→dataclass+logs idiom), the single **behaviour-preserving** extension of
the shipped `AdmissionController.drain()` (two optional keyword-only params + injected `sleep`, all
no-ops at defaults so every shipped admission test keeps passing), the new `edge/serve.py` host
(boot-deps + fail-closed fallback table, uvicorn config, SIGTERM drain hook, `main`), the real OS
worker-supervision `respawn` adapter wired into the shipped `WorkerSupervisor`, the `pyproject`
`serve` optional extra, and the real-socket OpenAI conformance (stock SDK + `curl` + the turned-on
`test_live_tcp_8300.py` against `gateway_v2`). Every value type and seam lands a caller in a later
step; the final steps boot the entrypoint over a real socket and run the full gate.

Implementation language: **Python** (the design is grounded in concrete `gateway_v2/gateway_v2/*.py`
modules and the shipped house idiom). All code targets `mypy --strict` + `ruff` clean.

### Conventions enforced in EVERY coding task

- **Property tests**: a seeded `random.Random` driven for **≥10,000 iterations** (≥100 minimum per
  the design), **NO hypothesis**; async lifecycle paths via `asyncio.run`. Each property test is
  tagged `# Feature: serving-entrypoint-gw-b3, Property N: <text>` and
  `# Validates: Requirements X.Y`, with exactly one property ↔ one property-based test. Only two
  genuinely universal properties exist (the config parser, Property 1; the extended-drain
  termination/clamp invariant, Property 2) — everything else is EXAMPLE / INTEGRATION / SMOKE per the
  design's prework, so no other property sub-tasks are added.
- **Value types** are frozen slotted dataclasses or `StrEnum`; `ServingConfig` is
  `@dataclass(frozen=True, slots=True)`. No module-level mutable — `serving_config.py` holds only the
  `_ENV_KEY` string constants and numeric defaults, matching `holdback_config.py`.
- **No capacity literal outside `runtime/resources.py`** — the default port/host/drain seconds are
  operational-config literals, not capacity-position literals; the **worker count is NOT a field**
  (it comes from `ResourceContract.workers()`), and the declared max stream duration comes from
  `ResourceContract.max_stream_duration_s()`.
- **No new import edge**: `serve.py` is an `edge`-layer module (the top layer) importing `build_app`
  (same package), `AdmissionController`/`WorkerSupervisor` from `admit`, and `ServingConfig` from
  `runtime` — all downward edges that already exist. `egress` never imports `detect`. `lint-imports`
  stays **2 contracts kept**.
- **`check_sizes`**: every function ≤ 120 lines and every module ≤ 800 lines; `edge/app.py` stays
  untouched at 794. `edge/serve.py` and `runtime/serving_config.py` are new small modules.
- **Honest boot / fail-closed everywhere**: each unwired `build_app` dependency falls back to its
  **shipped** fail-closed default and is named in a boot log line; `provider=StubProviderClient` and
  `catalog=empty_catalog_source` stay named deferred seams, never scaffolded. `uvicorn` is
  lazy-imported inside `main` so `edge` import and the AST/mypy gates stay uvicorn-free.
- Each task references its requirement sub-clauses and the design section/property it satisfies
  (traceability), matching the GW15/GW16 `tasks.md` style.

## Tasks

- [x] 1. Serving config surface — `runtime/serving_config.py` (NEW, bottom of the build order)
  - [x] 1.1 Implement `ServingConfig` + `load_serving_config(env)`
    - Create `gateway_v2/gateway_v2/runtime/serving_config.py` following the EXACT
      `load_holdback_config` / `load_contract` env→dataclass+logs idiom: parse env, validate, return
      a frozen value plus a tuple of warning log lines —
      `load_serving_config(env: Mapping[str, str] | None = None) -> tuple[ServingConfig, tuple[str, ...]]`
    - `@dataclass(frozen=True, slots=True) ServingConfig(host: str, port: int, drain_accept_s: float,
      drain_s: float, conn_max_age_s: float)`; keys + defaults `RV_HOST`="127.0.0.1" (loopback-safe),
      `RV_PORT`=8300, `RV_DRAIN_ACCEPT_S`=15.0, `RV_DRAIN_S`=60.0, `RV_CONN_MAX_AGE_S`=30.0
    - A present-but-unparseable or out-of-range value falls back to the documented default and
      appends ONE warning line naming the rejected key + value (byte-for-byte the `_parse_hold_cap`
      behaviour); the function NEVER raises and never leaves a field unset
    - Worker count is NOT a field (it comes from `ResourceContract.workers()`); module holds only the
      `_ENV_KEY` string constants + numeric defaults — no module-level mutable, no capacity literal
    - _Design: Components §1 (`serving_config.py`), Data Models (`ServingConfig`)_
    - _Requirements: 6.1, 6.2, 6.3, 6.5, 6.6, 6.7_

  - [x]* 1.2 Write property test for Serving_Config parse totality + defaulting (PBT)
    - `tests/runtime/test_b3_serving_config.py` — **Property 1: Serving_Config parse is total and
      never un-defaults a bad value**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.5, 6.7**; seeded `random.Random` ≥10,000 iters, no
      hypothesis: over random env mappings (missing keys, blank strings, non-numeric strings,
      out-of-range numbers) `load_serving_config` returns a fully-populated frozen `ServingConfig`
      where every field is the validly-parsed value OR its documented default, emits a warning line
      naming the key exactly when a present value was rejected, and never raises
    - _Design: Correctness Properties → Property 1_

  - [x]* 1.3 Write config example/unit tests
    - `tests/runtime/test_b3_serving_config.py`: all-unset env → the five documented defaults
      (8300 / "127.0.0.1" / 15.0 / 60.0 / 30.0) (6.2, 6.3, 3.6, 3.7, 3.10); a valid override is read
      through; worker count is NOT present on `ServingConfig`; `ServingConfig` is frozen (assignment
      raises)
    - _Design: Testing Strategy (unit/EXAMPLE — defaults, worker-count authority)_
    - _Requirements: 6.2, 6.3, 6.4, 3.6, 3.7, 3.10_
  - Run the gate (`.venv/bin/python -m pytest`, `mypy --strict`, `ruff`, `lint-imports`, the 6 AST
    gates) over `runtime/serving_config.py` before building on it.

- [x] 2. Minimal behaviour-preserving `AdmissionController.drain()` extension — `admit/admission.py` (MODIFY)
  - [x] 2.1 Extend `drain()` with two optional keyword-only params + injected sleep
    - Modify `gateway_v2/gateway_v2/admit/admission.py`: change `async def drain(self) -> DrainReport`
      to `async def drain(self, *, accept_grace_s: float = 0.0, drain_window_s: float | None = None,
      sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> DrainReport`
    - Extended flow, every added step a NO-OP at the defaults so ALL shipped tests keep passing:
      `ACCEPTING → DRAINING` (stop accepting, as today); NEW inside `DRAINING` —
      `await sleep(accept_grace_s)` then wait up to `drain_window_s` for in-flight streams to finish
      (poll `_in_flight` for `all(finished)` with an `await sleep(...)` bounded by the remaining
      window); `DRAINING → TERMINATING` runs `_declare_terminations()` exactly as shipped (one
      `DeclaredTermination(truncated=False)` per still-unfinished stream);
      `TERMINATING → FLUSHED → EXITED` (`_flush_audit()` + publish `duration_s`), unchanged
    - At `accept_grace_s=0.0` + `drain_window_s=None` the new wait collapses to the current immediate
      transition; adds ≤ ~15 lines, keeps `drain` under the 120-line gate, holds the injected-clock
      discipline, touches neither the frozen `DrainReport`/`DeclaredTermination` values nor the
      `DrainState` enum
    - _Design: Components §4 (minimal controller change), Data Models (`DrainReport` unchanged)_
    - _Requirements: 3.2, 3.3, 3.4, 3.5_

  - [x]* 2.2 Write property test for the extended drain termination set + duration clamp (PBT)
    - `tests/admit/test_b3_drain_extension.py` — **Property 2: Drain declares a terminal frame for
      exactly the unfinished streams, never truncates, and the declared max duration is covered by
      the window**
    - **Validates: Requirements 3.4, 3.5, 3.8**; seeded `random.Random` ≥10,000 iters, no hypothesis,
      INJECTED `sleep`/clock for determinism (async via `asyncio.run`): over random
      finished/unfinished in-flight sets and random `accept_grace_s ≥ 0` / `drain_window_s > 0`,
      `drain(...)` emits exactly one `DeclaredTermination(truncated=False)` per unfinished stream and
      none for a finished stream, and `min(contract.max_stream_duration_s(), drain_s) ≤ drain_s` holds
    - _Design: Correctness Properties → Property 2_

  - [x]* 2.3 Assert the extension is a no-op at defaults (shipped-behaviour regression)
    - `tests/admit/test_b3_drain_extension.py`: `drain()` called with no args walks
      `ACCEPTING → DRAINING → TERMINATING → FLUSHED → EXITED` identically to the shipped machine
      (same `DrainReport` shape, immediate transition, audit flushed); explicitly note that the full
      shipped admission suite (`tests/admit/`) must still pass unchanged because defaults are no-ops
    - _Design: Testing Strategy (unit — shipped drain-state walk R3.2/3.3/3.9), Minimal controller change_
    - _Requirements: 3.2, 3.3, 3.9_
  - Run the gate over `admit/admission.py` AND the full shipped `tests/admit/` suite (must stay green)
    before building on it.

- [x] 3. The serving host — `edge/serve.py` (NEW)
  - [x] 3.1 Implement `build_boot_dependencies` + the Fail_Closed_Fallback table
    - Create `gateway_v2/gateway_v2/edge/serve.py`;
      `build_boot_dependencies(*, contract, admission) -> tuple[dict[str, object], tuple[str, ...]]`
      returns the `build_app` kwargs with every unwired dependency set to its SHIPPED fail-closed
      default (the full table in the design: `provider=StubProviderClient`,
      `catalog=empty_catalog_source`, `resolver`/`scanner`/`readiness`/`state_view`/`active_streams`/
      `control`/`error_scanner`/`input_detector`/`identity`/`snapshot_source`/`admission`/`quota`/
      `exports` to their shipped defaults; `contract` real via `load_contract`; `clock=time.monotonic`)
      and a tuple of boot log lines, ONE per fallback naming the dependency + its fallback
    - Invent NO new fail-closed types — name the shipped defaults already defined in `edge/app.py` /
      `edge/chat_deps.py`; do NOT construct a real upstream client or a real catalog backend
    - _Design: Components §2b (boot deps + fallback table)_
    - _Requirements: 1.1, 1.4, 1.5, 1.6, 1.7_

  - [x] 3.2 Implement `build_uvicorn_config` (factory mode, modern loop, no deprecated options)
    - `build_uvicorn_config(app_factory, *, cfg: ServingConfig, workers: int) -> uvicorn.Config`:
      pass the app as a zero-arg FACTORY (`uvicorn.Config(app=app_factory, factory=True, ...)`) so
      each OS worker builds its own `Served_App` (per-worker isolation, C38); `loop="auto"`,
      `http="auto"`, `workers=workers` (from `contract.workers()`), `host=cfg.host`, `port=cfg.port`,
      `timeout_keep_alive=int(cfg.conn_max_age_s)`
    - Do NOT use `--worker-class uvicorn.workers.UvicornWorker` (deprecated Gunicorn worker class) and
      do NOT pass `--worker-connections` (inert); `uvicorn` is lazy-imported inside `main` (§3.4), not
      at module load, with a `pip install 'gateway-v2[serve]'` hint on `ImportError`
    - _Design: Components §2a (uvicorn host wiring)_
    - _Requirements: 1.2, 1.3, 2.1, 2.2, 2.3, 3.10, 6.4_

  - [x] 3.3 Implement `install_drain_hook` (SIGTERM → GW19 drain, two-phase + max-duration clamp)
    - `install_drain_hook(server, *, controller: AdmissionController, cfg: ServingConfig) -> None`:
      register the drain as part of uvicorn's SIGTERM-driven `lifespan.shutdown` so on SIGTERM
      `serve.py` `await controller.drain(accept_grace_s=cfg.drain_accept_s,
      drain_window_s=cfg.drain_s)` runs and only then lets uvicorn complete `lifespan.shutdown`
      (which releases the `ScanExecutor`, already wired in `build_app`)
    - Clamp the declared max stream duration to
      `min(contract.max_stream_duration_s(), cfg.drain_s)` and thread it into the serving unit so
      every in-flight stream has a deadline covered by the Drain_Window (G-02 second clause); the
      clamp holds NO capacity literal (both operands from existing authority)
    - _Design: Components §4 (SIGTERM → drain), §2a (lifespan)_
    - _Requirements: 3.1, 3.6, 3.7, 3.8, 3.9_

  - [x] 3.4 Implement `main(argv)` — orchestrate boot, refuse-to-start, run the server
    - `main(argv: list[str] | None = None) -> int`: load `ServingConfig` via `load_serving_config`
      and `ResourceContract` via `load_contract` ONCE (write both log tuples + the boot-deps fallback
      lines to the boot log); build boot deps; call `build_app(**deps)` wrapped in try/except — on ANY
      exception write `{"error": str(exc), "refuse_start": true}` to stderr and return exit code 2
      BEFORE constructing or running uvicorn (mirrors `run_cli` refuse-to-start); else lazy-import
      uvicorn, build the config (§3.2), install the drain hook (§3.3), and run the server
    - Reuse the shipped `install_sighup(runtime, …)` so SIGHUP still reloads the `ResourceContract`;
      keep every function ≤ 120 lines and the module ≤ 800
    - _Design: Components §2 (`main`), Boot sequence, Error Handling (`build_app` raises)_
    - _Requirements: 1.1, 1.2, 1.8_

  - [x]* 3.5 Write boot-deps + uvicorn-config + refuse-to-start unit tests
    - `tests/edge/test_b3_serve.py`: each unwired kwarg equals its shipped Fail_Closed_Fallback
      (1.4); `provider is StubProviderClient` with no real client built (1.5); catalog is
      `empty_catalog_source` and a models retrieve → `model_not_found` (1.6); one boot-log line per
      fallback naming dep + fallback (1.7); the uvicorn `Config` sets `loop="auto"`/`workers`/
      `factory=True`/`timeout_keep_alive` and does NOT reference `UvicornWorker` or
      `--worker-connections` (2.2, 2.3, 3.10); an injected raising `build_app` makes `main` return 2
      and never starts the server (1.8)
    - _Design: Testing Strategy (unit/EXAMPLE — fallback table, Config assembly, build_app-raises)_
    - _Requirements: 1.4, 1.5, 1.6, 1.7, 1.8, 2.2, 2.3, 3.10_
  - Run the gate over `edge/serve.py` (incl. `check_sizes` ≤800/≤120 and `lint-imports` 2 kept) before
    building on it.

- [x] 4. Real OS worker-supervision wiring — `edge/serve.py` (MODIFY) + `admit/supervisor.py` seam
  - [x] 4.1 Provide the real `respawn` adapter to `WorkerSupervisor`
    - In `gateway_v2/gateway_v2/edge/serve.py`, build the real `respawn: Callable[[str, int], Worker]`
      adapter closing over the serving unit's per-worker slot count (contract-derived, NO literal in
      `serve.py`) and pass it to `WorkerSupervisor`; register it as a uvicorn subprocess-lifecycle
      callback so on worker exit it (a) calls `supervisor.on_worker_exit(worker_id)` to reconcile the
      shared accounting (drop ONLY the dead worker's slots — peers untouched) and (b) mints a
      replacement `Worker` whose `slots` equal the crashed worker's slots (so `active_slots` is
      restored) with an incremented generation
    - uvicorn's multiprocess manager owns the OS mechanics; `WorkerSupervisor` owns the
      shared-concurrency accounting — keep that division; a respawn that itself fails leaves the dead
      worker isolated (slots stay out) rather than double-counting
    - _Design: Components §3 (real respawn callable), Error Handling (worker crash)_
    - _Requirements: 2.4, 2.5, 2.6, 2.7_

  - [x]* 4.2 Write isolate/respawn/peers-keep-admitting unit tests with a fake crash
    - `tests/edge/test_b3_supervisor_wiring.py`: using a FAKE `respawn` + a fake "crash" (a worker
      whose target raises — no real multiprocess at the unit layer), assert a crashed worker is
      isolated (its slots leave `active_slots`, drop == dead worker's slots) (2.4); `respawn` is
      invoked with the crashed `(id, slots)` (2.5); `live_worker_ids` for peers is unchanged across
      the event (2.6); the replacement re-enters with `replacement.slots == crashed.slots` and
      generation+1 (2.7)
    - _Design: Components §3 (Local-verifiable half), Testing Strategy (unit — WorkerSupervisor via fake respawn)_
    - _Requirements: 2.4, 2.5, 2.6, 2.7_
  - Run the gate over `edge/serve.py` + `admit/supervisor.py` before building on it.

- [x] 5. Packaging — `pyproject.toml` `serve` optional extra (MODIFY)
  - [x] 5.1 Add the `serve` optional-dependency extra and the live-TCP addopts change
    - Modify `gateway_v2/pyproject.toml`: add a NEW optional-dependency group
      `serve = ["uvicorn[standard]>=0.30"]` (pulls uvloop + httptools), mirroring exactly how the
      `conformance` extra is declared; keep `[project].dependencies` as `psycopg[binary]` + `redis`
      ONLY (base package stays framework-free)
    - Note (applied in task 7, recorded here): turning on `test_live_tcp_8300.py` requires removing
      its `--ignore=tests/openai_conformance/test_live_tcp_8300.py` line from `[tool.pytest.ini_options]
      addopts` — the test is otherwise collected and `importorskip`/env-gated on
      `AMF_CONFORMANCE_BASE_URL`
    - _Design: Components §2a (New dependency), §6 (turn on `test_live_tcp_8300.py`)_
    - _Requirements: 1.2, 5.5_
  - Run the gate (`.venv/bin/python -m pytest`, `mypy --strict`, `ruff`, `lint-imports`) after the
    packaging change to confirm the base suite is unaffected.

- [x] 6. Checkpoint — config + drain extension + host + supervision + packaging green
  - Ensure all tests pass, ask the user if questions arise. Run `.venv/bin/python -m pytest` (full
    offline suite + the B3 subset + the full shipped `tests/admit/` suite), `mypy --strict`,
    `ruff check gateway_v2 state_control lint tests`, `lint-imports` (2 contracts kept, no new edge),
    and the 6 AST gates (`check_capacity_literals`, `check_no_module_mutable`,
    `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_sizes`, `check_tenant_scale`)
    over `runtime/serving_config.py`, `admit/admission.py`, and `edge/serve.py`; confirm
    `unscanned_channels()` is still `()` and each module stays ≤ 800 lines / each function ≤ 120.

- [x] 7. Real-socket OpenAI conformance — `tests/openai_conformance/` (MODIFY/NEW)
  - [x] 7.1 Add the child-process boot fixture + stock-SDK surface exercises over the real socket
    - Add a `gateway_v2` conformance fixture that boots the Serving_Entrypoint
      (`edge/serve.py::main`) as a CHILD PROCESS on `127.0.0.1:RV_PORT` (default 8300), waits on
      `/readyz`, then points the stock OpenAI Python SDK at `http://127.0.0.1:RV_PORT/v1` with ONLY a
      base_url + API-key swap and exercises `/v1/chat/completions` (5.1) and the misc surfaces
      `/v1/embeddings`, `/v1/models`, `/v1/models/{id}`, `/v1/moderations` (5.2); also issue a `curl`
      against a served surface asserting a NON-HTML content type (5.3)
    - Assert `/readyz` 200/503-with-dep-name and `/metrics` 200 Prometheus text over the socket (4);
      a SIGTERM to the running server verifies graceful `lifespan.shutdown` + `ScanExecutor` release
      (3.1/3.9)
    - Assert SDK-visible PROTOCOL behaviour ONLY while the upstream is stubbed — auth errors, JSON
      error envelopes, content-type, surface routing — and do NOT assert stubbed-upstream response
      bytes (5.6)
    - _Design: Components §5 (/readyz+/metrics over socket), §6 (real-socket conformance), Testing Strategy_
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 5.6, 3.1, 3.9_

  - [x] 7.2 Turn on `test_live_tcp_8300.py` against `gateway_v2` + run the harness subset live
    - Un-ignore `tests/openai_conformance/test_live_tcp_8300.py` (remove its `addopts` ignore line,
      per task 5.1) and point `AMF_CONFORMANCE_BASE_URL` + `AMF_CONFORMANCE_APP=v2` at the running
      entrypoint so it asserts SDK-visible AUTHENTICATION errors and JSON (not HTML) error envelopes
      over the real socket (5.5); run the existing in-process `gateway_v2` conformance subset against
      the live socket via the harness (`tcp_base_url()`) with `AMF_CONFORMANCE_BASE_URL` set (5.4)
    - Note (carried from requirements ambiguity 1): `test_openai_sdk_compat_live_uvicorn.py` targets
      the OLD v1 gateway (`ai_mesh_gateway.main`), NOT `gateway_v2` — it is NOT in B3 scope and stays
      ignored; B3's real-socket conformance is written against `gateway_v2` `build_app` + harness only
    - _Design: Components §6 (turn on live-TCP; honest-stub boundary; v1 correction)_
    - _Requirements: 5.4, 5.5, 5.6_

  - [x]* 7.3 Real OS worker crash-and-respawn integration assertion
    - In the real-socket suite: crash a real uvicorn worker PROCESS of the running entrypoint and
      assert it is isolated + respawned end-to-end while peer workers keep serving (the OS-level
      counterpart to the unit-layer §4.2) — SDK requests keep succeeding across the event
    - _Design: Components §3 (real multi-process crash-and-respawn), Testing Strategy (real-socket)_
    - _Requirements: 2.4, 2.5, 2.6, 2.7_

- [x] 8. Deferred seams recorded as named comments — `edge/serve.py` (doc/verification task)
  - [x] 8.1 Verify each Deferred_Seam is named next to the fallback it leaves in place
    - Confirm `edge/serve.py` carries a comment next to each fallback naming the owning card and
      leaving the shipped Fail_Closed_Fallback/stub in place (nothing scaffolded): real provider
      socket → B4 / GW17-18 (`provider=StubProviderClient`, 7.1); fleet-scale overload / autoscaling
      / Poisson knee → GW20 / GW20b, gates G-01 / G-02 at fleet scale (7.2); real TLS termination +
      NLB bake-off / internet-vantage → B5 / GW16b, R2-12, gates G-10 / G-11 / G-13 (7.3);
      `/dev/shm` RemoveIPC host configuration → R2-20 host half (7.4)
    - This is a doc/verification task — a `grep` for the owning-card comments + an assertion that each
      stub/fallback is in place; NO scaffolding is added
    - _Design: Components §7 (deferred seams), Scope (category-2 local half)_
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [x] 9. Final verification gate
  - [x] 9.1 Run the full gate and map evidence to the §10.9 card format
    - From cwd `gateway_v2` with venv `gateway_v2/.venv`, run the full gate: `.venv/bin/python -m
      pytest` (full offline suite + the B3 subset + the real-socket conformance subset),
      `mypy --strict`, `ruff check gateway_v2 state_control lint tests`, `lint-imports` (2 contracts
      kept, no new edge), and the 6 AST gates (`check_capacity_literals`, `check_no_module_mutable`,
      `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_sizes`,
      `check_tenant_scale`); confirm `unscanned_channels()` is still `()` and file sizes stay ≤ 800 /
      functions ≤ 120. Fix any gate failure before closing
    - Map the evidence to §10.9 (why / implementation / live acceptance tests / exit / evidence /
      rollback): live acceptance = the real-socket SDK + `curl` + `/readyz` + `/metrics` +
      `test_live_tcp_8300.py` + SIGTERM drain report + worker crash-and-respawn transcripts; exit =
      full gate green + the two property tests; rollback = the entrypoint is additive (`edge/serve.py`,
      `runtime/serving_config.py`, the no-op-default `drain` extension, the `serve` extra), reverting
      restores the stub `app = None` with no behavioural change to any shipped module. Record the
      deferred gates (G-01/G-02 at fleet scale, B4 provider bytes, B5 TLS/NLB) as such — NOT closed here
    - _Design: Testing Strategy (Gate), Mapping to §10.9_
    - _Requirements: 1.3, 4.4, 5.4, 7.2_

- [x] 10. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks (property, unit, integration) and can be skipped
  for a faster MVP; core implementation sub-tasks are never optional. The agent MUST implement
  un-starred sub-tasks and MUST NOT implement starred ones.
- Each task references specific requirement sub-clauses and the design section/property it satisfies
  for traceability; collectively the tasks cover all 7 requirements per the design.
- Checkpoints (tasks 6, 10) ensure each layer's gates (`mypy --strict`, `ruff`, `lint-imports` 2
  contracts, the 6 AST gates incl. `check_sizes`, `unscanned_channels()` empty) are green before the
  next layer builds on it; each code task is also followed by a note to run the gate.
- Property tests use a seeded `random.Random` for ≥10,000 iterations, no hypothesis, async via
  `asyncio.run`; one property ↔ one test; tagged `# Feature: serving-entrypoint-gw-b3, Property N`
  and `# Validates: Requirements X.Y`. Only two properties exist (Property 1 config parser totality,
  Property 2 extended-drain termination/clamp invariant) — the design's prework classifies everything
  else as EXAMPLE / INTEGRATION / SMOKE, so no other property sub-tasks are added.
- B3 HOSTS the shipped `build_app` (it does not rewrite it) and DRIVES the shipped mechanisms (the
  GW19 `AdmissionController.drain` / `WorkerSupervisor`, the GW05/GW06/GW14d seams, the injected
  `ScanExecutor` lifespan). The ONLY shipped-code change is the user-approved minimal
  behaviour-preserving `drain()` extension (task 2.1), whose two optional keyword-only params +
  injected `sleep` are no-ops at defaults so every shipped admission test keeps passing.
- Capacity literals live only in `runtime/resources.py`: the default port/host/drain seconds are
  operational-config literals, the worker count comes from `ResourceContract.workers()`, and the
  declared max stream duration comes from `ResourceContract.max_stream_duration_s()`. `ServingConfig`
  is frozen+slotted with no module-level mutable; `uvicorn` is a NEW optional `serve` extra
  (lazy-imported in `main`) and the base deps stay `psycopg` + `redis` only.
- Deferred seams (task 8) — real provider socket (B4 / GW17-18), fleet-scale overload / autoscaling /
  Poisson knee (GW20 / GW20b, G-01 / G-02), real TLS + NLB bake-off (B5 / GW16b, G-10 / G-11 / G-13),
  `/dev/shm` RemoveIPC host config (R2-20 host half) — have NO implementation tasks; they stay named
  comments next to the shipped Fail_Closed_Fallback/stub, nothing scaffolded.
- Shared-file coordination: §1.2 + §1.3 both append to `tests/runtime/test_b3_serving_config.py`, and
  §2.2 + §2.3 both append to `tests/admit/test_b3_drain_extension.py` — co-waved test sub-tasks that
  append to one shared test file must coordinate on that file (append-only). Implementation sub-tasks
  that write `edge/serve.py` (3.1→3.2→3.3→3.4, then 4.1) are serialized into distinct waves to avoid
  write conflicts.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.2", "2.3", "3.1"] },
    { "id": 2, "tasks": ["3.2"] },
    { "id": 3, "tasks": ["3.3"] },
    { "id": 4, "tasks": ["3.4"] },
    { "id": 5, "tasks": ["3.5", "4.1", "5.1"] },
    { "id": 6, "tasks": ["4.2", "7.1"] },
    { "id": 7, "tasks": ["7.2", "7.3"] },
    { "id": 8, "tasks": ["8.1"] },
    { "id": 9, "tasks": ["9.1"] }
  ]
}
```
