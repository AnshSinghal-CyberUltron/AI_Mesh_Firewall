**Plan Reviewer B (security, scale & operability): REQUEST CHANGES**

Reviewed at `e2c3e2cf`. Everything below was run on the coordinator's `regex` 2023.8.8 / Python 3.12.3 venv with the plan's own `translate_prototype.py` and `incremental.py`. Timings are thread CPU unless marked otherwise. The host has 2 shared CPUs.

**What revision 5 fixes:**
- My revision-4 blocker 1: the incremental scan and one-sided `\b`.
- Most of my revision-4 blocker 3: the limits and the projector's parse-only check.
- The supply-chain items and all my NITs.

The incremental scan held up against a targeted attack (see "Checked and OK").

**What blocks:** two things. The central "thread CPU" claim of §5.2.4 is false, and a nesting-depth bomb that passes the length limit takes down the projector's safety heartbeat.

## BLOCKER

**1. `regex`'s timeout counts process CPU, not thread CPU, so one tenant's scans shrink every other tenant's budget.**
- **Evidence in the source** (2023.8.8 sdist, sha256 `fcbdc5f2…7712e`):
  - `_regex.c:18337` sets `state->start_time = … clock()`;
  - `_regex.c:2211` tests `clock() - state->start_time < state->timeout`;
  - `_regex.c:20791` converts the timeout with `CLOCKS_PER_SEC`.

  On Linux, glibc's `clock()` is `CLOCK_PROCESS_CPUTIME_ID`: CPU time summed over **every** thread of the worker process.
- **Direct measurement.** With 3 other scan threads busy in the same process, a 100 ms timeout fired at 101–104 ms of process CPU but only 27–31 ms of the calling thread's CPU.
- **The plan's design, simulated faithfully** (§5.2.4/§5.2.5). Org B's off-loop pass is 3 rules over 128 KiB, so its budget is 100 ms of thread CPU, shared by the rules, debited with `thread_time()`, and each call gets `timeout=remaining`. Org A's pathological passes run on the other scheduler threads:

  | Org A's scan threads | B's pass refused, of 10 (3 runs) |
  |---|---|
  | 0 | 0 (the pass costs 53–63 ms alone) |
  | 1 (T = 2, the most §5.2.5 allows A on this host) | 1–6 |
  | 3 (T = 4) | 10, at 22–54 ms of B's own CPU |

  On a T-thread worker, A may hold T − 1 threads, so B's effective budget is about budget ÷ T. Requirements 4 (no false refusals) and 6 (isolation) fail across tenants. The inline path's 5 ms calls also expire early, but they spill off the loop instead of refusing, so that part is benign.
- **Why the plan's evidence missed it.** `overshoot.py` runs single-threaded, where process CPU equals thread CPU. Revision 4's wall-time overshoot was indeed host contention; the explanation "thread CPU" is not.
- **Fix:**
  - The wheel is already built from the sdist in a builder stage. Patch the two `clock()` sites to `clock_gettime(CLOCK_THREAD_CPUTIME_ID)` (with the tick conversion to match), record the diff, and run the exhaustive, fuzz, crash-fuzz and ASAN suites on the patched wheel.
  - The alternative is to run scans in processes.
  - Either way, add a test: B's pass at about 60% of its budget is not refused while T − 1 threads of org A burn.

**2. A nesting-depth bomb that passes the length limit poisons the projector round, and so the whole fleet's stamp.**
- **The pattern.** `"(" * 499 + "a" + ")" * 499` is 999 characters, has no repeats, and passes the length, repetition and case-insensitive limits.
- **What it does.** On 3.12.3, `re.compile`, the plan's `check_limits`, `translate` and `stopper` all raise **`RecursionError`**, not `re.error` and not `Refused`.
- **The same failure exists at HEAD,** measured on 3.12.3 and 3.13.18:
  - `validate_plan` raises `RecursionError`. It catches only `ValueError`, `UnicodeEncodeError` and `CompileError` (`state_control/projector.py:565,578`), and `_prepare` catches only `re.error` (`detect/conditions.py:108-111`).
  - The exception escapes `round_once`. `projector_service.py:277-281` logs "the heartbeat then ages".
  - After `AMF_PROJECTOR_LAG_MS` (16 s), the re-hydrators withhold the stamp and every gateway fails closed. This repeats every round while the row exists.
- **The plan routes rows to exactly this path.** §5.2.3 sends Django admin, `load_policies` and existing stored rules through the projector check. Through the DRF serializer the same pattern raises `RecursionError` inside `validate_condition` (`re.error` only), so the save surfaces as an unhandled server error rather than a declared refusal (not run here).
- **A shallower variant hits the gateway.** At 400 levels, the pattern passes every check, and then `regex.compile` raises `RecursionError` in the gateway. The gateway compile path must fail closed.
- **Fix:**
  - Add a nesting-depth limit (for example ≤ 32), checked by an iterative bracket scan **before** `re._parser`.
  - `check_limits`, `validate_plan`, control and the gateway compile treat **any** exception (`RecursionError`, `SystemError`, `MemoryError`) as a refused rule. Nothing escapes the round.
  - Test: a 499-deep row leaves the projector heartbeat fresh, and the gateway refuses that rule.
