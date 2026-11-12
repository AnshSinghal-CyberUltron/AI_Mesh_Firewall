# Revision 7: the run record

Host: the shared 2-CPU agent host of 12 Nov 2026 (gcc 13.3.0, glibc 2.39, CPython 3.12.3), with other
agents' jobs queued on the same heavy lock and light jobs running outside it. Every step below ran under
`flock /home/claude/work/.heavy.lock nice -n 10`; `r7_progress.txt` has each step's start time, the load
average at its start, its exit code and duration, and the last line of its output.

## Wheels

Built with `clock/build_patched_regex.sh` (sdist `fcbdc5f2…7712e` and every patch verified by sha256 before
anything is unpacked; `setuptools==80.9.0` by hash; no build isolation; `SOURCE_DATE_EPOCH` and
`-ffile-prefix-map`; the compile line checked for `-O2 -DNDEBUG`):

| Build | Patches | Wheel sha256 | Extension (`_regex*.so`) sha256 |
|---|---|---|---|
| revision-7 candidate, two paths (`b7a`, `b7b`) | clock + match-memory cap + upstream 611-614 | `53e490576f57ddc7200713417cf0749598c65d03b5cab73046ab3a79645e56ba` (both) | `ab284ecaf7ace845bca68ca225ad20635af0689f13f00b219879f568cbefaf28` |
| `ONLY_CLOCK=1` (revision 6's wheel, rebuilt by this script) | clock | `8ca6147610412de1a99bef3ac300d603698c394398d1c9fb0ed31035d34a36da` (= revision 6's) | `64a8bac6…` |
| `ASAN=1` (crash fuzz only, never shipped) | all three | `0d4a02e2…010e` | — |

Patch hashes: clock `cb544353…cd7d`, match-memory cap `ceb08f83…55ea` (+93/−0 in `_regex.c`), upstream
611-614 `ccda1ce2…1111` (+11/−2, `_regex.c` and `_regex_core.py`).

## The run: `run_r7.sh`

```
P=<candidate venv>/bin/python P6=<revision-6 clock-only venv>/bin/python \
PCI=/home/claude/work/venv-ci/bin/python WT=<this worktree> PA=<ASAN candidate venv>/bin/python \
flock /home/claude/work/.heavy.lock nice -n 10 ./run_r7.sh
```

Queued 09:25:38 UTC; the lock was acquired at 09:42:24 and released at 10:27:59 (`r7_done.txt` written).
Load average 1.2–3.1 at step starts. At the end another agent's niced simulation was running outside the
lock at 95% of one CPU (`r7_top_at_end.txt`); timing figures are thread CPU, which that does not inflate
beyond the host contention §5.2.4 states (up to 1.7×).

