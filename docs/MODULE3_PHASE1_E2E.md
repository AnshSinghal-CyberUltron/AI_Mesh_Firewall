# Module 3 — Phase 1 + Both Pages E2E Runbook

Commands only. Defaults: control `http://127.0.0.1:8100`, demo admin credentials from other e2e scripts.

## 1. Cosign production spine (M3.1)

```bash
bash scripts/module3_gen_cosign_keypair.sh
# Ensure GATEWAY_INTERNAL_API_KEY is set in .env (control + gateway)
docker compose -f docker-compose.yml -f docker-compose.module3-verify.yml up -d gateway
# start Celery workers (deny → Module 2)
docker compose -f docker-compose.yml -f docker-compose.module3-verify.yml run --rm --no-deps \
  -v "$PWD/scripts:/scripts:ro" \
  -e CONTROL_URL=http://control:8000 \
  -e COSIGN_KEY=/run/secrets/cosign.key \
  -e TEST_EMAIL=admin@zeroshield.io \
  -e TEST_PASSWORD='…' \
  -e REQUIRE_VERIFY=1 \
  --entrypoint python3 gateway /scripts/module3_admission_e2e.py
```

UI: `/infrastructure/llmops` → Register artifact → Verify.  
Simulator digests only work with `MODULE3_ADMISSION_MODE=passthrough`.

## 2. Live K8s telemetry (M3.2)

```bash
export AGENT_API_KEY=...          # global key or OrganizationAgentKey
export ORG_SLUG=zeroshield          # required for global AGENT_API_KEY
bash scripts/module3_ingest_agent_demo.sh
# or:
bash scripts/module3_ingest_smoke.sh
```

UI: `/infrastructure/k8s-firewall` → topology, network events, embedding queue.  
Network drops and quarantines open Module 2 incidents (workers required).

## 3. Migrations

```bash
# control container / venv
python manage.py migrate module3
```

(`0002_widen_signature_digest` — Cosign base64 signatures exceed 512 chars.)

## 4. DVC / fingerprints

See [MODULE3_PHASE1_DVC_FINGERPRINTS.md](./MODULE3_PHASE1_DVC_FINGERPRINTS.md).

## 5. Sample GitHub Actions Cosign admission

See `.github/workflows/module3-cosign-admission-sample.yml` (manual/`workflow_dispatch`; requires secrets listed in the workflow header).
