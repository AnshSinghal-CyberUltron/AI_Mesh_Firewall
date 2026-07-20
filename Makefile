# AI Mesh Firewall — local + production deploy targets
#
# Local (dev):
#   make up | make down | make ps | make logs | make migrate
#
# Production (ECR → EC2):
#   make verify-prod-local          # local gate (no SSH / no :80 steal)
#   make deploy-full-ec2 TAG=vX.Y.Z  # build UI → push ECR → sync → remote up
#
# Prod-like locally (isolated ports 18xxx, needs local images):
#   make build-prod-images-local && make up-prod-local

.PHONY: help up up-demo up-workers up-all down ps logs migrate rebuild-control seed-pii-policy \
	extract-plan deploy-ec2 sync-ec2 sync-ec2-deploy frontend-build deploy-full-ec2 \
	ecr-push attach-ec2-iam observability-apply verify-observability \
	test-prod-compose verify-prod-local build-prod-images-local up-prod-local down-prod-local \
	prod-config mcp-adversarial-gate mcp-adversarial-reset-prd mcp-adversarial-reset \
	mcp-adversarial-iter mcp-adversarial-loop

COMPOSE := docker compose
COMPOSE_PROD := docker compose -f docker-compose.yml -f docker-compose.prod.yml
COMPOSE_PROD_LOCAL := docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.prod.local.yml

# Defaults for local prod-like runs (override: make up-prod-local ECR_REGISTRY=… IMAGE_TAG=…)
ECR_REGISTRY ?= local.ecr.test
IMAGE_TAG ?= local
export ECR_REGISTRY IMAGE_TAG

help:
	@echo "AI Mesh Firewall — make targets"
	@echo ""
	@echo "Local development"
	@echo "  make up                 Start postgres redis rabbitmq control gateway demo frontend"
	@echo "  make up-demo            Build/start OpenAI SDK demo only (:8770 + /demo via :8180)"
	@echo "  make up-workers         Start celery workers + beat"
	@echo "  make up-all             Start all profiles (services + telemetry-pilot)"
	@echo "  make down / ps / logs   Stack control"
	@echo "  make migrate            Run Django migrations"
	@echo "  Demo UI: http://127.0.0.1:8180/demo/  (console login → org gateway key)"
	@echo ""
	@echo "Production deploy (end-to-end)"
	@echo "  make verify-prod-local  Validate prod compose + deploy scripts (local)"
	@echo "  make test-prod-compose  Compose config / URL / ECR parity gate"
	@echo "  make frontend-build     Vite production build (VITE_* from .env)"
	@echo "  make ecr-push TAG=vX    Build+push gateway/control/workers/nginx/demo/mcp-* to ECR"
	@echo "  make sync-ec2           Rsync compose+.env+scripts to EC2 (no app source)"
	@echo "  make sync-ec2-deploy    Sync then remote deploy-ec2.sh"
	@echo "  make deploy-ec2         On EC2: pull ECR + compose prod up + health checks"
	@echo "  make deploy-full-ec2 TAG=vX   LOCAL→ECR→EC2 full pipeline"
	@echo ""
	@echo "Prod-like local (ports 18xxx; does not replace running :8180 stack)"
	@echo "  make build-prod-images-local   Build all ECR-named images locally (no push)"
	@echo "  make up-prod-local             compose.prod + prod.local up"
	@echo "  make down-prod-local           Tear down prodlocal project"
	@echo "  make prod-config               Render merged prod compose config"
	@echo ""
	@echo "Hosts: aimeshfirewall | aimeshbackend | aimeshgateway .zeroshield.ai"

# ── Local development ─────────────────────────────────────────────────────────
up:
	$(COMPOSE) up -d postgres redis rabbitmq control gateway demo frontend

up-demo:
	$(COMPOSE) build demo
	$(COMPOSE) up -d demo
	@echo "Demo: http://127.0.0.1:8770/  and  http://127.0.0.1:8180/demo/"

up-workers:
	$(COMPOSE) --profile workers up -d workers workers-beat

up-all:
	$(COMPOSE) --profile services --profile workers --profile telemetry-pilot up -d

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps -a

logs:
	$(COMPOSE) logs -f --tail=100

migrate:
	$(COMPOSE) exec control python manage.py migrate

rebuild-control:
	$(COMPOSE) build control
	$(COMPOSE) up -d control

seed-pii-policy:
	@test -n "$(ORG_SLUG)" || (echo "Usage: make seed-pii-policy ORG_SLUG=zeroshield" && exit 1)
	$(COMPOSE) exec -T control python manage.py seed_pii_policy_package --org-slug $(ORG_SLUG)

extract-plan:
	@echo "See docs/MIGRATION_FROM_AIGUARDX.md for phased copy from parent AI_Security repo"

# ── Production compose validation (local) ─────────────────────────────────────
test-prod-compose:
	bash scripts/test_docker_compose_prod.sh

verify-prod-local:
	bash scripts/verify_prod_local.sh

prod-config:
	@echo "Merged prod services:"
	@$(COMPOSE_PROD) config --services | sort
	@$(COMPOSE_PROD) config >/dev/null && echo "OK: compose config renders"

