.PHONY: all lint format test test-llm typecheck prek prek-install eval eval-prepush eval-update-baselines eval-composition

all: lint test typecheck  ## Full check suite (lint + test + typecheck)

lint:  ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format:  ## Auto-format with ruff
	uv run ruff format .
	uv run ruff check --fix .

test:  ## Run pytest (excludes LLM integration tests)
	uv run pytest -m "not llm and not eval"

test-llm:  ## Run LLM integration tests (requires Vertex AI credentials)
	uv run --group dev --group llm pytest -m llm -v

typecheck:  ## Pyright type checking (dev-guard/hooks)
	uv run pyright

prek:  ## Run pre-commit on all files
	uvx prek run --all-files

prek-install:  ## Install pre-commit + pre-push hooks
	uvx prek install --install-hooks --hook-type pre-commit --hook-type pre-push

eval-prepush:  ## Pre-push: eval only changed skills
	cd skill-eval && uv run python -m skill_eval.cli

eval:  ## Run skill evals for all skills with test cases
	cd skill-eval && uv run python -m skill_eval.cli --all

eval-update-baselines:  ## Update baselines.json with current scores
	cd skill-eval && uv run python -m skill_eval.cli --update-baselines

eval-composition:  ## Run composition eval (set CONFIG=path/to/composition.json)
	cd skill-eval && uv run python -m skill_eval.cli --composition $(CONFIG)
