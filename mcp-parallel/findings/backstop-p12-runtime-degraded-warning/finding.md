# CHG-0143 — sandbox ran under runc (no gVisor) SILENTLY when the runtime was unset/unavailable and not required

**Change-id:** CHG-0143
**Date:** 2026-07-03
**Severity:** LOW-MEDIUM (observability of a security-critical degraded posture — item 12. Does NOT change
the isolation behavior; converts a documented *silent* degradation into a *loud* one so operators can
detect "sandboxes are not actually running under gVisor").
**Area:** HARDEN THE ARCHITECTURE — item 12 (gVisor + seccomp/no-new-privileges/cap_drop/egress-lockdown).
**Files:** `services/mcp-broker/src/sandbox/docker_manager.py` (`DockerManager.__init__`, `_resolve_runtime`);
`services/mcp-broker/tests/test_sandbox_lifecycle.py` (+3 tests). No behavior change.
**Whose work it touches:** the broker sandbox runtime resolution (architecture-hardening surface).

## Root cause

`_resolve_runtime()` only validates the runtime when `runtime_required` (`MCP_SANDBOX_RUNTIME_REQUIRED`)
is true:

```python
def _resolve_runtime(self) -> str | None:
    runtime = self.config.runtime
    if not self.config.runtime_required:
        return runtime          # <-- no check, no signal
    ...
```

When `runtime_required` is false (the **default**), the method returns the configured runtime as-is:
- `MCP_SANDBOX_RUNTIME` unset → returns `None` → no `runtime=` kwarg → Docker uses the default **runc**,
  which **shares the host kernel** with the untrusted tenant workload. No gVisor.
- `MCP_SANDBOX_RUNTIME=runsc` set but runsc **not installed** in the daemon → returns `"runsc"` anyway;
  container creation later errors or silently degrades.

`docs/mcp/BACKSTOP_FINDINGS.md` already documented this: the main `docker-compose.yml` broker sets neither
`MCP_SANDBOX_RUNTIME` nor `MCP_SANDBOX_RUNTIME_REQUIRED`, and `docker-compose.prod.yml` defines no broker at
all — so *"by default containers run under runc (shared kernel), silently degrading instead of failing
closed."* An operator who believes they deployed with gVisor but mis-set the env gets **no signal at all**.

## Why not just default `runtime_required=true`?

That is the correct *prod* posture, but flipping the default would break every dev/CI host without gVisor
installed (most of them) and belongs to the deployment-owning config (compose/helm), not this code path.
Forcing it here would be an unsafe, widely-breaking change. The safe, in-scope hardening is to make the
degraded posture **loud** without changing behavior — so the gap is caught in a log/alert instead of a
breach.

## The fix (CHG-0143)

In the not-required branch, warn **once per manager** (guarded by `self._runtime_degraded_warned`, set in
`__init__`) — never raise, never spam a line per sandbox create:

- runtime unset → `WARNING: MCP sandboxes are starting WITHOUT a kernel-isolation runtime … Docker uses the
  default 'runc' (shared host kernel). Set MCP_SANDBOX_RUNTIME=runsc and MCP_SANDBOX_RUNTIME_REQUIRED=true …`
- runtime configured but unavailable → `WARNING: Configured MCP_SANDBOX_RUNTIME=%r is NOT available … will
  error or fall back … set MCP_SANDBOX_RUNTIME_REQUIRED=true to fail closed instead of degrading.`

The healthy case (runtime configured **and** available) stays quiet. The `runtime_required=true` path is
unchanged (still fails closed). The returned runtime value is identical in every case → **zero behavior
change**; only a log signal is added.

## Verification

```
cd services/mcp-broker
./.venv/bin/python -m pytest tests/test_sandbox_lifecycle.py -q -k runtime   # 7 passed (4 old + 3 new)
./.venv/bin/python -m pytest tests/test_sandbox_lifecycle.py -q              # 49 passed
./.venv/bin/python -m pytest tests -q -k "not websocket"                     # 170 passed, 0 failed
```

New tests (caplog): unset+not-required → returns `None`, warns exactly **once** across two calls (warn-once);
configured-but-unavailable → returns `"runsc"` (no raise) + warns "NOT available"; available+not-required →
returns `"runsc"` with **no** warning. The `runtime_required` fail-closed tests are unchanged and still pass.

## Scope / honesty note

Observability only — this does NOT make gVisor active where it wasn't; it makes the *absence* detectable.
The real remediation (set `MCP_SANDBOX_RUNTIME=runsc` + `MCP_SANDBOX_RUNTIME_REQUIRED=true` on the broker in
both compose files, define the broker in prod compose, and prove `docker inspect … Runtime=runsc` live)
remains a deployment-config change on a gVisor-capable host and keeps item 12 at `[ ]`. Partial coverage is
not completion.
