# digimod: custom Elektron firmware from mods, verified before you flash.
#
#   make help     every target
#   make up       the build service in Docker  -> http://127.0.0.1:8700
#   make setup    once, on macOS: the native tools and the emulator
#   make run      natively, with the emulator  -> http://127.0.0.1:8700
#
# .env holds the defaults; .env.local (not in git) overrides them.

include .env
-include .env.local
export

COMPOSE := docker compose -f docker-compose.yaml -p $(SRV)
PY      := .venv/bin/python

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "digimod $(VERSION)"
	@echo ""
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ---- Docker: the build service (no emulator) -----------------------------------------------------------------

.PHONY: image up down restart logs ps shell test prune

image: ## Build the Docker image
	$(COMPOSE) build

up: image ## Start the service (http://127.0.0.1:PORT, 8700 by default)
	@mkdir -p firmware data
	$(COMPOSE) up -d
	@echo ""
	@echo "  digimod -> http://127.0.0.1:$(PORT)"
	@echo "  put official OS .syx files into firmware/ (or add them on the page)"
	@echo ""

down: ## Stop the service
	$(COMPOSE) down --remove-orphans

restart: ## Restart the service
	$(COMPOSE) restart

logs: ## Follow the service's log
	$(COMPOSE) logs -f --tail 100

ps: ## Show the container
	$(COMPOSE) ps

shell: ## A shell in the container
	@$(COMPOSE) exec digimod bash 2>/dev/null || $(COMPOSE) run --rm digimod bash

test: image ## Smoke test in Docker: build every mod onto every firmware in firmware/
	$(COMPOSE) run --rm --no-deps digimod python tests/smoke.py

prune: ## Remove dangling images and the build cache
	docker image prune -f
	docker builder prune -f

# ---- Native, macOS: the service with the emulator ------------------------------------------------------------

.PHONY: setup run test-native

setup: ## Once: Python env, firmware tool, Unicorn and the emulator engine (a few minutes)
	@test "$$(uname)" = Darwin || { echo "the emulator builds on macOS only; use make up"; exit 1; }
	@test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -q cmake ninja
	emulator/build.sh

run: ## Start natively with the emulator and open the page
	@test -x data/build/elektron-firmware-tool || { echo "run make setup first"; exit 1; }
	$(PY) -u server/app.py --port $(PORT) --open

test-native: ## Smoke test natively
	$(PY) tests/smoke.py
