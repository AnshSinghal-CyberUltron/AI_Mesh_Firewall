# Module 2 — Production Deploy Runbook

Date: 2026-06-30  
Branch: `module2`  
Rollback commit (`origin/main`): `c3af308c`

## Pre-deploy (local / CI)

1. Merge or tag the release commit on `module2` (includes entrypoint migrations, frontend build fix, admin bootstrap).
2. Build and push ECR images:
   ```bash
   export ECR_REGISTRY=<account>.dkr.ecr.ap-south-1.amazonaws.com IMAGE_TAG=v<release>
   bash infra/scripts/build-push-images.sh
   ```
3. Optional local verification:
   ```powershell
   docker compose down -v
   docker compose up --build -d
   .\scripts\e2e-release-smoke.ps1
   docker compose exec -T control python manage.py test module2.tests
   docker compose exec -T frontend sh -c "cd /app && npm run build"
   ```

## EC2 production `.env` (never commit)

| Variable | Required | Notes |
|----------|----------|-------|
| `DJANGO_SECRET_KEY` | Yes | 64+ char random |
| `POLICY_SIGNING_KEY` | Yes | Same on control, gateway, workers |
| `ZEROSHIELD_ADMIN_PASSWORD` | Yes | First-login admin; entrypoint + `deploy-ec2.sh` use this |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Yes* | Or EC2 instance role with Bedrock access |
| `BEDROCK_REGION` | Yes | e.g. `ap-south-1` |
| `ENABLE_TIER2` | Yes | `true` for full scanning |
| `DEBUG` | Yes | `false` |
| `SIMULATOR_DEFAULTS_ENABLED` | Prod | `false` (default in prod overlay) |
| `TELEMETRY_DRAIN_MODE` | Prod | `beat` (workers profile) |
| `GATEWAY_INTERNAL_API_KEY` | Recommended | Control ↔ gateway internal calls |
| `ECR_REGISTRY` / `IMAGE_TAG` | Yes | Set before `docker compose pull` |

\* Tier-2 Bedrock without credentials causes fail-closed blocks when `tier2_fail_closed_enabled=true`.

## Deploy on EC2

```bash
export ECR_REGISTRY=... IMAGE_TAG=v<release>
export ZEROSHIELD_ADMIN_PASSWORD='<strong-password>'
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
bash scripts/deploy-ec2.sh
```

`deploy-ec2.sh` waits for Postgres, starts control (entrypoint runs `migrate` + `ensure_zeroshield_admin`), runs an extra `migrate` (idempotent), then starts gateway/workers/nginx.

Post-deploy smoke:

```bash
bash infra/scripts/deploy-smoke.sh   # requires Terraform outputs / DNS
curl -sf https://aimeshgateway.zeroshield.ai/health
curl -sf https://aimeshbackend.zeroshield.ai/api/health/
```

## Rollback

```bash
export IMAGE_TAG=<previous-stable-tag>   # or rebuild from c3af308c on main
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
bash scripts/deploy-ec2.sh
```

Git reference for last known `main`: `c3af308c`.

## Still manual before calling production “done”

- [ ] Dashboard period switch stress (1h / 24h / 7d)
- [ ] Incident queue UX (escalate / resolve under filters)
- [ ] Threat intel create / edit / delete + sync visibility
- [ ] UEBA containment (disable key, kill switch activate/deactivate)
- [ ] WebSocket connected / disconnected fallback in UI
- [ ] Login at `https://aimeshfirewall.zeroshield.ai` with production admin password
- [ ] Attack simulator or gateway test → Module 2 dashboard shows telemetry
- [ ] Confirm gateway `/health` is 200 (not 503 `policy_signing_key_missing`)
