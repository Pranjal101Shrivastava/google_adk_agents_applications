# Google ADK Agent Applications - monorepo targets
#
# Each agent is also independently usable: `cd 0N-... && make help`.

PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
AGENTS := 01-lead-generation-deep-research \
          02-gemini-cli-tool-agent \
          03-mcp-bug-assistant \
          04-code-review-assistant \
          05-ecommerce-agent

.PHONY: help venv install test test-% lint lint-% check agents clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_%-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Per-agent: make test-01, make lint-03, etc."

venv: ## Create the shared virtualenv
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

install: venv ## Install every agent's dependencies into the shared venv
	$(BIN)/pip install -r requirements-dev.txt
	@for agent in $(AGENTS); do \
		echo "--> $$agent"; \
		$(BIN)/pip install -q -r $$agent/requirements.txt; \
	done

test: ## Run every agent's test suite (agent by agent, as CI does)
	@total=0; \
	for agent in $(AGENTS); do \
		printf "\033[36m--> %s\033[0m\n" "$$agent"; \
		(cd $$agent && ../$(BIN)/python -m pytest tests/ -q) || exit 1; \
	done; \
	printf "\033[32mall agent suites passed\033[0m\n"

test-%: ## Run one agent's tests, e.g. make test-04
	@agent=$$(ls -d $**/ 2>/dev/null | head -1); \
	printf "\033[36m--> %s\033[0m\n" "$$agent"; \
	cd "$$agent" && ../$(BIN)/python -m pytest tests/ -v

lint: ## Lint every agent
	@for agent in $(AGENTS); do \
		echo "--> $$agent"; \
		$(BIN)/ruff check $$agent --config $$agent/pyproject.toml || exit 1; \
	done

lint-%: ## Lint one agent, e.g. make lint-02
	@agent=$$(ls -d $**/ 2>/dev/null | head -1); \
	$(BIN)/ruff check "$$agent" --config "$$agent/pyproject.toml"

check: lint test ## Lint and test everything

agents: ## List the agents and their entrypoints
	@for agent in $(AGENTS); do \
		pkg=$$(find $$agent -maxdepth 1 -name '*_agent' -o -maxdepth 1 -name '*_assistant' | head -1); \
		printf "  %-36s %s\n" "$$agent" "$$(basename $$pkg)"; \
	done

clean: ## Remove caches across the repo
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
