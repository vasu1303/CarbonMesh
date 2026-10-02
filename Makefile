PYTHON ?= python
NPM ?= npm
COMPOSE ?= docker compose

API_DIR := apps/api
WEB_DIR := apps/web

.PHONY: up down db-bootstrap db-check test lint seed-demo reset-demo

up:
	$(COMPOSE) up --build

down:
	$(COMPOSE) down

db-bootstrap:
	$(COMPOSE) build api
	$(COMPOSE) run --rm api python -m app.db.bootstrap

db-check:
	$(COMPOSE) build api
	$(COMPOSE) run --rm api python -m app.db.bootstrap --check

test:
	cd $(API_DIR) && $(PYTHON) -m pytest -q

lint:
	cd $(API_DIR) && $(PYTHON) -m ruff check app tests
	$(NPM) --prefix $(WEB_DIR) run lint
	$(NPM) --prefix $(WEB_DIR) run typecheck

# Compose commands share the same local database configuration as bootstrap.
# Both exact target identifiers are required; seed refuses any occupied database.
seed-demo:
	$(COMPOSE) run --rm api python -m app.modules.demo.cli seed --endpoint "$(ENDPOINT)" --database "$(DATABASE)"

reset-demo:
	$(COMPOSE) run --rm api python -m app.modules.demo.cli reset --endpoint "$(ENDPOINT)" --database "$(DATABASE)"
