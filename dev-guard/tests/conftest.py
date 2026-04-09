"""Shared test fixtures for dev-guard tests."""

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_guard_db(tmp_path):
    """Redirect GUARD_DB_PATH so tests never write to the production database."""
    db_path = str(tmp_path / "test-guard.db")
    old = os.environ.get("GUARD_DB_PATH")
    os.environ["GUARD_DB_PATH"] = db_path
    yield
    if old is None:
        os.environ.pop("GUARD_DB_PATH", None)
    else:
        os.environ["GUARD_DB_PATH"] = old