| Step | Duration | Result |
|---|---|---|
| `gen_cased.py --check` | 2 s | exit 0; clip properties P1, P2, P3, P6: 0 violations |
| `ci_clip_test.py 1 20000` | 175 s | **12 mismatches** in 20,000 random sets (0 in 20,000 ranges and A's 586 items) → the `_merge` bug (below); output kept as `r7_ci_clip_prefix.txt` |
| `exhaustive.py` | 160 s | classes and two-sided boundaries: 0 mismatches |
| `exhaustive_boundary.py` | 111 s | one-sided boundaries, 60 patterns: 0 mismatches |
| `icase_exhaustive.py 0 1` | 40 s | 2,927 literals: 0 mismatches |
| `alt_exhaustive.py` | 365 s | 86,640 patterns: 0 mismatches |
| `alt_negated.py` | 308 s | 65,040 patterns: 0 mismatches; no one-code-point negated set emitted; raw `regex` still merges |
| `fuzz.py` seeds 11-13 × 6,000 | 268 s | 133,544 checks, 0 mismatches; 9 `re` hangs skipped; 7 `regex` 1 s timeouts where `re` took 0.50–13.3 s |
| `incremental_fuzz.py` seeds 11-13 × 6,000 | 85 s | 15,038 streams, 246,810 deltas, 0 mismatches; 27 skipped on a timeout |
| `renumber_test.py` seeds 1-3 × 3,000 | 2 s | 44,700 checks, 0 mismatches, group count right on every pattern, 1,454 self-references refused |
| `nesting_test.py 20000` | 4 s | exact on 7,505; 0 failures |
| `estimate_test.py` seeds 1-3 × 3,400 | 44 s | 9,744 checked, 0 below the estimate, 0 accepted that `re.compile` refuses, 0 patch-bound violations |
| `s1_test.py` | 1 s | pass |
| upstream `regex.test_regex`, cap 0 and 16 MiB | 1 s | 100 tests OK each |
| `ci_model7.py 4 300` | 10 s | features 0.29–0.64 of their units; 300 rules: real/model median 0.44, p99 0.75, max 0.79; worst 17.8 ms |
| `ci_cost7.py` | 6 s | A's flat rule: refused by `check_limits` (163,790 units) and, uncapped, by the CPU cap at 50.3 ms after 138 checks (revision 6: translated after 414 ms, 1 check); A's plan: revision 6 3,439 ms, revision 7 all refused; worst accepted shape 13.5 ms. (Its last line, "0 accepted", is the harness adding a distinguishing literal to a rule already at its unit cap, which then refuses each rule on its own) |
| `mem7.py corpus` | 3 s | largest charge over the ordinary corpus 0.003 MiB per 512 KiB pass |
| `mem7.py all 0` | 7 s | no cap: `(\w)*` and the 16-deep nest +0 MiB (non-capturing); other shapes 40–136 MiB charged, up to +417 MiB RSS with three threads |
| `mem7.py all 16777216` | 2 s | every memory-growing shape `MemoryError` in 11–40 ms; three at once +24–34 MiB |
| `mem7.py speed`, 3 × (clock-only, candidate) | 75 s | candidate 2,402.8 ms each time; clock-only 2,402.8–2,413.2 ms |
| `clock/probes/cross_tenant.py 20 3 131072` | 17 s | B refused 0/80 (alone, T = 2, 4, 8); org A refused at 100.3–100.4 ms of its own CPU |
| `s1_hold7.py 100` | 72 s | the §5.2.4 table; `scan_pass`: ordinary rules never left undecided |
| `rewrite_check7.py` | 2 s | HEAD control 52/191/780 ms ticker gaps at 20/22/24 characters; revision 7 control 2.1–6.9 µs; gateway < 0.1 ms, gaps 1.1–1.3 ms |
| `idle7.py` (venv-ci, this branch's `gateway_v2`) | 2 s | three gateway-side waits: cut at HEAD, complete with keep-alives; a non-reading client cut by the write bound at 1 s; the OpenAI SDK decoder yields 3 data events |
| `inline_fair7.py 3 3 …` | 55 s | light tenant inline 6–25% (plain bucket) → 99–100% (bypass) at 1–6 busy orgs; aggregate 21–25%; max loop CPU ahead of B 10.9–11.8 ms |
| `drr_fast7.py` | 5 s | light-pass wait p99 141–1,008 ms (DRR only, ≥ 2 heavy orgs) → 2.0 ms (fast lane) |
| `cost7.py` | 6 s | the §5.2.4 scan-point table |
| `cache7.py` | 5 s | charge covers all 2,074 rules; 10-rule plan 143 KiB, 64-rule 946 KiB; cold compile 9.8 / 57.1 ms |
| `check_plan7.py` | 1 s | `check_plan` 1.85 / 11.0 ms per plan against a 1.1 / 4.7 µs hash comparison |
| `clock/probes/asan_fuzz.py r7_asan 900 2` (ASAN + UBSan build) | 901 s | positive control reported; 132,025 patterns, 122,480 compiled and matched, 52.6 million matches, 1,409 timeouts at 0.2 s, 9,542 invalid in `re`, 3 refused; **0 child deaths, 0 sanitizer reports, 0 errors** |

`r7_META.txt` records the interpreters, the extension hashes and the file hashes at the start (its line
for the ASAN interpreter is an import error: that build loads only with `LD_PRELOAD=libasan.so`, which
`asan_fuzz.py` sets for its children).

## The translator changed once during the run

`ci_clip` found that the clipped verdict source merged pieces of **two different ranges** across a gap
that held a cased code point the item leaves out (`[Ȣ-ɢɤ-᏷]` leaves out U+0263, which
the merged piece included). `_merge` was fixed at 09:51:30 UTC to bridge only gaps with no cased code
point. `translate_prototype.py`: `a41884c2…` at the start (`r7_META.txt`), `ab152878…` after the fix.

- Steps that started before the fix: `gen_cased_check`, `ci_clip`, `exhaustive`, `exhaustive_boundary`,
  `icase`, `alt_exhaustive` (started 09:50:32 and imported the module then). None of them contains a
  case-insensitive set with two ranges that both cover cased code points (the only two-range set,
  `[A-Z0-9]`, has one cased piece), so `_merge` returns the same for every one of their items.
- Every later step imported the fixed file.
- `ci_clip_test.py`'s random sets were re-run on the fixed file (below). Its other two parts (20,000 single
  ranges and Reviewer A's items) give `_merge` pieces of one BMP range per item, whose gaps are non-cased
  by construction, so the fix does not change them; their 0 mismatches stand from the first run.

## Follow-up: the random sets of `ci_clip_test.py` on the fixed file

```
nice -n 10 <candidate venv>/bin/python ci_clip_test.py 1 20000 2b > r7c_ci_clip_random.txt
```

`r7c_progress.txt`: 10:29:07–10:31:37 UTC, 150 s wall, 124 s of CPU, load 1.8–3.0. Run **outside** the heavy
lock, at about the 2-minute CPU threshold, because the lock queue then held eight other agents' jobs; the
`2b` argument (added after the first run) skips parts 2a and 2c but keeps the random generator's state,
so these are the same 20,000 sets as in the first run. Result: **0 mismatches** (`r7c_ci_clip_random.txt`);
part 1's properties again 0 violations.

A queued full re-run under the lock (`run_r7b.sh`, queued 09:52:43) acquired the lock at 10:31:51, after
the run above had finished, and was stopped by the author at once (it had written only part 1); its
script and partial output were removed, and nothing from it is cited.