- Because this is live at HEAD, consider shipping the `validate_plan` part as a hotfix ahead of F1a-1.

## SHOULD-FIX

**3. The inline loop share is capped per org but not in aggregate.** Each org's bucket held its 25% (simulated: 4 streams per org, 50 deltas/s, about 5.8 ms of customer-regex CPU per delta). The aggregate did not:

| Busy orgs | Inline customer-regex share of the loop | Tenant B extra delay, p50 | p99 |
|---|---|---|---|
| 2 | 51% | 0.2 ms | 14 ms |
| 4 | 98% | 51 ms | 87 ms |
| 6 | 97% | 85 ms | 128 ms |

- Fix: add a worker-wide inline bucket, beyond which every org's customer scans go off the loop.
- State the DRR latency bound as a function of the number of active orgs × the per-pass budget (up to 400 ms at 512 KiB).

**4. Translation size and compile cost are understated, and the limits miss them.** These patterns pass `check_limits`:

| Pattern | Translated length | Compile | Other cost |
|---|---|---|---|
| `\s`×500 | 39k characters | 85 ms | — |
| `\b.`×330 | 27k characters | 113 ms | 24.7 ms per 32 KiB match |
| `(?=\w)`×166 + `a` | — | — | 77 ms per 32 KiB match |

- At 64 rules that is about 7 s of compile per program, against §5.2.3's "236 ms at every per-plan limit".
- Isolation still holds, because compile is charged to the org off the loop. I measured a 9–15 ms loop stall during one compile, against 7.3 ms claimed.
- Fix: add a limit on translated size, or count the `\s` and two-sided `\b` expansions; correct the table; charge the byte-bounded cache with the measured footprint.

**5. Supply chain: the plan is sound; three additions.**
- **Hash-pinned build:** pin the sdist sha256 (`fcbdc5f2b0f1cd0f6a56cdb46fe41d2cce1e644e3b68832f3eeebc5fb0f7712e` from PyPI today), the builder image digest and the build backend with hashes, so a cache rebuild reproduces the wheel or fails.
- **If the blocker-1 patch lands,** it is a fork: record it in §5.2.2 and run the full evidence suite on it.
- **Advisories:** OSV still lists no advisory for `regex`.

## NIT
6. §5.2.4: correct "counts the matching thread's CPU time" (blocker 1).
7. The projector time box should validate oldest-pending-first, not in slug order. One org can add at most one new body per round, so starvation needs many orgs, but the order should be fair.

## Checked and OK
- **The incremental scan is exact under attack.** I ran a targeted fuzz of 40 hostile patterns, 39 of them incremental. They covered:
  - multiline `$` and `\b`/`\B` at the text edge;
  - a lookbehind containing a lookahead;
  - conditionals, backreferences and atomic groups;
  - `(?i)` Kelvin sign and long s;
  - U+001C, combining marks and newlines.

  Over 3 seeds, random 1–5 character deltas, 36,000 streams and 764,482 deltas, every incremental hit list equalled a whole-text `finditer`: 0 mismatches. The stopper reasoning also holds:
  - consumers inside lookarounds and backreferences are collected;
  - multiline `$` is evaluated at a position ≤ the first stopper, which exists;
  - `\Z`, a non-multiline `$` and a DOTALL `.` give no stopper.
- **Per-delta cost reproduces.** Set B on prose costs 0.04 ms per delta at 32 KiB and 0.07 ms at 128 KiB. A tenant-steered stream with no stopper (one 128 KiB word) grows from 4.1 to 14.9 ms per delta. That is the case §5.2.4 names, and it moves off the loop (subject to blocker 1).
- `check_limits` now refuses my revision-4 cost cases: `x{1000}`×140 by expanded repetition, and 996 case-insensitive items by the item limit.
- The projector runs parse-only.
- The boot check uses `importlib.metadata`, and `requires-python` is `==3.12.*`.
- Explicit-set exit plan: recorded in §5.2.2.
- My revision-3 and revision-4 NITs are addressed: parent hardening, reserved `_` slugs, and `base_url` kept out of the projector notes.
- The negative-timeout refusal is needed: `_regex.c:20791` maps a negative timeout to no timeout.

## What I ran
Probes are in `/home/claude/work/rv-103-b-scratch/r5/`, run against a detached worktree at `e2c3e2cf`.

| Probe | What it measured |
|---|---|
| `clock_semantics.py`, `clock_t2.py`, `clock_pass.py` | Process-CPU timeout behaviour, and the plan's per-pass budget under it |
| `projector_poison.py` | HEAD `validate_plan` with the 499-deep pattern, on 3.12.3 and 3.13.18 |
| `inc_attack.py` | Targeted incremental-vs-whole-text fuzz |
| `perdelta5.py` | Per-delta cost, including a stopper-free stream |
| `inline_bucket.py` | Aggregate inline share across busy orgs |
| `limits_attack.py` | Patterns that pass the limits but cost a lot |

I also read `_regex.c` from the 2023.8.8 sdist, fetched from PyPI as data only.


---
_Generated by [Claude Code](https://claude.ai/code)_
