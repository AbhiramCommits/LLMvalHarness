COMPOSE = docker compose
RUN_API = $(COMPOSE) run --rm api

.PHONY: up down logs migrate test lint

up: ## build images and start all services
	$(COMPOSE) up -d --build

down: ## stop all services
	$(COMPOSE) down

logs: ## tail logs from all services
	$(COMPOSE) logs -f

migrate: ## apply alembic migrations
	$(RUN_API) alembic upgrade head

test: ## run the test suite
	$(RUN_API) env PYTEST_ADDOPTS="-o cache_dir=/tmp/.pytest_cache" pytest

coverage: ## run tests with coverage (>=80% required on app/graders, app/workers, app/api)
	$(RUN_API) env PYTEST_ADDOPTS="-o cache_dir=/tmp/.pytest_cache" pytest \
		--cov=app/graders --cov=app/workers --cov=app/api \
		--cov-report=term-missing --cov-fail-under=80

typecheck: ## run mypy
	$(RUN_API) env MYPY_CACHE_DIR=/tmp/.mypy_cache mypy app tests

lint: ## run ruff checks
	$(RUN_API) env RUFF_CACHE_DIR=/tmp/.ruff_cache ruff check app tests alembic scripts
	$(RUN_API) env RUFF_CACHE_DIR=/tmp/.ruff_cache ruff format --check app tests alembic scripts

load: ## seed 50 tasks x 3 models and run an eval against FakeProvider
	@key=$$(docker compose run --rm -T api python scripts/create_api_key.py load-admin admin | tail -1); \
	$(RUN_API) env EVAL_API_KEY=$$key python scripts/seed_and_run.py
