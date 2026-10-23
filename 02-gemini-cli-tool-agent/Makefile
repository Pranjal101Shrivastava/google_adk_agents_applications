# Gemini CLI Tool Agent
VENV ?= ../.venv
BIN := $(VENV)/bin

.PHONY: help install dev cli check web run test lint eval clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime dependencies
	$(BIN)/pip install -r requirements.txt

dev: ## Install runtime + dev dependencies
	$(BIN)/pip install -r requirements-dev.txt

cli: ## Install the Gemini CLI globally (requires Node 20+)
	npm install -g @google/gemini-cli
	gemini --version

check: ## Verify the agent can see the Gemini CLI
	$(BIN)/python -c "from gemini_cli_agent.tools import check_gemini_cli; import json; print(json.dumps(check_gemini_cli(), indent=2))"

web: ## Launch the ADK dev UI on http://localhost:8000
	$(BIN)/adk web .

run: ## Run the agent in the terminal
	$(BIN)/adk run gemini_cli_agent

test: ## Run the offline test suite
	$(BIN)/python -m pytest tests/ -v

lint: ## Lint the package
	$(BIN)/ruff check gemini_cli_agent tests

eval: ## Run the ADK eval sets (requires model credentials + the CLI)
	$(BIN)/adk eval gemini_cli_agent eval/workspace_lifecycle.evalset.json --config_file_path eval/test_config.json
	$(BIN)/adk eval gemini_cli_agent eval/cli_delegation.evalset.json --config_file_path eval/test_config.json

clean: ## Remove caches and any leftover workspaces
	rm -rf .pytest_cache .ruff_cache **/__pycache__ __pycache__
	$(BIN)/python -c "from gemini_cli_agent.workspace import destroy_all_workspaces; print(f'removed {destroy_all_workspaces()} workspace(s)')"
