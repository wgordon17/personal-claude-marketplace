"""Shared test fixtures for dev-guard tests."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_guard_db(tmp_path, monkeypatch):
    """Redirect GUARD_DB_PATH for tests that inherit os.environ."""
    monkeypatch.setenv("GUARD_DB_PATH", str(tmp_path / "test.db"))
