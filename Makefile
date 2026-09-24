.PHONY: install lint format typecheck test test-unit test-integration check mongo-up mongo-down

install:          ## Create .venv and install mongomig with dev extras (needs uv)
	uv venv --python 3.12 .venv
	uv pip install -e ".[dev]"

lint:
	.venv/bin/ruff check src tests
	.venv/bin/ruff format --check src tests

format:
	.venv/bin/ruff check --fix src tests
	.venv/bin/ruff format src tests

typecheck:
	.venv/bin/mypy

test:             ## All tests (integration tests skip if MongoDB is not running)
	.venv/bin/pytest

test-unit:
	.venv/bin/pytest tests/unit

test-integration: ## Requires `make mongo-up`
	MONGOMIG_REQUIRE_MONGO=1 .venv/bin/pytest tests/integration

check: lint typecheck test

mongo-up:
	docker compose up -d --wait

mongo-down:
	docker compose down -v
