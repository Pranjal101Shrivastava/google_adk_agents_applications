# Code Review Assistant
VENV ?= ../.venv
BIN := $(VENV)/bin

.PHONY: help install dev sandbox web run test lint eval clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime dependencies
	$(BIN)/pip install -r requirements.txt

dev: ## Install runtime + dev dependencies
	$(BIN)/pip install -r requirements-dev.txt

sandbox: ## Report how test execution is configured and isolated
	$(BIN)/python -c "import json; from code_review_assistant.tools import execution_environment_report as r; print(json.dumps(r(), indent=2))"

web: ## Launch the ADK dev UI on http://localhost:8000
	$(BIN)/adk web .

run: ## Run the agent in the terminal
	$(BIN)/adk run code_review_assistant

test: ## Run the offline test suite
	$(BIN)/python -m pytest tests/ -v

lint: ## Lint the package
	$(BIN)/ruff check code_review_assistant tests

eval: ## Run the ADK eval sets (requires model credentials)
	$(BIN)/adk eval code_review_assistant eval/review.evalset.json --config_file_path eval/test_config.json
	$(BIN)/adk eval code_review_assistant eval/fix.evalset.json --config_file_path eval/test_config.json

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache **/__pycache__ __pycache__
