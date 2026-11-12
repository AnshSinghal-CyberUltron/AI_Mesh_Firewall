# Revision-6 re-run on the 12 Nov host (plan PR #103)

The 11 Nov host stopped before the revision-6 regression (`r6_done.txt` absent), the patched-wheel exactness run and the ASAN/fuzz runs (`clock/results/asan-fuzz/QUEUED.txt`) finished. Nothing from those runs is cited. Everything below was re-run on the 12 Nov host. The 11 Nov WIP copy of this directory is on branch `wip/handoff-2026-11-12` (`4f13403b`, `docs/plans/evidence/2026-11-12-f1-plan-rev6-wip/`); this directory is that copy plus the files listed under "Changed or added on 12 Nov".

## Host and builds

- Host: a 2-CPU VM shared by about 20 agents; 7.8 GiB RAM; Linux; gcc 13.3.0, glibc 2.39; Python 3.12.3 (`/usr/bin/python3.12`, Unicode 15.0.0). Load average 1.5–4 during the runs; each step's start load is in `r6_progress.txt` and `clock/results/host2_progress.txt`, and the busiest processes at the end in `r6_top_at_end.txt` and `clock/results/host2_top_at_end.txt` (other agents' review harnesses, control test runs and benchmarks for PRs #115, #117, #121 and #122).
- Heavy jobs ran under `flock /home/claude/work/.heavy.lock nice -n 10`, so no other heavy job ran at the same time; light jobs (other agents' editors, servers and harnesses) still did.
- **Wheels** (`clock/build_patched_regex.sh`; PyPI has no cp312 wheel for 2023.8.8, so every `regex` here is built from the hash-pinned sdist):

  | Build | Command | Wheel sha256 | `_regex` `.so` sha256 | Same as 11 Nov? |
  |---|---|---|---|---|
  | Unpatched control ("stock" below) | `NOPATCH=1 ./build_patched_regex.sh build/s1` (19 s wall) | `5bea7ab3d41fff15fcc1b100d488556cb7dce4d60d0f0c05515088f1cca8c790` | `d07e53d1a45cab4b…` | **yes, bit-identical** |
  | Patched (thread CPU) | `./build_patched_regex.sh build/p1`, and again at a second path (`…/altpath/p2`) | `8ca6147610412de1a99bef3ac300d603698c394398d1c9fb0ed31035d34a36da` (both) | `64a8bac6ca333786…` | **yes, bit-identical** |
  | Patched, rebuilt after adding the `EXTRA_PATCH` option to the script | `./build_patched_regex.sh build/p1check` | `8ca61476…36da` | — | yes |
  | ASAN + UBSan (fuzz only) | `ASAN=1 ./build_patched_regex.sh build/asan` | `fa671fa5b0de2ccc11acc47a0d4ed99c0f307be65ca6e81f5a02e764077cc5ad` | `a0095ad84c8d587c…` | (not recorded on 11 Nov) |
  | PROTOTYPE: patched + GIL-free periodic check (not shipped) | `EXTRA_PATCH=clock/regex-2023.8.8-nogil-check.prototype.patch EXTRA_PATCH_SHA256=7ec3e5af… ./build_patched_regex.sh build/p2g` | `435d3bfedea709d5104414ea46dc3bb4971d9f1cd6348bfd252b1d7a2b309e4f` | — | new |

  Each venv was installed with `pip install --no-index --require-hashes --no-deps -r <venv>.req` from the built wheel. Functional marker (`clock/probes/semantics_check.py`): `regex.compile("a").search("a", timeout=float("nan"))` is accepted by the unpatched build and raises `ValueError` on both patched builds; `timeout=inf` times out at once on the unpatched build and not on the patched ones.

## Runs

| # | What | Command | Wall | Result |
|---|---|---|---|---|
| 1 | Revision-6 regression and measurements, unpatched wheel | `P=venv-stock/bin/python flock …heavy.lock nice -n 10 ./run_r6.sh` | 2,090 s (04:53–05:27 UTC), lock held throughout | All exactness steps 0 mismatches; `r6_done.txt` written. Per step below |
| 2 | Exactness suites on the **patched** wheel | `SUITE=r6 flock …heavy.lock nice -n 10 clock/run_suite.sh 2 clock/venv-tcpu/bin/python` | 1,364 s (06:11–06:34 UTC) | Every exactness suite 0 mismatches, identical verdicts to run 1. A first attempt at 06:00 ran on the base interpreter (no `regex`; every step failed at import) because `run_suite.sh` resolved the venv symlink with `readlink -f`; fixed (the script now keeps the venv path and checks `import regex` first), and its `META.txt` is kept in `clock/results/failed-run-2-20261112/` |
| 3 | Probes on this host (B-1 on both wheels, the convoy, thread-CPU variance, A-B1, the inline refill comparison, the prototype) | `flock …heavy.lock nice -n 10 clock/run_host2.sh` | 219 s (06:00–06:04 UTC) | All steps exit 0; see below |
| 4 | ASAN + UBSan crash fuzz, patched wheel, revision-6 translator | `cd clock/probes && XLATE_SNAP=<this dir> flock …heavy.lock nice -n 10 clock/venv-asan/bin/python asan_fuzz.py clock/results/asan-fuzz-host2 1800 2` | 1,804 s (06:35:30–07:05:34 UTC) | 0 child deaths, 0 sanitizer reports; positive control reported. Below |

### Run 1, per step (`r6_progress.txt`)

| Step | Output | Wall | Result |
|---|---|---|---|
| `exhaustive.py` | `r6_exhaustive.txt` | 187 s | classes 0 mismatches (12.0 s); two-sided boundaries 0 mismatches (175.5 s) |
| `exhaustive_boundary.py` | `r6_boundary.txt` | 153 s | one-sided boundaries: 60 patterns, 0 mismatches |
| `alt_exhaustive.py` | `r6_alt.txt` | 501 s | 86,640 patterns, 0 mismatches |
| `fuzz.py` seeds 11, 12, 13 × 6,000 | `r6_fuzz.txt` | 306 s | 133,544 checks, 0 mismatches; 10 patterns hung inside `re` (skipped); 6 `regex` 1 s timeouts where `re` took 0.44–9.5 s |
| `incremental_fuzz.py` seeds 11, 12, 13 × 6,000 | `r6_inc.txt` | 115 s | 15,065 of 18,000 patterns incremental; 15,038 streams checked, 246,809 deltas, 0 mismatches; 27 skipped on a `regex` timeout |
| `icase_exhaustive.py 0 1` | `r6_icase.txt` | 63 s | 2,927 literals × every code point, 0 mismatches |
| `gen_cased.py --check` | `r6_table.txt` | 3 s | exit 0: `_cased_table.py` (`CASED_RANGES`, `WORD_FLIPS`, `FOLD_DISAGREE`) is current |
| `alt_negated.py` | `r6_alt_negated.txt` | 457 s | 65,040 patterns, 0 mismatches, 0 one-code-point negated sets emitted; the raw `regex` bug still fires 3/3 |
| `nesting_test.py 20000` | `r6_nesting.txt` | 4 s | scanner exact on all 7,505 patterns `re` accepts; bombs refused everywhere; 8 worst 16-deep shapes and 204 random ones compile from 600 frames deep; injected exceptions become `Refused`; 0 failures |
| `estimate_test.py` seeds 1–3 × 3,400 | `r6_estimate.txt` | 63 s | 9,744 checked, 456 refused by a limit; estimate below actual 0; accepted but `re.compile` refuses 0; patch-premise violations 0; size ratio median 1.00, p99 2.80–2.87, max 3.55; units max 3.65 |
| `s1_test.py` | `r6_s1_test.txt` | 2 s | pass: whole text 0.03 ms; "refuse" refused at 28 chars; "hold" decided `[(0, 110)]`; with a comma both refuse |
| `s1_hold.py` seeds 1–3 (1 ms), 4 (10 ms) × 2,000 | `r6_s1_hold.txt` | 44 s | see `LIMITS6.md` §4 (6,497 streams: "refuse" 132 false refusals, "hold-once" 0; 0 mismatches) |
| `drr_bound.py` | `r6_drr.txt` | 1 s | deterministic simulation, same table as 11 Nov |
| `inline_bucket6.py 3 3` | `r6_inline_locked.txt` | 174 s | see `LIMITS6.md` §3.1 |
| `stall6.py 3` | `r6_stall.txt` | 7 s | see `LIMITS6.md` §2.5 |
| `perf6.py` | `r6_perf6.log`, `perf6.md` | 9 s | see `LIMITS6.md` §2.4 and `perf6.md` (overwritten by this run; the 11 Nov version is on the WIP branch) |

### Run 2 (`clock/run-2/`)

Snapshot of the revision-6 translator and suites (`clock/run-2/SHA256SUMS`, `LIMITS.txt`: limits changes included), run on `clock/venv-tcpu/bin/python` (`.so` `64a8bac6…`, `META.txt`), 06:11:21–06:34:05 UTC (1,364 s; the lock wait before it is not counted):

| Step | Output | Result |
|---|---|---|
| `gen_cased.py --check` | `r_table.txt` | exit 0 |
| `nesting_test.py 20000` | `r_nesting.txt` | exact on 7,505; bombs refused; 16-deep from 600 frames; exceptions refused; 0 failures |
| `estimate_test.py` seeds 1–3 × 3,400 | `r_estimate.txt` | 9,744 checked, 0 below the estimate, 0 accepted that `re.compile` refuses, 0 patch-premise violations |
| `s1_test.py` | `r_s1_test.txt` | pass |
| `alt_negated.py` | `r_alt_negated.txt` | 65,040 patterns, 0 mismatches, 0 one-code-point negated sets emitted (339 s) |
| `icase_exhaustive.py 0 1` | `r_icase.txt` | 2,927 literals, 0 mismatches (38 s) |
| `exhaustive.py` | `r_exhaustive.txt` | classes 0 (12.3 s); two-sided boundaries 0 (154.4 s) |
| `exhaustive_boundary.py` | `r_boundary.txt` | 60 patterns, 0 mismatches (98.1 s) |
| `alt_exhaustive.py` | `r_alt.txt` | 86,640 patterns, 0 mismatches (336 s) |
| `fuzz.py` seeds 11–13 × 6,000 | `r_fuzz.txt` | 133,552 checks, 0 mismatches; 8 `re` hangs skipped; 7 `regex` 1 s timeouts where `re` took 0.48–13.1 s |
| `incremental_fuzz.py` seeds 11–13 × 6,000 | `r_inc.txt` | 15,038 streams, 246,809 deltas, 0 mismatches; 27 skipped on a `regex` timeout |

The patched wheel gives the same verdicts as the unpatched one on every exactness suite.

### Run 3 (`clock/results/host2_*.txt`, `r6_range_b1.txt`, `r6_inline_avail.txt`)

`clock/run_host2.sh`, 06:00:01–06:03:40 UTC (219 s; per step in `clock/results/host2_progress.txt`), load average 2.3–3.3:

| Step | Output | Wall | Result |
|---|---|---|---|
| `range_b1.py .` and `range_b1.py rev5_e2c3e2cf` (3 rounds) | `r6_range_b1.txt` | 17 s | Range classifier: 26,586 ranges (1,496 flips), 0 mismatches against brute force. A's 166 ranges: rev. 6 3.2 ms (rev. 5: 892 ms); `\b` form 1.9 ms (763 ms). A's 6-rule harness: rev. 6 48.8 ms worker CPU, 53 ms wall, loop lateness max 6.9 ms; rev. 5 5,388 ms CPU, 6.5 s wall, 100 ticks/s, lateness p99 45.5 ms, max 108 ms |
| `inline_bucket6.py 3 3 …` (wall refill against loop-available refill) | `r6_inline_avail.txt` | 73 s | `LIMITS6.md` §3.1a: available-time refill keeps B's loop-CPU wait at p99 5.8–7.5 ms, max 11.0–14.2 ms (bound 18.3 ms) |
| B's three probes, unpatched / patched | `host2_b_probes_{stock,tcpu}.txt` | 6 / 8 s | unpatched refuses B in every multi-thread row (up to 10/10); patched 0 in every row |
| `cross_tenant.py 20 3 131072`, unpatched / patched / prototype | `host2_cross_tenant_{stock,tcpu,nogil}_k3.txt` | 10 / 21 / 13 s | unpatched B refused 0/20, 20/20, 20/20 at T = 2, 4, 8; patched 0/80; prototype 0/80 with B's wall halved |
| `edge_timeouts.py`, unpatched / patched | `host2_edge_*.txt` | 6 / 4 s | unpatched: NaN = no limit, `inf`/`1e300`/`1e13` fire at once; patched: NaN `ValueError`, huge values complete |
| `gil_probe.py 384 5`, patched | `host2_gil_tcpu.txt` | 8 s | busy loop: wall ×3.9 at 5 ms, ×2.4 at 1 ms (own CPU 60–64 ms) |
| `gil_duty.py 384 5`, patched / prototype | `host2_gil_duty_{tcpu,nogil}.txt` | 27 / 5 s | wall/CPU 1.0–3.2 (5 ms), 1.0–6.9 (1 ms); prototype 1.0–1.5 |
| `cpu_variance.py 60` × 2, patched | `host2_cpu_variance.txt` | 7 s | max/min 1.24 and 1.48 |
| `semantics_check.py`, three builds | `host2_semantics.txt` | 1 s | as `clock/CLOCK.md` §4.8 |
| upstream `python -I -m unittest regex.test_regex`, patched / prototype (06:36 UTC, outside the lock: under a second each) | `host2_upstream_tests.txt` | 1 s | Ran 100 tests, OK, on both |
| `overshoot_mt.py 5`, patched | `host2_overshoot_tcpu.txt` | 13 s | fires at +0.1 to +0.3 ms of own CPU with 0, 1, 3 other busy threads |

### Run 4 (`clock/results/asan-fuzz-host2/`)

`clock/results/asan-fuzz-host2/` (`summary.json`, `parent.log`, `LOCK.txt`, `top_at_end.txt`). Build: `ASAN=1 build_patched_regex.sh` (`-fsanitize=address,undefined -fno-sanitize-recover=undefined`). Children run with `LD_PRELOAD=libasan.so`, `PYTHONMALLOC=malloc`, `ASAN_OPTIONS=detect_leaks=0:halt_on_error=1`, `UBSAN_OPTIONS=halt_on_error=1`, no `-I`. Each pattern from `fuzz_gen.py`'s grammar (seed 7; plus the 25 limit-edge shapes once): `re.compile` (skip invalid), `translate` (skip refused), `regex.compile`, then `finditer(text, pos=q, timeout=0.2, concurrent=True)` over 6 short and 2 long texts. Load average 1.0–2.0.

| Positive control (16-byte `malloc`, 32-byte `memset`) | Patterns | Compiled and matched | Matches | Timeouts | Invalid in `re` | Refused by the translator | Errors | Child deaths | Sanitizer reports |
|---|---|---|---|---|---|---|---|---|---|
| exit 99, `heap-buffer-overflow` reported | 287,725 | 267,183 | 114,563,711 | 3,190 | 20,510 | 32 | 0 | 0 | 0 |

The 11 Nov 20-second smoke (`clock/results/asan-smoke2/`, revision-5 translator) and the queued 11 Nov campaign (`clock/results/asan-fuzz/QUEUED.txt`, never run) are superseded. A long campaign belongs on a larger runner (plan §5.8).

## Changed or added on 12 Nov

- `run_r6.sh`: path-independent (`P=` selects the interpreter); adds `exhaustive.py`, `exhaustive_boundary.py`, `drr_bound.py` and `perf6.py`, per-step timing and load, and `r6_META.txt`.
- `clock/run_suite.sh`, `clock/probes/*.py`: path-independent (`XLATE_DIR`, `XLATE_SNAP`; default this directory).
- `clock/build_patched_regex.sh`: an `EXTRA_PATCH` option for prototype builds only; the shipped wheel's hash is unchanged.
- `range_b1.py`, `rev5_e2c3e2cf/` (Reviewer A's B1 before/after), `clock/run_host2.sh`, `clock/probes/gil_duty.py`, `clock/probes/semantics_check.py`, `clock/regex-2023.8.8-nogil-check.prototype.patch`, `inline_bucket6.py` (a loop-available-time refill mode).
- `LIMITS6.md`, `clock/CLOCK.md`, `RESULTS.md`: placeholders filled with the 12 Nov results and marked as such.
- Not copied from the WIP: `next6/` (an older staging copy of the root files, superseded; still on the WIP branch). Venvs, `build/` trees and the sdist tarball are not committed: the build script fetches the sdist by hash.
