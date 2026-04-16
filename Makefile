.PHONY: all lint format test test-llm typecheck prek prek-install eval eval-locked eval-compare eval-update-baselines eval-compare-scoring eval-with-context

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

eval:  ## Run skill evals for all skills with test cases
	cd skill-eval && uv run python -m skill_eval.hook --all

eval-locked:  ## Run skill evals with integrity check (for LLM self-improvement)
	cd skill-eval && uv run python -m skill_eval.hook --all --locked

eval-compare:  ## A/B compare current skills against origin/main
	cd skill-eval && uv run python -m skill_eval.hook --compare origin/main

eval-update-baselines:  ## Update baselines.json with current scores
	cd skill-eval && uv run python -m skill_eval.hook --update-baselines

eval-compare-scoring:  ## Compare single-shot vs multi-trial scoring side-by-side
	cd skill-eval && uv run python -m skill_eval.hook --compare-scoring

eval-with-context:  ## A/B compare skill-only vs skill+CLAUDE.md context
	cd skill-eval && uv run python -m skill_eval.hook --with-context
