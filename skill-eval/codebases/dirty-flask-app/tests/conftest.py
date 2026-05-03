# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

import pytest
from src.app import create_app
from src.db import get_session, drop_tables, create_tables
from src.models.user import User, UserRole
from src.auth.tokens import create_token
import bcrypt


@pytest.fixture(scope="session")
def app():
    test_config = {
        "TESTING": True,
        "DATABASE_URL": "postgresql://localhost/taskdb_test",
        "JWT_SECRET": "test-jwt-secret",
        "SECRET_KEY": "test-secret-key",
    }
    application = create_app(test_config)
    with application.app_context():
        create_tables()
        yield application
        drop_tables()


@pytest.fixture(scope="function")
def client(app):
    return app.test_client()


@pytest.fixture(scope="function")
def db(app):
    with app.app_context():
        session = get_session()
        yield session
        session.rollback()


def make_password_hash(password):
    salt = bcrypt.gensalt(rounds=4)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


@pytest.fixture
def admin_user(db):
    user = User(
        username="admin_test",
        email="admin@example.com",
        password_hash=make_password_hash("AdminPass1"),
        role=UserRole.admin,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def editor_user(db):
    user = User(
        username="editor_test",
        email="editor@example.com",
        password_hash=make_password_hash("EditorPass1"),
        role=UserRole.editor,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def viewer_user(db):
    user = User(
        username="viewer_test",
        email="viewer@example.com",
        password_hash=make_password_hash("ViewerPass1"),
        role=UserRole.viewer,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def admin_token(app, admin_user):
    with app.app_context():
        return create_token(admin_user.id, admin_user.role.value)


@pytest.fixture
def editor_token(app, editor_user):
    with app.app_context():
        return create_token(editor_user.id, editor_user.role.value)


@pytest.fixture
def viewer_token(app, viewer_user):
    with app.app_context():
        return create_token(viewer_user.id, viewer_user.role.value)


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}
