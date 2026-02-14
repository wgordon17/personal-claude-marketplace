.PHONY: all lint format test prek

all: lint test  ## Full check suite (lint + test)

lint:  ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format:  ## Auto-format with ruff
	uv run ruff format .
	uv run ruff check --fix .

test:  ## Run pytest
	uv run pytest

prek:  ## Run pre-commit on all files
	uvx prek run --all-files
