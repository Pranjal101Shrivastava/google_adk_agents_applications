# MCP Bug Assistant
VENV ?= ../.venv
BIN := $(VENV)/bin
TOOLBOX_VERSION ?= 0.19.0
LOCAL_DB_URL ?= postgresql://postgres:admin@127.0.0.1:5432/ticketsdb

.PHONY: help install dev inventory db-up db-down db-init toolbox toolbox-run web run test lint eval clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime dependencies
	$(BIN)/pip install -r requirements.txt

dev: ## Install runtime + dev dependencies
	$(BIN)/pip install -r requirements-dev.txt

inventory: ## Print which tool groups are available and why
	$(BIN)/python -c "from software_bug_assistant.agent import tool_inventory; print(tool_inventory)"

db-up: ## Start local Postgres with pgvector
	docker compose -f deployment/docker-compose.yaml up -d postgres
	@echo "waiting for postgres..."
	@until docker compose -f deployment/docker-compose.yaml exec -T postgres pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
	@echo "postgres ready"

db-init: ## Apply the local schema and seed data
	psql "$(LOCAL_DB_URL)" -f data/schema_local.sql
	psql "$(LOCAL_DB_URL)" -f data/seed.sql
	@echo "seeded $$(psql "$(LOCAL_DB_URL)" -tAc 'SELECT COUNT(*) FROM tickets') tickets"

db-down: ## Stop and remove the local Postgres
	docker compose -f deployment/docker-compose.yaml down -v

toolbox: ## Download the MCP Toolbox binary
	curl -fsSL -o toolbox "https://storage.googleapis.com/genai-toolbox/v$(TOOLBOX_VERSION)/linux/amd64/toolbox"
	chmod +x toolbox
	./toolbox --version

toolbox-run: ## Run the Toolbox server against tools.yaml
	./toolbox --tools-file deployment/mcp-toolbox/tools.yaml --port 5000

web: ## Launch the ADK dev UI on http://localhost:8000
	$(BIN)/adk web .

run: ## Run the agent in the terminal
	$(BIN)/adk run software_bug_assistant

test: ## Run the offline test suite
	$(BIN)/python -m pytest tests/ -v

lint: ## Lint the package
	$(BIN)/ruff check software_bug_assistant tests

eval: ## Run the ADK eval sets (requires model credentials)
	$(BIN)/adk eval software_bug_assistant eval/triage.evalset.json --config_file_path eval/test_config.json
	$(BIN)/adk eval software_bug_assistant eval/ticket_management.evalset.json --config_file_path eval/test_config.json

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache **/__pycache__ __pycache__
