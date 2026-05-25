# shared (ai_mesh_shared)

Python package consumed by `gateway`, `control`, `workers`, and `services/*`.

- `jobs/` — Redis queue keys, Celery queue constants, `JobEnvelope`
- `contracts/` — contract version markers (JSON Schema in `docs/contracts/`)

Do not import Django or FastAPI here — keep dependency-free.