build-prod-images-local:
	bash scripts/build-prod-images-local.sh $(if $(TAG),--tag $(TAG),)

up-prod-local:
	@echo "==> Ensuring mcp_sandbox_bridge network"
	@bash scripts/ensure_mcp_sandbox_network.sh
	@echo "==> Starting prod-like stack (nginx http://127.0.0.1:18080)"
	$(COMPOSE_PROD_LOCAL) up -d postgres redis rabbitmq
	@sleep 3
	$(COMPOSE_PROD_LOCAL) up -d control
	@echo "Waiting for control health on :18100..."
	@for i in $$(seq 1 40); do curl -sf http://127.0.0.1:18100/api/health/ >/dev/null 2>&1 && break; sleep 2; done
	curl -sf http://127.0.0.1:18100/api/health/ >/dev/null \
	  || (echo "control not healthy — $(COMPOSE_PROD_LOCAL) logs control --tail=50"; exit 1)
	$(COMPOSE_PROD_LOCAL) exec -T control python /app/control/manage.py migrate --noinput || true
	$(COMPOSE_PROD_LOCAL) up -d \
	  mcp-sandbox-image mcp-broker guardrails vector-retrieval \
	  gateway workers workers-beat nginx demo
	@echo "Waiting for gateway :18300 + nginx :18080..."
	@for i in $$(seq 1 30); do curl -sf http://127.0.0.1:18300/health >/dev/null 2>&1 && break; sleep 2; done
	@curl -sf http://127.0.0.1:18300/health | head -c 200; echo
	@curl -sf -H "Host: aimeshfirewall.zeroshield.ai" http://127.0.0.1:18080/gw-health \
	  || (echo "nginx→gateway proxy failed"; exit 1)
	@curl -sf -H "Host: aimeshbackend.zeroshield.ai" http://127.0.0.1:18080/api/health/ \
	  || (echo "nginx→control proxy failed"; exit 1)
	@curl -sf -H "Host: aimeshgateway.zeroshield.ai" http://127.0.0.1:18080/health \
	  || (echo "nginx gateway vhost failed"; exit 1)
	@echo "OK prod-local: UI http://127.0.0.1:18080 (Host: aimeshfirewall.zeroshield.ai)"

down-prod-local:
	$(COMPOSE_PROD_LOCAL) down

# ── ECR → EC2 deploy ─────────────────────────────────────────────────────────
deploy-ec2:
	bash scripts/deploy-ec2.sh

sync-ec2:
	bash scripts/sync-to-ec2.sh

sync-ec2-deploy:
	bash scripts/sync-to-ec2.sh --deploy

frontend-build:
	bash infra/scripts/build-frontend-prod.sh

# Full pipeline: local ECR build/push → frontend build → sync deploy config → EC2 pull
deploy-full-ec2: verify-prod-local
	bash scripts/deploy-full-ec2.sh $(if $(TAG),--tag $(TAG),)

ecr-push:
	bash infra/scripts/build-push-images.sh $(TAG)

attach-ec2-iam:
	bash scripts/attach-ec2-iam-policy.sh

observability-apply:
	cd infra/terraform/envs/ec2-demo && terraform init && terraform apply -var-file=ec2-demo.tfvars

verify-observability:
	bash scripts/verify-observability-local.sh

# ── MCP frontend adversarial ──────────────────────────────────────────────────
mcp-adversarial-gate:
	node scripts/ralph/run_parallel_org_agents.mjs --full
	cd gateway && MCP_ADVERSARIAL_USE_LIVE_BROKER=true MCP_ADVERSARIAL_RESET_SANDBOXES=true MCP_BROKER_URL=http://127.0.0.1:8311 MCP_BROKER_INTERNAL_KEY=dev-mcp-broker-key-change-me ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py -q
	BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs

mcp-adversarial-reset-prd:
	@test -f scripts/ralph/prd-mcp-frontend-adversarial.json || (echo "PRD not found" && exit 1)
	@tmp=$$(mktemp) && jq '(.userStories[] | select(.id | startswith("R")) | .passes) = false' scripts/ralph/prd-mcp-frontend-adversarial.json >$$tmp && mv $$tmp scripts/ralph/prd-mcp-frontend-adversarial.json
	@echo "PRD: R1–R16 reset to passes:false (F stories unchanged)"

mcp-adversarial-reset:
	bash scripts/ralph/mcp-adversarial-reset.sh

mcp-adversarial-iter:
	@test -n "$(ITER)" || (echo "Usage: make mcp-adversarial-iter ITER=1" && exit 1)
	bash scripts/ralph/run_logical_iteration.sh $(ITER)

mcp-adversarial-loop:
	@echo "Cursor Ralph: say 'continue Ralph loop' in chat (see README-CURSOR-RALPH.md)"
	@echo "Terminal alternative:"
	@echo "  cd $(CURDIR) && for i in \$$(seq 1 20); do ./scripts/ralph/run_logical_iteration.sh \$$i || exit 1; done"
