from unittest.mock import MagicMock, patch

import pytest
from src.app import create_app


@pytest.fixture
def mock_db_session():
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


@pytest.fixture
def app(mock_db_session):
    test_config = {
        "TESTING": True,
        "SECRET_KEY": "test-secret-key-not-used-in-prod",
        "DATABASE_URL": "postgresql://localhost/test_projectmgmt",
        "SESSION_COOKIE_SECURE": False,
        "WTF_CSRF_ENABLED": False,
    }
    with (
        patch.dict("os.environ", {"SECRET_KEY": test_config["SECRET_KEY"]}),
        patch("src.db.init_db"),
    ):
        application = create_app(config_overrides=test_config)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def authenticated_client(app, client):
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "testuser"
    return client


def make_user(
    id=1,
    username="testuser",
    email="test@example.com",
    password_hash="$2b$12$fakehash",
    is_active=True,
):
    from src.models.user import User

    return User(
        id=id, username=username, email=email, password_hash=password_hash, is_active=is_active
    )


def make_project(id=1, name="Test Project", description="desc", owner_id=1, member_ids=None):
    from src.models.project import Project

    return Project(
        id=id, name=name, description=description, owner_id=owner_id, member_ids=member_ids or [1]
    )
