# Verification — Module 1.6 telemetry

## Automated

```bash
cd control/ai_mesh_control && python manage.py test core.tests.test_module_16_telemetry -v2
```

Or via Docker:

```bash
docker compose exec control python manage.py test core.tests.test_module_16_telemetry -v2
```

## Live (stack running)

1. Trigger kill-switch or use IsolationOpsSimulator on `http://localhost:8180/?tab=firewall-1-6`
2. Ensure Celery drains telemetry (or run drain in control shell)
3. `curl -H "Authorization: Bearer $TOKEN" "http://localhost:8100/api/security/module-kpis/?period=24h"` → `modules["1.6"].total > 0`
4. `curl ... "/api/security/module-trends/?period=24h"` → non-zero `1.6` series
5. `curl ... "/api/security/threat-feed/?module_id=1.6&limit=50"` → enforcement + `audit-*` rows
6. Homepage AI Mesh firewall overview → submodule 1.6 card chart/metrics non-zero

## Sign-off

| Check | Status |
|-------|--------|
| Django tests pass | OK (9/9) |
| module-kpis 1.6 | |
| module-trends 1.6 | |
| threat-feed merge | |
| UI evidence table | |
| Homepage 1.6 chart | |
