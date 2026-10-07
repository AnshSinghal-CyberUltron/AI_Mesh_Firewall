# c36m-rc2.patch: on top of RC2 (budget-set honesty, Prometheus label escaping, lost-COMMIT read-back)

> **STATUS: RC3 input, unreviewed.** The controller decided on 2026-09-24: no RC2.1 patch. RC2's items 3, 4 and 5
> pass as specified; the gaps below go to RC3. Do not apply this to RC2 / RC2.1. The patch and its evidence are
> kept as the starting point for RC3 (label-value escaping, budget-set status, the status of a lost read
> acknowledgement, seed status, the USAGE `verify` wording, and a read-back to shrink the unknown count).

Lane r2-fix-c36m. Built for RC2.1, then re-scoped as RC3 input (see STATUS above). The controller had set three priorities:
1. MUST: budget-set never reports "ok" when the counter write failed.
2. MUST: escape Prometheus label values everywhere a series is built.
3. SHOULD: read back a lost COMMIT before reporting it.

RC2's reporting contract is unchanged: the status names `ok`, `ok_publish_pending`, `unknown` and `error`; exit codes 0, 0, 3 and 1; `StateWriter.last`; and the CLI's JSON fields.

- **Base:** RC2, manifest `6093c8dd10bd12f5d5058a963ac9db3ed9768985c4907d73d3a5272c01cff0fc`. I extracted `rc2/rvproto2-rc2.tar.gz` (sha256 `eb9d4da2…`) fresh to `SP/rvproto2-c36m-rc2-base` and re-verified its manifest.
- **Work tree:** `SP/rvproto2-c36m-rc2-work`.
- **Patch:** `patches/c36m-rc2.patch`, sha256 `6e44be471b97ae5a8cebd5214de786a1fa44528892ba2d90e436474617ff8a14`. 12 files: 8 modified, 4 new.
- **Apply:** `cd SP/rvproto2 && patch -p1 --dry-run < …/c36m-rc2.patch && patch -p1 < …/c36m-rc2.patch`.
  - On the fresh RC2 extraction it applies cleanly, and RC2 + patch is byte-identical to the work tree.
  - The live `SP/rvproto2` was still exactly RC2 at 12:03Z (manifest verified). The patch dry-runs cleanly on it.
- **Unit suite:** 147 passed: RC2's 136 plus 11 new. That includes import-linter (2 contracts) and the GW00 gates. It passes with prometheus_client 0.21.1 (the shared venv) and with 0.25.0.
- **Supersedes:** `c36m.patch`, withdrawn and not for RC2 (kept in `evidence/r2-fix-c36m/withdrawn-c36m-patch/`).

## What changes

### 1. budget-set never reports "ok" while the new budget is not in effect (MUST)

The budget record commits and publishes. The live counter is then written with a separate `MSET`. If that fails, RC2 logged the failure but still reported `ok`, although the new budget does not apply until the re-hydrator resets the counter.

- **Now:** any exception from that `MSET` makes the status `ok_publish_pending`, with the note "the live budget counter was not reset: the re-hydrator resets it".
- **Why every exception:** the record is committed at that point, so no exception may turn it into a reported failure either.
- **Tested:** library and CLI (exit 0, `ok_publish_pending`). The re-hydrator restored the counter to the new limit and version tag in its next round: 0.12 s after the write in the real-Postgres run, with a 1 s period.

### 2. Prometheus label values are escaped wherever series are built (MUST)

**Escaping at the source.** `metrics.label(name, **labels)` escapes backslash, double quote and newline in every value. It builds every name that carries operator data:
- `quota.py`: `quota_admitted_tokens`, `lease_granted_tokens` and `quota_rejected` by org (4 sites);
- `ops._gauges`: `plan_version_info` (org, version), `killswitch_engaged` (org or model key) and `lease_held_tokens` (org).

**The renderer.** `metrics.prometheus()` is the one renderer for the gateway and unit workers, the guard and unit owners (`role="owner"`), per-worker gauges (`worker=`) and the worker's own `/metrics`. For every series it:
- parses the labels embedded in the name (strict escaping);
- adds its own labels (`stat`, `role`, `worker`), escaped;
- checks the metric name, the label names, and that no label name repeats.

