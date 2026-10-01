PYTHON ?= python
NPM ?= npm
COMPOSE ?= docker compose

API_DIR := apps/api
WEB_DIR := apps/web

.PHONY: up down db-bootstrap db-check test lint

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
