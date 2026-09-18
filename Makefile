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

lint: ## run ruff checks
	$(RUN_API) env RUFF_CACHE_DIR=/tmp/.ruff_cache ruff check app tests alembic
	$(RUN_API) env RUFF_CACHE_DIR=/tmp/.ruff_cache ruff format --check app tests alembic
