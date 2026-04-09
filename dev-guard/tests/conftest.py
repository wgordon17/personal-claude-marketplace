"""Shared test fixtures for dev-guard tests."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_guard_db(tmp_path, monkeypatch):
    """Redirect GUARD_DB_PATH so tests never write to the production database."""
    monkeypatch.setenv("GUARD_DB_PATH", str(tmp_path / "test-guard.db"))
