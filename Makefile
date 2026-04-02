.PHONY: all lint format test test-llm typecheck prek prek-install

all: lint test typecheck  ## Full check suite (lint + test + typecheck)

lint:  ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format:  ## Auto-format with ruff
	uv run ruff format .
	uv run ruff check --fix .

test:  ## Run pytest (excludes LLM integration tests)
	uv run pytest -m "not llm"

test-llm:  ## Run LLM integration tests (requires Vertex AI credentials)
	uv run --group dev --group llm pytest -m llm -v

typecheck:  ## Pyright type checking (dev-guard/hooks)
	uvx pyright@1.1.408

prek:  ## Run pre-commit on all files
	uvx prek run --all-files

prek-install:  ## Install pre-commit + pre-push hooks
	uvx prek install --install-hooks --hook-type pre-commit --hook-type pre-push
