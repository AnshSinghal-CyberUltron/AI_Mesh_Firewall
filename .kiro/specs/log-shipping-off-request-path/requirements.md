# Task 5 — get log shipping off the request path

## Problem

Two facts on this tree, both verified by reading the code:

1. **`main.py:6019` — `gateway_logger.setLevel(logging.DEBUG)`, unconditional.** Not
   env-gated. `GATEWAY_LOG_LEVEL` does not override it, which the perf overlay already
   documents.
2. **`redis_log_handler.emit` publishes synchronously** — `client.publish(...)` inside
   `logging.Handler.emit`, which runs inline at the call site. On an async request
   handler that is a **blocking network round trip on the event loop**.

Combined: every DEBUG line on the request path blocks the loop on Redis. A slow or
briefly stalled Redis blocks it for up to `socket_timeout=1.0` **per log line**.

## Why this is now the prime suspect

`docs/perf/evidence/2026-09-09-task10-capacity-baseline.md` measured a firewall-tax p99
of 53 → 146 ms between concurrency 4 and 16 while the gateway used **1.52 of 4 vCPU**.
Median flat, tail collapsing, CPU idle — the signature of **blocking I/O on the event
loop**, not of CPU exhaustion.

Two candidates were identified. Task 2 (thread-per-regex) has been fixed, so the
re-run of that exact sweep discriminates between them:

| sweep result after task 2 | conclusion |
|---|---|
| p99 collapses | thread churn was the cause; this task is a smaller, separate win |
| p99 stays bad | thread churn was **not** the cause; blocking Redis publishes are the live hypothesis, and the capacity baseline's stated hypothesis was wrong |

Either outcome gets recorded. The second one contradicts what I wrote in the baseline,
and that is exactly the point of running it.

## Requirements

**R1 — zero synchronous network I/O on the request path from logging.** Handing a record
to the shipper must not block the caller on a socket.

**R2 — log records must not be silently dropped without saying so.** A bounded queue that
discards under pressure is acceptable; discarding *silently* is not. Drops must be
counted and observable.

**R3 — the DEBUG level must become configurable**, with the current behaviour reachable
explicitly. The unconditional `setLevel(DEBUG)` is removed, not merely defaulted.

**R4 — no log ordering guarantee is broken that anything relies on.** Establish first
whether ordering across loggers is depended upon; a queue changes interleaving.

**R5 — shutdown must flush.** A queued shipper that loses the last records on exit
trades a latency bug for a diagnosability bug.

**R6 — measured on the same sweep**, so the result is comparable with the baseline and
with task 2.

## Out of scope

Changing what is logged, or the log format. This task changes only *when and on which
thread* records leave the process.
