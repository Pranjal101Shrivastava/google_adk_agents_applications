# E-Commerce Agent
VENV ?= ../.venv
BIN := $(VENV)/bin
TOOLBOX_VERSION ?= 0.19.0
LOCAL_DB_URL ?= postgresql://postgres:storefront@127.0.0.1:5432/storefront

.PHONY: help install dev db-up db-init db-down toolbox toolbox-run web run serve test lint eval clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime dependencies
	$(BIN)/pip install -r requirements.txt

dev: ## Install runtime + dev dependencies
	$(BIN)/pip install -r requirements-dev.txt

db-up: ## Start local Postgres + Toolbox
	docker compose -f deployment/docker-compose.yaml up -d
	@echo "waiting for postgres..."
	@until docker compose -f deployment/docker-compose.yaml exec -T postgres pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
	@echo "ready"

db-init: ## Apply the local schema and seed data
	psql "$(LOCAL_DB_URL)" -v ON_ERROR_STOP=1 -f data/schema_local.sql
	psql "$(LOCAL_DB_URL)" -v ON_ERROR_STOP=1 -f data/seed.sql
	@echo "seeded $$(psql "$(LOCAL_DB_URL)" -tAc 'SELECT COUNT(*) FROM products') products, $$(psql "$(LOCAL_DB_URL)" -tAc 'SELECT COUNT(*) FROM product_variants') variants"

db-down: ## Stop and remove the local stack
	docker compose -f deployment/docker-compose.yaml down -v

toolbox: ## Download the MCP Toolbox binary
	curl -fsSL -o toolbox "https://storage.googleapis.com/genai-toolbox/v$(TOOLBOX_VERSION)/linux/amd64/toolbox"
	chmod +x toolbox
	./toolbox --version

toolbox-run: ## Run the Toolbox server against tools.yaml
	./toolbox --tools-file deployment/toolbox/tools.yaml --port 5000

web: ## Launch the ADK dev UI (guest mode - no token, so personal tools refuse)
	$(BIN)/adk web .

run: ## Run the agent in the terminal (guest mode)
	$(BIN)/adk run ecommerce_agent

serve: ## Run the FastAPI backend with verified identity on :8080
	$(BIN)/uvicorn backend.app:app --reload --port 8080

test: ## Run the offline test suite
	$(BIN)/python -m pytest tests/ -v

lint: ## Lint the package
	$(BIN)/ruff check ecommerce_agent backend tests

eval: ## Run the ADK eval sets (requires model credentials)
	$(BIN)/adk eval ecommerce_agent eval/shopping.evalset.json --config_file_path eval/test_config.json
	$(BIN)/adk eval ecommerce_agent eval/identity.evalset.json --config_file_path eval/test_config.json

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache **/__pycache__ __pycache__
