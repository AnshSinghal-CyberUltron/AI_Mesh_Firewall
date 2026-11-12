# `regex` 2023.8.8 timeout clock: thread-CPU patch, build and evidence (plan PR #103, §5.2.2 / §5.2.4)

Scope: Reviewer B's revision-5 blocker 1 (`plan_rev5_review_B.md`). Everything here was measured on the shared
2-CPU host (Firecracker VM, Linux 6.18), Python 3.12.3 (`/usr/bin/python3.12`, Unicode 15.0.0). The load average
was 4–8 throughout (other agents' full test suites and benchmarks; each results file records the top processes).
Times are the calling thread's CPU time unless marked wall. All paths are under
`/tmp/claude-0/-home-claude/2a18a307-83e0-5467-9172-0939d20bde00/scratchpad/xlate5/clock/`.

## Recommendation

Ship the patched wheel (`regex-2023.8.8-thread-cputime.patch`, +56/−6 lines in `_regex.c`), built by
`build_patched_regex.sh`, as the F1a-1 fix for blocker 1. Keep the process-based design (§7) as the documented
fallback: switch to it if the crash fuzz / ASAN ever finds a reachable memory-safety bug in the frozen 2023.8.8,
because process isolation is then needed regardless of the clock.

- **It fixes the blocker.** Org B's pass at about 57% of its budget was refused 0/80 times with T−1 = 0/1/3/7
  org-A threads burning. Stock refused 7/20 at T = 2, 20/20 at T = 4 and 20/20 at T = 8. A timeout now fires within
  +0.5 ms of its value in the matching thread's CPU, whatever else runs. Stock fired at timeout ÷ (busy threads + 1).
- **It costs nothing measurable.** One clock read costs 204–242 ns, against 216–279 ns for upstream's `clock()`.
  Over perf5's 30 pattern-text pairs, patched/unpatched (same pinned build) is 0.96. Upstream's 100 tests and the
  existing exactness suites pass (§5).
- **It is reproducible.** Hash-pinned sdist, patch and build backend. Three builds at two paths gave the same wheel
  sha256, `8ca61476…`.

Two plan changes are needed even with the patch (§8): the GIL convoy (wall time) and thread-CPU inflation under
host contention (headroom).

## 1. How the timeout works in stock `_regex.c` (sdist sha256 `fcbdc5f2…7712e`, verified)

The sdist was fetched by URL from the PyPI JSON API and its hash checked. `pip download --no-binary :all:` was not
used because it runs the sdist's build backend to prepare metadata, which is not "data only". Every use of
`clock()`, `CLOCKS_PER_SEC` and timeout arithmetic:

| `_regex.c` line | What |
|---|---|
| 86–90 | `RE_NO_TIMEOUT -1`, `RE_BAD_TIMEOUT -2`; "values are in clock ticks" |
| 517–518 | `RE_State.timeout` (`Py_ssize_t`, ticks), `RE_State.start_time` (`clock_t`) |
| **2211** | `check_timed_out`: `if ((Py_ssize_t)(clock() - state->start_time) < state->timeout) return FALSE;` otherwise `set_error(RE_ERROR_TIMED_OUT)` → `TimeoutError("regex timed out")` (2106–2107) |
| 2221–2234 | `safe_check_cancel`: **acquire the GIL**, `PyErr_CheckSignals()`, then `check_timed_out`, **release the GIL** |
| **18336–18337** | `state_init`: `state->timeout = timeout; state->start_time = timeout == RE_NO_TIMEOUT ? 0 : clock();` |
| **20779–20792** | `decode_timeout`: `None` → no timeout; non-float → `ValueError`; **20791** `value >= 0.0 ? (Py_ssize_t)(value * CLOCKS_PER_SEC) : RE_NO_TIMEOUT` |
| 20830, 21317, 21410, 21958, 21989, 22020, 22051, 22094, 22271 | `decode_timeout` callers: `scanner`/`finditer`, `splititer`, `search`/`match`/`fullmatch`, `sub`/`subf`/`subn`/`subfn`, `split`, `findall` |

How the timeout is checked:

- **Unit.** Ticks of `CLOCKS_PER_SEC` (POSIX: 1,000,000, so microseconds). C `clock()` is process CPU time on glibc
  (`CLOCK_PROCESS_CPUTIME_ID`), summed over every thread: this is the blocker.
- **When the clock starts.** In `state_init`, when the call or iterator is created. For `finditer` it covers the
  iterator's whole life, including Python code that runs between yields.
- **How often it is checked.** `RE_State.iterations` is a `RE_UINT16` incremented by `0x100`, so it wraps every
  256 steps. It is checked:
  - in the matcher's main loop (11747–11750);
  - in its backtrack loop (15033–15036);
  - at `start_match` when the counter is 0 (11611; `init_match` zeroes it at 3355, once per match call).

  Search-start prefilters and `match_many_*` runs do not count iterations, so a span between checks can be one
  linear scan of the text. Measured overshoot is at most +1 ms.
- **On expiry.** `TimeoutError("regex timed out")` is raised out of `search` or out of the iterator's `next()`.
- **GIL and `concurrent=`.** `do_match` releases the GIL for the whole match (17951) and re-acquires it at the end
  (18016) when `state->is_multithreaded` is set. That is `concurrent=True`, or `concurrent=None` (the default) on a
  `str`/`bytes` (18322–18333); `concurrent=False` holds the GIL throughout.
  - **But every check re-acquires the GIL** (2224), to call `PyErr_CheckSignals()`, even with `timeout=None`. So a
    "GIL-free" scan needs the GIL every 256 iterations (§4.4).
  - The prototype passes `concurrent=True` on every timed call (`incremental.py:53,55`). Its untimed stopper scan
    (`incremental.py:45`) uses the default, which is also multithreaded for `str`.
- **Upstream has not changed this.** The latest release, 2026.11.9 (sdist sha256 `316a70fb…6277`, published
  2026-11-09, checked as data in `upstream-latest/`), still uses `clock()` (lines 2241 and 18544, and
  `CLOCKS_PER_SEC` at 21083). Its `safe_check_cancel` still takes the GIL on every check. There is no upstream fix
  to move to, and moving would lose exact `\w` anyway (§5.2.2).
- **Edge cases measured on stock** (`probes/edge_timeouts.py`):
  - `timeout=NaN` means no timeout: `NaN >= 0.0` is false, so it falls to `RE_NO_TIMEOUT`.
  - `timeout=-1` means no timeout (the plan already guards this).
  - `timeout=inf`, `1e300` and `1e13`, anything at or above about 9.22e12 s, **time out at once**: the
    double-to-`Py_ssize_t` conversion is undefined and yields `INT64_MIN` on x86-64.

## 2. The patch (`regex-2023.8.8-thread-cputime.patch`, sha256 `cb544353…3cd7d`)

- **Clock.** `thread_cpu_ticks()` = `clock_gettime(CLOCK_THREAD_CPUTIME_ID)` in microseconds, the same unit as
  before, so `timeout=` keeps its meaning. It is used at the two former `clock()` sites. `start_time` becomes
  `int64_t`, so there is no 32-bit `clock_t` wrap.
- **Platform.** `#error` unless `CLOCK_THREAD_CPUTIME_ID` is defined. That is POSIX: Linux, and macOS 10.12 or
  later. Windows is unsupported and fails to build, rather than silently falling back to process time.
- **Fail-closed rules in `check_timed_out`.**
  - A thread's CPU clock means nothing on another thread. So a timed iterator advanced on a thread other than the
    one that created it raises `TimeoutError("regex timed out (timeout started on another thread)")` at its first
    check (`probes/migrate.py`). Untimed iterators are unaffected, and the prototype never migrates one.
  - An unreadable clock, or one that reads earlier than its start, also counts as timed out.
- **`decode_timeout`.**
  - NaN now raises `ValueError` (it used to mean no limit).
  - Huge and infinite values clamp to `PY_SSIZE_T_MAX` (they used to fire at once).
  - **Negative still means no timeout, as upstream**, so the plan's "t ≤ 0 → refuse without calling" guard stays
    required.
- **Upstream tests.** `regex.test_regex` passes on both the pinned unpatched build and the patched build (100
  tests). It also passes inside `python@sha256:5e2bdb1b…` (Python 3.12.16), with `--network none`.

## 3. Hash-pinned, reproducible build (`build_patched_regex.sh`)

The script:

1. verifies the sdist sha256 and the patch sha256;
2. applies the patch with `--fuzz=0`;
3. creates an isolated venv on `/usr/bin/python3.12` (3.12.3, the interpreter `rv2023.8.8` uses);
4. installs `setuptools==80.9.0` with `--require-hashes --only-binary :all:` (`build-requirements.txt`);
5. calls `setuptools.build_meta.build_wheel` directly: no frontend, no build isolation, no network.

Options: `NOPATCH=1` builds an unpatched control wheel; `ASAN=1` builds the sanitizer variant.

**Reproducibility, at `-O2 -g` (17 s of CPU per build):**

| Builds | Result |
|---|---|
| Default (`REPRO=1`: `SOURCE_DATE_EPOCH=1691527080`, the sdist's newest mtime, plus `-ffile-prefix-map=<src>=/usr/src/regex-2023.8.8`), two paths and a rebuild | **bit-identical**: wheel `8ca6147610412de1a99bef3ac300d603698c394398d1c9fb0ed31035d34a36da`, `.so` `64a8bac6…` |
| Unpatched control (`NOPATCH=1`), twice | bit-identical: `5bea7ab3…c8790` |
| `SOURCE_DATE_EPOCH` only, same path rebuilt | identical; across two paths it differs |
| Neither (`REPRO=0`) | differs on every build |

Two things break reproducibility, and each knob fixes one:

- **Zip timestamps.** The `.so` and the dist-info entries take the build time; the `.py` entries take the sdist's
  mtimes. `SOURCE_DATE_EPOCH` fixes this.
- **The absolute build directory in the `.so`'s DWARF** (`-g`). `-ffile-prefix-map` fixes this.

Bit-reproducibility holds for one toolchain (here gcc 13.3.0, glibc 2.39, CPython 3.12.3 headers). A Debian builder
image gives a different, but again stable, hash. CI must record its own hash and fail when a rebuild differs.

**Pitfalls found (relevant to the plan's builder stage):**

1. **Any `CFLAGS` in the environment, even an empty one, replaces the interpreter's `-DNDEBUG -g -O2 -Wall`.**
   setuptools' distutils does `cflags = os.environ.get('CFLAGS', cflags)`. The wheel then builds at `-O0` with
   `assert()` live. My first builds did exactly this. The script now prepends the interpreter's `CFLAGS`, never
   exports an empty one, and fails if the compile line lacks `-O2` or `-DNDEBUG`.
2. **`--require-hashes` does not cover the build backend under pip's build isolation.**
   `pip wheel --require-hashes -r req.txt`, with the sdist hash-pinned, installed the latest setuptools (84.2.1)
   into the isolated build environment with no hash check (`build/pipiso/pip.log`). The stock `rv2023.8.8` wheel
   was built this way (`WHEEL`: `Generator: setuptools (84.2.1)`). Pin the backend with a hash and build with no
   isolation, or call the hook, as the script does.
3. `-Wall` gives one `-Warray-bounds` warning, in upstream code (`pattern_findall`, via `PyUnicode_GET_LENGTH`). The
   unpatched source gives the same.

**Builder image.**

- `docker manifest inspect python:3.12-slim` failed: Docker Hub returned `toomanyrequests` (unauthenticated pull
  rate limit), so the registry could not be queried.
- The image already in the local store is `python@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b`
  (amd64, created 2026-11-05, Python 3.12.16, Debian 13.7, **no gcc**). It is recorded from the local store,
  **not verified against the registry**.
- The runtime image has no compiler, so the builder stage needs a pinned image that has one (or a pinned
  `apt-get install gcc` from a snapshot mirror); its digest belongs next to the wheel hash. That image was not
  built here.

## 4. Measurements (stock = `rv2023.8.8` venv; patched = `venv-tcpu`)

### 4.1 Reviewer B's probes, verbatim (`probes/b/`, with B's translator snapshot; `results/b_probes_*.txt`)

| Probe | Stock | Patched |
|---|---|---|
| `clock_semantics.py`: B with timeout=100 ms while 0/1/2/3 org-A threads burn | 0, 0, 1 and **5/5** TimeoutError | 0, 0, 0 and **0/5** |
| `clock_t2.py`: T = 2, org A on 1 thread | **8/10** refused, at about 50 ms of B's CPU | **0/10** |
| `clock_pass.py`: the plan's per-pass budget with A on 0/1/3 threads | 0, 2 and **10/10** refused (refused at 22–55 ms) | 0, 0 and **0/10** |

### 4.2 Overshoot (`probes/overshoot_mt.py`; `results/overshoot_*.txt`; 7 runs per row)

The timeout fires at the matching thread's CPU shown below. The pattern is cubic and never finishes.

| Other busy regex threads | Timeout | Stock: own CPU at fire, median | Patched: own CPU at fire, median (range) | Patched wall, median |
|---|---|---|---|---|
| 0 | 10 ms | 10.2 | 10.2 (10.0–10.5) | 22 ms |
| 0 | 50 ms | 50.3 | 50.2 (50.1–50.5) | 128 ms |
| 0 | 100 ms | 100.4 | 100.4 (100.1–101.0) | 185 ms |
| 0 | 400 ms | 400.1 | 400.2 (400.1–400.9) | 511 ms |
| 1 | 100 ms | **51.3** | 100.5 (100.1–100.8) | 208 ms |
| 1 | 400 ms | **208.6** | 400.4 (400.1–400.6) | 1,335 ms |
| 3 | 100 ms | **26.6** | 100.2 (100.1–100.8) | 814 ms |
| 3 | 400 ms | **101.3** | 400.2 (400.1–400.8) | 2,100 ms |

The patched overshoot is at most +0.5 ms of own CPU (one check interval). Wall time is host oversubscription: up to
four busy threads plus other agents' work on 2 CPUs.

### 4.3 Cross-tenant test (`probes/cross_tenant.py PASSES K LENGTH`; `results/cross_tenant_*_k{3,4}.txt`)

The setup follows plan §5.2.4/§5.2.5:

- Org A holds T−1 scan threads, each running pathological passes back to back, every one refused at its own
  100 ms budget.
- Org B's pass is K translated rules over 128 KiB of unpunctuated text (the stopper-free worst case). It has one
  100 ms budget shared by its rules, debited with `thread_time()`, with `timeout=remaining` on each call.
- 20 passes per row. Both wheels get identical inputs.

**K = 3: B's pass alone costs about 57% of its budget (56.8 ms when calibrated).**

| T | Org A threads | Stock: B refused | Stock: B's own CPU per pass, median (a refused pass stops early) | Patched: B refused | Patched: B's own CPU, median / max | Patched: B wall, median |
|---|---|---|---|---|---|---|
| B alone | 0 | 0/20 | — | 0/20 | 81.7 / 90.0 ms | 193 ms |
| 2 | 1 | **7/20** | 75.9 ms | **0/20** | 62.6 / 85.2 | 352 ms |
| 4 | 3 | **20/20** | 23.1 ms | **0/20** | 56.5 / 78.1 | 518 ms |
| 8 | 7 | **20/20** | 11.9 ms | **0/20** | 68.1 / 85.4 | 1,235 ms |

In the patched runs, org A's own passes were refused at 100.3–100.4 ms of their CPU, exactly their budget. On
stock they were refused at 45.5, 26.7 and 14.0 ms, so stock also shrinks a tenant's budget with its own other
threads.

**K = 4: B's pass costs about 80% of its budget alone.**

- Stock refused B 0, 10, 20 and 20 of 20 (B alone, then T = 2, 4, 8).
- Patched refused 2, 3, 1 and 2 of 20. The rate does not depend on org A: the 2/20 is with B alone. These refusals
  are the pass's own thread-CPU variance (§4.7), not a cross-tenant effect.

### 4.4 GIL: blocked threads accrue no CPU, but scans are coupled to the loop (`probes/gil_probe.py 384 5`; `results/gil_*.txt`)

Setup: thread H is a pure-Python busy loop, which holds the GIL (a stand-in for a CPU-busy event loop). Thread M
runs one translated rule over 384 KiB with `timeout=0.1`. 5 runs per row; default switch interval 5 ms.

| H | `concurrent=` | Stock: M outcome | Stock: M own CPU, median | Patched: M outcome | Patched: M own CPU, median | Patched: M wall, median | Process CPU during M, median (patched run) |
|---|---|---|---|---|---|---|---|
| idle | True | 5 ok | 53.9 ms | 5 ok | 60.6 ms | 76 ms | 61 ms |
| busy | True | **5 timeout** | 33.3 | 5 ok | 79.4 | **964** | 434 ms |
| busy | None (default) | **5 timeout** | 31.7 | 5 ok | 82.7 | 751 | 289 ms |
| busy | False | 5 ok | 67.5 | 5 ok | 69.1 | 219 | 78 ms |
| busy, switch interval 1 ms | True | **5 timeout** | 40.8 | 5 ok | 54.0 | 273 | 137 ms |

What this shows:

- **The GIL wait accrues no thread CPU.** With H busy, M's wall time grew about 12×, but its own CPU stayed in the
  same range as with H idle. Stock counts H's CPU as M's, so it refused M at 31–43 ms of M's work.
- **`concurrent=True` releases the GIL, but the matcher needs it back every 256 iterations** (`safe_check_cancel`).
  While the loop runs Python, each re-acquisition waits up to one switch interval. A pass of about 80 ms of CPU
  took about 1 s of wall time at 5 ms, and 273 ms at 1 ms.
- `concurrent=False` avoids the wait, but then holds the GIL for the whole scan, which stalls the loop. That is
  what the plan avoids.

### 4.5 Cost of the clock read (`probes/clock_cost.c`; `results/clock_cost.txt`; ns per call, best of 5 × 400k)

| Call | Alone | +3 busy threads | +7 busy threads |
|---|---|---|---|
| `clock()` (upstream) | 216 | 237 | 279 |
| `clock_gettime(CLOCK_THREAD_CPUTIME_ID)` (patch) | 204 | 230 | 242 |
| `clock_gettime(CLOCK_PROCESS_CPUTIME_ID)` | 222 | 236 | 254 |
| `pthread_self()` (the patch's thread check) | 2 | 2 | 2 |
| `clock_gettime(CLOCK_MONOTONIC)` (vDSO, for reference) | 25 | 26 | 28 |

Both CPU clocks are syscalls; the thread clock is never dearer. A read happens at most once per 256 matcher
iterations, and only when a timeout is set.

### 4.6 Match speed (`probes/match_speed.py`, perf5 §4 verbatim; `results/match_speed.md`; best of 5 per run, min of 3 rounds)

Over perf5's 15 patterns × {prose, code} at 32 KiB, with `timeout=5, concurrent=True` so every check reads the
clock:

- Stock: 20.41 ms.
- Pinned unpatched control: 17.65 ms.
- Patched: **16.95 ms (patched / control = 0.96)**.

No single pattern is slower beyond noise (worst 1.07×, on patterns taking 0.02–0.3 ms). Check-heavy cubic search
(almost all matcher iterations): 54.18 ms with no timeout and 55.79 ms with a timeout, patched, against 54.77 and
54.95 ms on the control. Clock reads cost at most about 3% in the worst case, within this host's noise.

### 4.7 Thread CPU of a fixed pass on this host (`probes/cpu_variance.py`; `results/cpu_variance.txt`)

B's 3-rule, 128 KiB pass, single thread, no timeout, 60 runs, twice:

- min 48.5, p50 56.8, p95 79.8, max 80.6 ms;
- min 48.6, p50 67.8, p95 80.3, max 82.1 ms.

Max/min is about 1.7. Steal time is accounted by the kernel (about 1% at the time). The rest is shared-cache, SMT
and frequency contention from other tenants, which thread CPU cannot remove. This explains the K = 4 refusals in
§4.3.

## 5. Exactness suites on the patched wheel

EXACTNESS_PLACEHOLDER

## 6. ASAN + UBSan crash fuzz

ASAN_PLACEHOLDER

## 7. Process-based alternative (`probes/proc_alt.py`; `results/proc_alt.txt`)

The alternative runs each off-loop pass in a worker process. Inside a single-threaded worker, stock `clock()`
equals the scan's CPU, so no fork of `regex` is needed.

| Cost | 128 KiB | 512 KiB |
|---|---|---|
| IPC round trip, Pipe + pickle (the worker does no scanning), ASCII text: wall median / p90 | 0.06 / 0.09 ms | 0.43 / 0.74 ms |
| Same, non-ASCII text | 0.25 / 1.53 ms | 1.50 / 4.00 ms |
| Sender thread CPU (Pipe + pickle) | 0.03–0.04 ms | 0.17–0.22 ms |
| Worker CPU to receive (unpickle) | 0.03–0.20 ms | 0.22–1.22 ms |
| SharedMemory (UTF-8 copy into a pre-allocated block), wall median | 0.05–0.35 ms | 0.24–1.26 ms |

| Per-worker memory (VmRSS, fresh spawned worker) | |
|---|---|
| Interpreter + `regex` + translator | 21.7 MiB |
| Plus a compiled 64-rule realistic DLP plan | +0.4 MiB |
| Plus a compiled 64-rule plan of compile-heavy shapes at the limits (`(?:\b\w{3}\b ){250}`-style) | +65.9 MiB |
| Worker start to first answer | fork 2–7 ms; spawn 65–100 ms |

**Comparison:**

- **Transport is cheap.** Per pass it is at most 1.5 ms (median) of wall, against a 100–400 ms budget.
- **Memory and compile multiply.** Every worker that may serve an org holds its own compiled cache and pays its
  own compile. With T workers that is T × (22 MiB + the cache) and T × the compile CPU, unless orgs are pinned to
  workers. Pinning brings back head-of-line blocking, which the DRR scheduler exists to avoid.
- **What processes buy:**
  - crash containment, which matters for a frozen release with unconfirmed memory-safety fixes (#611/#612);
  - a hard kill that does not depend on the engine's check granularity;
  - no GIL coupling with the loop (§4.4);
  - no fork of `regex`.
- **What they cost:** respawn and cold caches after a kill, IPC code, and the memory above.

On balance the in-process patch is cheaper and simpler for F1a-1. Process isolation is the right next step if the
crash fuzz ever finds a reachable crash.

## 8. What contradicts the plan, and what to change

1. **§5.2.4, "`regex`'s timeout counts the matching thread's CPU time across a whole `finditer`".**
   - False for stock 2023.8.8: it counts process CPU (§1, §4.1–4.3).
   - True only with this patch, and then only on the creating thread; a migrated timed iterator fails closed.
   - Record the fork in §5.2.2: the patch file, its sha256, and the CI evidence on the patched wheel.
2. **§5.2.4, "`concurrent=True`, which releases the GIL".**
   - Releases it, yes. But the matcher re-acquires it every 256 iterations (`_regex.c:2224`), with or without a
     timeout.
   - So the wall time of an off-loop pass depends on how much Python the loop is running: ×12 at a 5 ms switch
     interval (§4.4).
   - CPU accounting is correct with the patch; wall latency is not bounded by the CPU budget. The DRR bound (§5.2.5)
     is in CPU. The plan should say that a pass's wall time also depends on the loop's Python duty cycle and the
     switch interval: measured ×12 at 5 ms and ×3.6 at 1 ms, for a pure-Python-busy holder. The hand-off is more
     lenient than "one switch interval per check", which would predict far more; treat these as measurements, not
     a bound.
   - Options, not built here:
     - a second patch that skips the GIL in the periodic check on non-main threads (only the main thread handles
       signals; take the GIL only to raise the `TimeoutError`);
     - a smaller `sys.setswitchinterval`;
     - processes (§7).
3. **§5.2.4, "Wall time under host oversubscription is a capacity matter".** Thread CPU itself inflates by up to
   1.7× under host contention here (§4.7).
   - Requirement 4 (no false refusals) therefore needs explicit headroom: an ordinary pass should cost at most
     about 55% of its budget on a quiet host.
   - At about 57% the patched wheel refused 0/80; at about 80% it refused 8/80, all from B's own variance.
   - Reviewer B's 3-rule, 128 KiB pass (53–63 ms against 100 ms) is at that edge.
4. **§5.2.4, "When the computed `t` is ≤ 0 the scan refuses".**
   - Still required: negative still means no timeout.
   - On the stock wheel, also guard `math.isfinite(t)`: NaN means no limit, and inf or anything at or above about
     9.22e12 s fires at once. The patch fixes both.
5. **§5.2.2, the build.**
   - "fetched under `--require-hashes`" does not pin the build backend under pip's default build isolation (§3,
     pitfall 2).
   - A `CFLAGS` environment variable silently drops `-O2 -DNDEBUG` (§3, pitfall 1).
   - "CI caches the two wheels and records their sha256" can be strengthened to "CI rebuilds and requires the same
     sha256", because the build is bit-reproducible on a pinned toolchain.
   - The local `python:3.12-slim` is 3.12.16; the plan's evidence is 3.12.3. Both report Unicode 15.0.0, and the
     patched wheel's upstream tests pass on 3.12.16.
6. **§5.2.2, the boot check cannot tell the patched wheel from stock.** Both report
   `importlib.metadata.version("regex") == "2023.8.8"`. A stock wheel from a stale cache would silently bring the
   cross-tenant refusals back.
   - Add a check that the extension's sha256 (`regex._regex.__file__`) equals the CI-recorded hash for that
     architecture.
   - Add a cheap functional marker: `regex.compile("a").search("a", timeout=float("nan"))` raises `ValueError`
     only on the patched build (`results/edge_*.txt`).
   - Make the cross-tenant test (§4.3) a CI test on the built wheel.
7. **§5.2.2 / §5.8, ASAN.** An ASAN wheel loads into the stock CPython only with
   `LD_PRELOAD=$(gcc -print-file-name=libasan.so)` and `ASAN_OPTIONS=detect_leaks=0`. Without the preload it aborts
   with "ASan runtime does not come first".
   - It also needs `PYTHONMALLOC=malloc`. Otherwise `regex`'s small `PyMem_Malloc` blocks come from pymalloc
     arenas, where ASAN has no redzones and cannot see an overflow.
   - `python -I` ignores `PYTHONMALLOC`, so the fuzz children must not use `-I`.
   - The CI job should include the positive control used here: an out-of-bounds `memset` that must be reported.

Plan lines that rest on the old clock claim (in `docs/plans/2026-11-11-f1-production-serving.md` at `e2c3e2cf`):

- 203–204: the build;
- 217–218: crash fuzz and ASAN;
- 271–274: the budget unit and the calls;
- 403–407: the tests (add the cross-tenant test);
- 588: the risk row (the pin is now a patched fork);
- 664: the B-B2 disposition ("thread-CPU accounting").

## Files

| Path (under `clock/`) | What |
|---|---|
| `regex-2023.8.8-thread-cputime.patch` | The patch (apply with `patch -p1` in the sdist root) |
| `build_patched_regex.sh`, `build-requirements.txt` | Hash-pinned reproducible build (`REPRO`, `ASAN`, `NOPATCH`) |
| `sdist/regex-2023.8.8.tar.gz`, `pypi_regex_2023.8.8.json` | The verified sdist and its PyPI metadata |
| `build/p1/dist/*.whl` (`WHEEL_SHA256`) | The patched wheel, `8ca61476…` |
| `build/s1/dist/*.whl` | Unpatched control wheel, `5bea7ab3…` |
| `build/asan/dist/*.whl` | ASAN + UBSan wheel (fuzz only) |
| `venv-tcpu/`, `venv-stockpin/`, `venv-asan/` | Venvs (installed with `--no-index --require-hashes`) |
| `run_suite.sh`, `run-<n>/` | Parameterised copy of `run_r5b.sh`; each run is a sha256-recorded snapshot of the translator and suites, with results |
| `probes/` | All probes; `probes/b/` is Reviewer B's three, verbatim, with B's translator snapshot |
| `results/` | Every table's raw output, with the host load at the time |
