# control (control plane)

Django **modular monolith** for tenants, API keys, policies, model registry, kill switches, firewall config, compliance.

Publishes signed policy bundles to Redis; does **not** serve inference traffic.

## Extract from monorepo

`../backend/` (auth, policy, core firewall, security_engines, ws) — exclude device/agent_distribution.

## Run locally

```bash
cd control && uv run python manage.py migrate
uv run python manage.py runserver 0.0.0.0:8000
```