A series that fails this is **left out**, and counted in a comment line: `# rvproto: N series left out (invalid name or labels)`. So one bad series can never invalidate the scrape again.

**Other rendering changes:**
- `rv2_signal` lines go through the same escaping.
- Values go through `number()`: integers stay integers, and non-finite values are written `+Inf`/`-Inf`/`NaN`.
- For ordinary label values the output is byte-identical to RC2. I checked this on the same exports rendered by both trees: 116 lines, covering histograms, counters, gauges, per-worker gauges, owners and the worker route.

### 3. A lost COMMIT is read back from Postgres before anything is reported (SHOULD)

On `CommitUnknown` from the write transaction, `StateWriter._durable()` reads the outcome back:
- **What it reads:** `rv2_log` at the write's `(kind, version)`, taken as a `FOR SHARE` read of the kind's version row. That waits for a COMMIT still in flight on the old connection, with `lock_timeout` 2 s per attempt.
- **Retries:** with backoff, for up to `RV_WRITE_RESOLVE_S` (default 30 s).
- **Durable:** published as usual, then `ok` / `ok_publish_pending`.
- **Not durable:** `error`. `NotCommitted` is raised and the CLI exits 1. Nothing was written.
- **Postgres doesn't answer within the bound:** `unknown`, `CommitUnknown`, exit 3, exactly as in RC2. This is now the ONLY source of `unknown`.

Read-only transactions ignore a lost COMMIT answer, because their rows are read and they wrote nothing. These are `PgDB.version`, `records` and `snapshot`, and the rollback log read. In RC2 such a failure raised `CommitUnknown`, which had two effects:
- key-revoke, plan-update and rollback reported `unknown`, and the CLI printed no status field, although nothing was written;
- a publish's snapshot read turned a committed write into `ok_publish_pending`.

### Files

