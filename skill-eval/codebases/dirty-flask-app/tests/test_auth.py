# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

import pytest
from tests.conftest import auth_headers, make_password_hash
from src.models.user import User, UserRole


class TestLogin:
    def test_login_success(self, client, viewer_user):
        resp = client.post("/auth/login", json={
            "username": "viewer_test",
            "password": "ViewerPass1",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "token" in data
        assert "user" in data

    def test_login_wrong_password(self, client, viewer_user):
        resp = client.post("/auth/login", json={
            "username": "viewer_test",
            "password": "wrongpassword",
        })
        assert resp.status_code == 401

    def test_login_unknown_user(self, client):
        resp = client.post("/auth/login", json={
            "username": "doesnotexist",
            "password": "SomePass1",
        })
        assert resp.status_code == 401

    def test_login_missing_fields(self, client):
        resp = client.post("/auth/login", json={"username": "viewer_test"})
        assert resp.status_code == 400

    def test_login_returns_user_dict(self, client, viewer_user):
        resp = client.post("/auth/login", json={
            "username": "viewer_test",
            "password": "ViewerPass1",
        })
        data = resp.get_json()
        assert data["user"]["username"] == "viewer_test"
        assert "password_hash" not in data["user"]


class TestRegister:
    def test_register_success(self, client):
        resp = client.post("/auth/register", json={
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "NewPass1",
        })
        assert resp.status_code == 201
        assert "user" in resp.get_json()

    def test_register_duplicate_username(self, client, viewer_user):
        resp = client.post("/auth/register", json={
            "username": "viewer_test",
            "email": "other@example.com",
            "password": "OtherPass1",
        })
        assert resp.status_code == 409

    def test_register_missing_fields(self, client):
        resp = client.post("/auth/register", json={"username": "incomplete"})
        assert resp.status_code == 400


class TestLogout:
    def test_logout_success(self, client, viewer_token):
        resp = client.post("/auth/logout", headers=auth_headers(viewer_token))
        assert resp.status_code == 200


class TestInactiveUser:
    def test_inactive_user_cannot_login(self, client, db):
        user = User(
            username="inactive_user",
            email="inactive@example.com",
            password_hash=make_password_hash("InactivePass1"),
            role=UserRole.viewer,
            is_active=False,
        )
        db.add(user)
        db.commit()

        resp = client.post("/auth/login", json={
            "username": "inactive_user",
            "password": "InactivePass1",
        })
        assert resp.status_code == 401
