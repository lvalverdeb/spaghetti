REPO_ROOT := $(shell git rev-parse --show-toplevel 2>/dev/null || dirname $(realpath $(lastword $(MAKEFILE_LIST)))/..)

.PHONY: clean build publish test lint check reinstall help

clean: ## Remove build artifacts
	rm -rf $(REPO_ROOT)/dist/ $(REPO_ROOT)/build/ src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

build: clean ## Build sdist and wheel
	uv build

publish: build ## Publish to PyPI using token from .env
	@test -f .env || (echo "ERROR: .env file not found"; exit 1)
	@uv publish --token "$$(grep UV_PUBLISH_TOKEN .env | cut -d= -f2)" $$(ls $(REPO_ROOT)/dist/spaghetti-*)

test: ## Run tests
	uv run pytest tests/ -q

lint: ## Run ruff linter
	uv run ruff check src/

check: lint test ## Run lint + tests

reinstall: build ## Reinstall locally for testing
	uv pip install $$(ls $(REPO_ROOT)/dist/spaghetti_detector-*.whl) --force-reinstall

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'
