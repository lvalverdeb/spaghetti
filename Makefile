# spaghetti is its own git repo nested inside the uv workspace root, so
# `git rev-parse --show-toplevel` would return spaghetti's own root, not
# the workspace root where `uv build` actually writes dist/ — derive it
# from the Makefile's own location instead (always one level up).
REPO_ROOT := $(shell realpath $(dir $(realpath $(lastword $(MAKEFILE_LIST))))..)

.PHONY: clean build publish test lint format-check verify check reinstall help

clean: ## Remove build artifacts
	rm -rf $(REPO_ROOT)/dist/ $(REPO_ROOT)/build/ src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

build: clean ## Build sdist and wheel
	uv build

publish: verify build ## Publish to PyPI using token from .env
	@test -f .env || (echo "ERROR: .env file not found"; exit 1)
	@uv publish --token "$$(grep UV_PUBLISH_TOKEN .env | cut -d= -f2)" $$(ls $(REPO_ROOT)/dist/spaghetti_detector-*)

test: ## Run tests
	uv run pytest tests/ -q

lint: ## Run ruff linter (matches CI: src/ and tests/)
	uv run ruff check src/ tests/

format-check: ## Check formatting without applying (matches CI)
	uv run ruff format --check src/ tests/

# Mirrors .github/workflows/ci.yml's test + lint jobs exactly, so a local
# publish can't happen without the same checks CI enforces.
verify: test lint format-check ## Run the exact checks CI runs

check: lint test ## Run lint + tests

reinstall: build ## Reinstall locally for testing
	uv pip install $$(ls $(REPO_ROOT)/dist/spaghetti_detector-*.whl) --force-reinstall

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'
