from unittest.mock import MagicMock, patch

from tests.conftest import make_user


class TestRegister:
    def test_register_success(self, client):
        mock_user = make_user()
        with (
            patch("src.api.auth.get_db") as mock_get_db,
            patch("src.api.auth.get_user_by_email", return_value=None),
            patch("src.api.auth.create_user", return_value=mock_user),
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
            resp = client.post(
                "/auth/register",
                json={
                    "username": "newuser",
                    "email": "new@example.com",
                    "password": "securepassword123",
                },
            )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["username"] == "testuser"

    def test_register_duplicate_email(self, client):
        existing = make_user()
        with (
            patch("src.api.auth.get_db") as mock_get_db,
            patch("src.api.auth.get_user_by_email", return_value=existing),
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
            resp = client.post(
                "/auth/register",
                json={
                    "username": "newuser",
                    "email": "test@example.com",
                    "password": "securepassword123",
                },
            )
        assert resp.status_code == 409

    def test_register_invalid_email(self, client):
        resp = client.post(
            "/auth/register",
            json={
                "username": "validuser",
                "email": "not-an-email",
                "password": "securepassword123",
            },
        )
        assert resp.status_code == 422
        assert "email" in resp.get_json()["errors"]

    def test_register_short_password(self, client):
        resp = client.post(
            "/auth/register",
            json={
                "username": "validuser",
                "email": "valid@example.com",
                "password": "short",
            },
        )
        assert resp.status_code == 422
        assert "password" in resp.get_json()["errors"]

    def test_register_invalid_username(self, client):
        resp = client.post(
            "/auth/register",
            json={
                "username": "a",
                "email": "valid@example.com",
                "password": "securepassword123",
            },
        )
        assert resp.status_code == 422
        assert "username" in resp.get_json()["errors"]

    def test_register_no_json(self, client):
        resp = client.post("/auth/register", data="not json", content_type="text/plain")
        assert resp.status_code == 400


class TestLogin:
    def test_login_success_clears_session(self, client):
        user = make_user()
        with client.session_transaction() as sess:
            sess["stale_key"] = "old_value"

        with (
            patch("src.api.auth.get_db") as mock_get_db,
            patch("src.api.auth.get_user_by_email", return_value=user),
            patch("src.api.auth.verify_password", return_value=True),
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
            resp = client.post(
                "/auth/login",
                json={
                    "email": "test@example.com",
                    "password": "password123",
                },
            )

        assert resp.status_code == 200
        with client.session_transaction() as sess:
            # Session fixation prevention: stale_key must be gone
            assert "stale_key" not in sess
            assert sess["user_id"] == 1

    def test_login_wrong_password(self, client):
        user = make_user()
        with (
            patch("src.api.auth.get_db") as mock_get_db,
            patch("src.api.auth.get_user_by_email", return_value=user),
            patch("src.api.auth.verify_password", return_value=False),
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
            resp = client.post(
                "/auth/login",
                json={
                    "email": "test@example.com",
                    "password": "wrong",
                },
            )
        assert resp.status_code == 401

    def test_login_inactive_user(self, client):
        user = make_user(is_active=False)
        with (
            patch("src.api.auth.get_db") as mock_get_db,
            patch("src.api.auth.get_user_by_email", return_value=user),
            patch("src.api.auth.verify_password", return_value=True),
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
            resp = client.post(
                "/auth/login",
                json={
                    "email": "test@example.com",
                    "password": "password123",
                },
            )
        assert resp.status_code == 401

    def test_login_unknown_user(self, client):
        with (
            patch("src.api.auth.get_db") as mock_get_db,
            patch("src.api.auth.get_user_by_email", return_value=None),
        ):
            mock_get_db.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)
            resp = client.post(
                "/auth/login",
                json={
                    "email": "nobody@example.com",
                    "password": "password123",
                },
            )
        assert resp.status_code == 401

    def test_login_missing_fields(self, client):
        resp = client.post("/auth/login", json={"email": "test@example.com"})
        assert resp.status_code == 400


class TestLogout:
    def test_logout_clears_session(self, authenticated_client):
        with authenticated_client.session_transaction() as sess:
            assert "user_id" in sess
        resp = authenticated_client.post("/auth/logout")
        assert resp.status_code == 200
        with authenticated_client.session_transaction() as sess:
            assert "user_id" not in sess


class TestMe:
    def test_me_authenticated(self, authenticated_client):
        resp = authenticated_client.get("/auth/me")
        assert resp.status_code == 200
        assert resp.get_json()["id"] == 1

    def test_me_unauthenticated(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code == 401
