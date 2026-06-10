.PHONY: up down ps logs migrate extract-plan frontend-build

up:
	docker compose up -d postgres redis rabbitmq control gateway frontend

up-workers:
	docker compose up -d workers workers-beat

up-all:
	docker compose --profile services --profile telemetry-pilot up -d

down:
	docker compose down

ps:
	docker compose ps -a

logs:
	docker compose logs -f --tail=100

migrate:
	docker compose exec control python manage.py migrate

rebuild-control:
	docker compose build control
	docker compose up -d control

seed-pii-policy:
	@test -n "$(ORG_SLUG)" || (echo "Usage: make seed-pii-policy ORG_SLUG=zeroshield" && exit 1)
	docker compose exec -T control python manage.py seed_pii_policy_package --org-slug $(ORG_SLUG)

extract-plan:
	@echo "See docs/MIGRATION_FROM_AIGUARDX.md for phased copy from parent AI_Security repo"

deploy-ec2:
	bash scripts/deploy-ec2.sh

sync-ec2:
	bash scripts/sync-to-ec2.sh

sync-ec2-deploy:
	bash scripts/sync-to-ec2.sh --deploy

frontend-build:
	bash infra/scripts/build-frontend-prod.sh

# Full pipeline: local ECR build/push → frontend build → sync deploy config → EC2 pull
deploy-full-ec2:
	bash scripts/deploy-full-ec2.sh $(if $(TAG),--tag $(TAG),)

ecr-push:
	bash infra/scripts/build-push-images.sh $(TAG)

attach-ec2-iam:
	bash scripts/attach-ec2-iam-policy.sh

observability-apply:
	cd infra/terraform/envs/ec2-demo && terraform init && terraform apply -var-file=ec2-demo.tfvars

verify-observability:
	bash scripts/verify-observability-local.sh
