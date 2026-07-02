# P8.26 — Root cause: shared `nproc` ulimit caps ALL tenants at one process budget

**Iteration:** cursor-ralph-iter20
**Date:** 2026-07-02
**Story:** P8.26 (multi-org harness) — item #27 driver revealed a real multi-tenant scaling + isolation bug.

## Symptom

`scripts/mcp_multi_org_harness.py` (parallel tool calls across 3 orgs × 5 Everything MCPs = 15)
consistently showed only **7–8 of 15** servers returning tools; the rest returned
`tools/list` → `status=200 tools=0` (and echo/get-sum against them failed). Bounded warmup
retry (15 attempts × 2s) did **not** help — persistent, not transient cold-start.

## Evidence chain

1. `docker exec <org>-mcp-sandbox sh -c '…'` → `exec /usr/bin/sh: resource temporarily
   unavailable` (fork/exec EAGAIN) — the sandbox could not fork even a shell.
2. `docker inspect` → `PidsLimit=256`; `docker top` → only 8/11/15 **processes** per sandbox.
   So the per-container `pids_limit` was NOT the limiter.
3. cgroup `pids.current` (counts tasks/threads, not just processes): org-a=31, org-b=73,
   zeroshield=106, acme=17, default=17 → **sum = 244 tasks**.
4. Container user `sandbox` = **uid 1000** (`id sandbox` in `ai-mesh/mcp-sandbox:latest`).
   Host tasks for uid 1000 = **239** ≈ the sum of all sandbox cgroups.
5. `docker_manager._sandbox_ulimits` set `Ulimit(nproc, soft=hard=pids_limit=256)`.

## Root cause

`RLIMIT_NPROC` (the `nproc` ulimit) is enforced by the kernel **per real UID, host-wide** —
it counts every task owned by that UID across ALL containers, not per-container. Every org
sandbox runs as the **same** `sandbox` user (uid 1000), so the nproc ulimit became **one
256-task budget SHARED by all tenant sandboxes**. At ~15 servers the aggregate (≈244) hit 256
and further `node`/`npx` forks failed with EAGAIN → those servers never initialized → the
gateway reported them as 0-tools.

This is also an **isolation defect** (STRIDE T1 flavour): a single noisy org spawning many
threads can exhaust the shared nproc budget and **deny other orgs the ability to fork**.

## Fix (root-cause, Cursor-owned `docker_manager.py`)

Removed the `nproc` ulimit. Per-container process/thread containment is provided by
**`pids_limit`** (the pids cgroup controller), which IS container-scoped and already caps
fork bombs per sandbox (verified in P7 item #22: fork-bomb capped at `pids_limit`). `nofile`
(per-process FD cap) is retained. No mechanism can make `RLIMIT_NPROC` per-container while all
sandboxes share a UID; `pids_limit` is the correct Docker-native per-container control.

```python
def _sandbox_ulimits() -> list[Any]:
    # nofile only — nproc removed (see docstring): RLIMIT_NPROC is per host-UID,
    # sandboxes share uid 1000, so an nproc ulimit is a cross-tenant shared cap.
    return [Ulimit(name="nofile", soft=1024, hard=2048)]
```

## Verification

- Broker unit gate: `95 passed` (incl. updated `test_sandbox_lifecycle.py`: exactly one ulimit,
  `nofile` present, `nproc` absent).
- Hot-deployed to live broker (`docker cp` + restart) and recreated the 3 org sandboxes.
- `scripts/mcp_multi_org_harness.py` → **warmup 15/15**, **51/51 calls pass**, cross-tenant
  negative matrix **6/6 rejected (403)**. **GREEN 3× in a row.**
- `docker exec org-a-mcp-sandbox sh -c 'echo fork-ok'` → `fork-ok` (fork restored).

## Follow-up (defense-in-depth, not required for scaling)

Per-org **distinct UIDs** would make both `pids_limit` and a future `nproc` ulimit truly
per-tenant. Out of scope here (needs image/user provisioning); `pids_limit` already contains
per-sandbox fork bombs. Note this depends on Docker running **without** userns-remap (uid maps
1:1 to host); if userns-remap is later enabled, re-verify the host-UID accounting.
