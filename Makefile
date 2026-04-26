.PHONY: help dev migrate seed test test-int lint typecheck check down clean

help:
	@echo "lite-horse v0.4 dev targets:"
	@echo "  make dev       — bring up Postgres + Redis + MinIO + api + scheduler"
	@echo "  make down      — tear down compose stack"
	@echo "  make migrate   — apply Alembic migrations against local PG"
	@echo "  make seed      — seed bundled instructions/commands + local admin user"
	@echo "  make test      — unit tests (no docker required)"
	@echo "  make test-int  — integration tests (requires docker compose up)"
	@echo "  make lint      — ruff check"
	@echo "  make typecheck — mypy --strict"
	@echo "  make check     — lint + typecheck + test"

dev:
	docker compose up -d --build
	docker compose ps

down:
	docker compose down

migrate:
	uv run alembic -c src/lite_horse/alembic.ini upgrade head

seed:
	uv run python -m lite_horse.scripts.seed

test:
	uv run pytest -q

test-int:
	uv run pytest -q -m integration

lint:
	uv run ruff check src tests

typecheck:
	uv run mypy src

check: lint typecheck test
