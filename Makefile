.PHONY: all lint format test test-llm test-live test-ts typecheck prek prek-install eval eval-changed eval-update-baselines eval-coverage-check

all: lint test typecheck  ## Full check suite (lint + test + typecheck)

lint:  ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format:  ## Auto-format with ruff
	uv run ruff format .
	uv run ruff check --fix .

test:  ## Run pytest (excludes LLM integration and live/network tests)
	uv run pytest -m "not llm and not eval and not slow"

test-llm:  ## Run LLM integration tests (requires Vertex AI credentials)
	uv run --group dev --group llm pytest -m llm -v

test-live:  ## Run live/network-dependent smoke tests (requires network access)
	RUN_LIVE_TESTS=1 uv run pytest -m slow -v

test-ts:  ## Run bun test for the OMP extension bridges (requires bun; not part of `make all`)
	bun test dev-guard/tests git-tools/tests github-mcp/tests

typecheck:  ## Pyright type checking (dev-guard/hooks)
	uv run pyright

prek:  ## Run pre-commit on all files
	uvx prek run --all-files

prek-install:  ## Install pre-commit + pre-push hooks
	uvx prek install --install-hooks --hook-type pre-commit --hook-type pre-push

eval-changed:  ## On-demand: eval only changed skills (run before opening/merging a PR)
	cd skill-eval && uv run python -m skill_eval.cli

eval:  ## Run skill evals for all skills with test cases
	cd skill-eval && uv run python -m skill_eval.cli --all

eval-update-baselines:  ## Update baselines.json with current scores
	cd skill-eval && uv run python -m skill_eval.cli --update-baselines

eval-coverage-check:  ## CI: flag changed skills whose test_cases file wasn't updated (no AI creds needed)
	cd skill-eval && uv run python -m skill_eval.cli --coverage-check
