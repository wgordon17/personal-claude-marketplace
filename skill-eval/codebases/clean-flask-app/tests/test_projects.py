from unittest.mock import MagicMock, patch

from tests.conftest import make_project


def _patch_db():
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=MagicMock())
    m.__exit__ = MagicMock(return_value=False)
    return m


class TestListProjects:
    def test_list_requires_auth(self, client):
        resp = client.get("/projects/")
        assert resp.status_code == 401

    def test_list_returns_projects(self, authenticated_client):
        projects = [make_project(id=1), make_project(id=2, name="Other")]
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.list_projects_for_user", return_value=projects),
        ):
            resp = authenticated_client.get("/projects/")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        assert data[0]["id"] == 1

    def test_list_pagination_clamped(self, authenticated_client):
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.list_projects_for_user", return_value=[]) as mock_list,
        ):
            resp = authenticated_client.get("/projects/?page=0&per_page=999")
        assert resp.status_code == 200
        call_kwargs = mock_list.call_args
        assert call_kwargs[1]["page"] >= 1
        assert call_kwargs[1]["per_page"] <= 100


class TestGetProject:
    def test_get_own_project(self, authenticated_client):
        project = make_project(owner_id=1)
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.get_project_by_id", return_value=project),
        ):
            resp = authenticated_client.get("/projects/1")
        assert resp.status_code == 200
        assert resp.get_json()["id"] == 1

    def test_get_member_project(self, authenticated_client):
        project = make_project(owner_id=99, member_ids=[1, 99])
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.get_project_by_id", return_value=project),
        ):
            resp = authenticated_client.get("/projects/1")
        assert resp.status_code == 200

    def test_get_project_forbidden(self, authenticated_client):
        project = make_project(owner_id=99, member_ids=[99])
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.get_project_by_id", return_value=project),
        ):
            resp = authenticated_client.get("/projects/1")
        assert resp.status_code == 403

    def test_get_project_not_found(self, authenticated_client):
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.get_project_by_id", return_value=None),
        ):
            resp = authenticated_client.get("/projects/999")
        assert resp.status_code == 404


class TestCreateProject:
    def test_create_success(self, authenticated_client):
        project = make_project(id=5, name="New Project")
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.create_project", return_value=project),
        ):
            resp = authenticated_client.post(
                "/projects/",
                json={
                    "name": "New Project",
                    "description": "A test project",
                },
            )
        assert resp.status_code == 201
        assert resp.get_json()["name"] == "New Project"

    def test_create_empty_name(self, authenticated_client):
        resp = authenticated_client.post(
            "/projects/",
            json={
                "name": "",
                "description": "desc",
            },
        )
        assert resp.status_code == 422

    def test_create_name_too_long(self, authenticated_client):
        resp = authenticated_client.post(
            "/projects/",
            json={
                "name": "x" * 101,
                "description": "desc",
            },
        )
        assert resp.status_code == 422

    def test_create_description_too_long(self, authenticated_client):
        resp = authenticated_client.post(
            "/projects/",
            json={
                "name": "Valid Name",
                "description": "x" * 1001,
            },
        )
        assert resp.status_code == 422

    def test_create_no_json(self, authenticated_client):
        resp = authenticated_client.post("/projects/", data="not json", content_type="text/plain")
        assert resp.status_code == 400


class TestAddMember:
    def test_owner_can_add_member(self, authenticated_client):
        project = make_project(owner_id=1)
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.get_project_by_id", return_value=project),
            patch("src.api.projects.add_member", return_value=True),
        ):
            resp = authenticated_client.post("/projects/1/members", json={"user_id": 42})
        assert resp.status_code == 200

    def test_non_owner_cannot_add_member(self, authenticated_client):
        project = make_project(owner_id=99, member_ids=[1, 99])
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.get_project_by_id", return_value=project),
        ):
            resp = authenticated_client.post("/projects/1/members", json={"user_id": 42})
        assert resp.status_code == 403

    def test_invalid_user_id(self, authenticated_client):
        project = make_project(owner_id=1)
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.get_project_by_id", return_value=project),
        ):
            resp = authenticated_client.post("/projects/1/members", json={"user_id": "notanint"})
        assert resp.status_code == 422


class TestDeleteProject:
    def test_owner_can_delete(self, authenticated_client):
        project = make_project(owner_id=1)
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.get_project_by_id", return_value=project),
            patch("src.api.projects.delete_project", return_value=True),
        ):
            resp = authenticated_client.delete("/projects/1")
        assert resp.status_code == 200

    def test_non_owner_cannot_delete(self, authenticated_client):
        project = make_project(owner_id=99, member_ids=[1, 99])
        with (
            patch("src.api.projects.get_db", return_value=_patch_db()),
            patch("src.api.projects.get_project_by_id", return_value=project),
        ):
            resp = authenticated_client.delete("/projects/1")
        assert resp.status_code == 403