| File | Change |
|---|---|
| `rvproto/control/writer.py` | `NotCommitted`; `StateWriter(resolve_s=30.0)`; `put()` resolves `CommitUnknown` through `_durable()`; `budget_set` → `ok_publish_pending` on a counter failure; the rollback read is read-only. |
| `rvproto/control/db.py` | `PgDB.tx(read_only=)`; `PgDB.logged()` with `lock_timeout_ms=2000`; `errors` on both DBs; `MemDB.logged()` and test hooks `commit_lost_next` and `down`. The `DB` protocol gains `errors`, `logged` and `read_only`. |
| `rvproto/control/__main__.py` | `RV_WRITE_RESOLVE_S`. |
| `rvproto/runtime/metrics.py` | `label()`, `number()`, and the validating `_series()` / `prometheus(extra=((name, value), …))`. |
| `rvproto/runtime/exporter.py` | owners use `extra=(("role", "owner"),)`; `rv2_signal` lines use `label()` and `number()`. |
| `rvproto/admit/quota.py`, `rvproto/edge/ops.py` | operator-data label names built with `label()`. |
| `tests/unit/test_c36_state.py` (RC2's test, changed) | `test_write_outcomes_are_reported_honestly` asserted that a durable COMMIT with a lost answer (`commit_unknown_next`) reports `unknown`. With the read-back it reports `ok`. The test now covers: durable → ok; never landed → `NotCommitted` / error (`commit_lost_next`); Postgres unreadable → `unknown` (`down`, `resolve_s` 0.2). |
| `tests/unit/test_prometheus_exposition.py` (new) | Gateway, guard and unit exporters (full `/metrics` over HTTP) and the worker route (C38 off and on). Operator characters in an org id, plan version and model name must round-trip; a raw, unescaped owner series must be left out with the comment. Every parsed name is checked against the exposition grammar, one sample per line, no duplicates. |
| `tests/unit/test_c36_writer_readback.py` (new) | budget-set counter failure (library + CLI); lost COMMIT durable → ok and published; never landed → error; unknown only while Postgres is unreadable; CLI exit codes; a read whose COMMIT answer is lost is complete (a fake psycopg connection drives the real `PgDB.tx`). |
| `tests/functional/f_c36_writer.py` (new) | The real-Postgres write-stream checker below, as a tree tool (`RV_TREE` selects the tree). It reads RC2-style `last`, the CLI JSON and exit codes. |
| `tests/functional/cutproxy.py` (new) | A TCP proxy that cuts every live connection with RST on `kill -USR1` (optional per-chunk delay). It is how the functional runs produce lost COMMIT answers. |

## Test results

### Unit: the 11 new tests fail on RC2 and pass with the patch, under both prometheus_client versions

| Test | RC2 (0.21.1 and 0.25.0) | RC2 + patch |
|---|---|---|
| exposition: gateway, unit exporters (org id with `"` `\` `\n`) | FAIL: the scrape does not parse | PASS; the value round-trips |
| exposition: guard exporter (a raw, unescaped owner series) | FAIL: invalid text (0.25.0). 0.21.1 accepts the line leniently, so the test fails on the missing drop comment. | PASS: the series is left out and counted in the comment |
| exposition: worker `/metrics` route, C38 off and on (plan version, org, model name) | FAIL | PASS |
| writer: budget-set counter write fails (library + CLI) | FAIL: status `ok` | PASS: `ok_publish_pending` |
| writer: lost COMMIT answer of a durable write | FAIL: `CommitUnknown` / unknown | PASS: read back → ok, published |
| writer: lost COMMIT answer of a write that never landed | FAIL (the hook and `NotCommitted` are new) | PASS: error, nothing written |
| writer: unknown only while Postgres cannot be read back | FAIL: unknown even after Postgres answers again | PASS |
| CLI: exit codes after read-back | FAIL: exit 3 where the write was durable | PASS: 0 / 1 / 3 |
| a read whose COMMIT answer is lost is complete | FAIL: `CommitUnknown` from `records()` | PASS |

Full suite on RC2 + patch: 147 passed with 0.21.1 and with 0.25.0. Files are in `evidence/r2-fix-c36m/rc2-patch/unit/`: `new-tests-{rc2,patch}-pc{0.21.1,0.25.0}.txt` and `full-suite-patch-pc*.txt`.

### Real Postgres: RC2 vs RC2 + patch, same containers and fault schedules, run back to back

Setup:
- Throwaway `postgres:16-alpine` (16.15) and `redis:7.4-alpine`; both are removed.
- 4 writer threads, plus a re-hydrator running throughout, plus a final re-hydration check.
- The checker is `tests/functional/f_c36_writer.py`, the same file for both trees.
- Driver: `evidence/r2-fix-c36m/rc2-patch/run_writer.sh`. Schedules: `faults-*.txt`.

Runs:
- **lib:** 90 s at 60 writes/s: 4 restarts, 5 SIGKILL crashes, a 2 s pause, and a Redis kill and restart.
- **cut:** 120 RST cuts of every live Postgres connection (cutproxy), plus a crash and a restart.
- **cutdelay:** the same proxy with 4 ms client→server forwarding delay, so a COMMIT can be cut before or after the server has it: 200 cuts in 50 s.
- **cli:** one `python -m rvproto.control plan-set` process per attempt: 100 cuts, a crash, a restart and a Redis kill.

| Run | Tree | Verdict | ok | ok_publish_pending | error | **false failures** | **unknown** | lost COMMITs read back |
|---|---|---|---|---|---|---|---|---|
| lib | RC2 | PASS | 4493 | 367 | 353 | 0 | 0 | – |
| lib | **patch** | PASS | 4487 | 368 | 372 | 0 | **0** | – |
| cut | RC2 | PASS | 5190 | 55 | 123 | 0 | **3** (2 durable, 1 not) | – |
| cut | **patch** | PASS | 5191 | 51 | 130 | 0 | **0** | 4 durable, 1 rolled back |
| cutdelay | RC2 | PASS | 1168 | 537 | 158 | 0 | **99** (5 durable, 94 not) | – |
| cutdelay | **patch** | PASS | 1202 | 498 | 269 | 0 | **0** | 2 durable, 55 rolled back |
| cli | RC2 | PASS | 502 | 115 | 96 | 0 | **6** (1 durable, 5 not) | – |
| cli | **patch** | PASS | 506 | 106 | 106 | 0 | **0** | (inside the CLI processes) |

In every run:
- 0 acknowledged writes missing from Postgres;
- 0 duplicate versions or `(epoch, seq)`;
- 0 out-of-order log rows or acks;
- the store equalled Postgres after re-hydration.

The CLI status agrees with the exit code on every attempt: patch `ok/0` 506, `ok_publish_pending/0` 106, `error/1` 106.

Files: `evidence/r2-fix-c36m/rc2-patch/matrix-summary.txt` and `runs/<run>-{rc2,patch}/`.

### Pending writes really are published by the re-hydrator (`evidence/r2-fix-c36m/rc2-patch/pending/{rc2,patch}/pending.json`)

Real Postgres and Redis, the tree's own CLI, and `python -m rvproto.control rehydrate` as a separate process with its default period and grace:

| Scenario | RC2 | RC2 + patch |
|---|---|---|
| A. Store down at write time (CLI) | `ok_publish_pending`, exit 0; published 1.16 s after the store returned | the same (1.14 s) |
| B. Postgres fails right after the COMMIT (r2-state's path) | `ok_publish_pending`; published by the re-hydrator in 1.33 s | the same (1.28 s) |
| C. COMMIT durable, answer lost | `unknown`; the re-hydrator publishes it (1.33 s) | read back: **`ok`**, published by the writer (0.03 s) |
| D. COMMIT never reached the server, answer lost | `unknown`; never published | read back: **`error`** (`NotCommitted`); never published |
| E. budget-set, counter write lost | **`ok`** | **`ok_publish_pending`**; counter repaired in 0.12 s |

`verify` exits 0 on both. The first patch attempt failed because of a harness bug, not the patch: the test's `PgDB` subclass lacked the new `read_only` keyword. It is kept in `pending-first-attempt/` with a note.

## New knobs and API

| Name | Default | Meaning |
|---|---|---|
| `RV_WRITE_RESOLVE_S` | 30 | Control-plane CLI: how long a write whose COMMIT answer was lost keeps reading the outcome back from Postgres. After that it reports `unknown` (exit 3). |
| `StateWriter(resolve_s=30.0)` | 30 | The same, for library callers. |
| `PgDB(lock_timeout_ms=2000)` | 2000 | Per-attempt wait for a writer of the kind still in flight. |
| `NotCommitted` (writer.py) | — | Raised when a lost COMMIT is read back as not durable (status `error`). |
| `PgDB.tx(read_only=)`, `DB.logged()`, `DB.errors` | — | Protocol additions. `MemDB` has matching hooks (`commit_lost_next`, `down`) for tests. |

## USAGE.md text

- **§3 control plane, "Every write prints its outcome":**
  - `ok_publish_pending` also covers a budget-set whose live-counter reset failed ("the new budget is in effect once the re-hydrator resets the counter").
  - `unknown` (exit 3) now means: "the connection failed during COMMIT and Postgres could not be read back within `RV_WRITE_RESOLVE_S` (30 s)". The write may or may not be durable. rv2_log shows which, by the printed version and hash; `verify` does not.
  - Add: "A COMMIT whose answer is lost is read back from Postgres first: durable → `ok` / `ok_publish_pending`, not durable → `error`."
- **§5 observability:** "Label values are escaped (`\\`, `\"`, `\n`). A series whose name or labels cannot be rendered validly is left out and counted in a `# rvproto: N series left out` comment. Non-finite values are `+Inf` / `-Inf` / `NaN`."
- **§6 tests:**
  - F-C36-WRITER is `tests/functional/f_c36_writer.py [--cli] --fault 'T:CMD' …`, with `tests/functional/cutproxy.py` for connection cuts.
  - The unit suite needs `prometheus_client` (0.21.1 is in the shared venv).

## Not in this patch

- **seed status:** `seed` still prints no status line (RC2 behaviour). Its budget-set writes do report their own outcomes to library callers.
- **A lost COMMIT answer inside the re-hydrator's own `repair_epoch`:** it still raises `CommitUnknown`, so the round fails and the next round re-diagnoses (RC2 behaviour).
