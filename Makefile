.PHONY: up down ps logs migrate extract-plan

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

extract-plan:
	@echo "See docs/MIGRATION_FROM_AIGUARDX.md for phased copy from parent AI_Security repo"
