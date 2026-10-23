# Deep Research Lead Generation Agent
PY ?= python3
VENV ?= ../.venv
BIN := $(VENV)/bin

.PHONY: help install dev web run test lint eval clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime dependencies
	$(BIN)/pip install -r requirements.txt

dev: ## Install runtime + dev dependencies
	$(BIN)/pip install -r requirements-dev.txt

web: ## Launch the ADK dev UI on http://localhost:8000
	$(BIN)/adk web .

run: ## Run the agent in the terminal
	$(BIN)/adk run lead_generation_agent

test: ## Run the offline test suite
	$(BIN)/python -m pytest tests/ -v

lint: ## Lint the package
	$(BIN)/ruff check lead_generation_agent tests

eval: ## Run the ADK eval sets (requires model credentials)
	$(BIN)/adk eval lead_generation_agent eval/clarification.evalset.json --config_file_path eval/test_config.json
	$(BIN)/adk eval lead_generation_agent eval/pattern_discovery.evalset.json --config_file_path eval/test_config.json
	$(BIN)/adk eval lead_generation_agent eval/lead_generation.evalset.json --config_file_path eval/test_config.json

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache **/__pycache__ __pycache__
