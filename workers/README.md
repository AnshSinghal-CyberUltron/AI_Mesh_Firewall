# workers (async plane)

Celery workers + beat scheduler. **No HTTP** except health sidecar optional.

## Queues

Subscribes to all queues in `shared/ai_mesh_shared/jobs/queues.py`:

- `policy.compile`, `platform.batch`, `compute.heavy`, `scan.tier2`, `vector.index`, `mcp.audit`

## Split from monorepo

| Process | Env |
|---------|-----|
| Worker | `TELEMETRY_DRAIN_MODE=off` |
| Beat | `TELEMETRY_DRAIN_MODE=beat` |

## Extract from

`../backend/core/tasks.py`, `../backend/policy/tasks.py`, drainer modules.
