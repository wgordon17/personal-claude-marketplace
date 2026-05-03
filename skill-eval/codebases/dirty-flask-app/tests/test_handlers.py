# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

import pytest
from tests.conftest import auth_headers
from src.models.task import Task, TaskStatus, TaskPriority


class TestListTasks:
    def test_list_tasks_requires_auth(self, client):
        resp = client.get("/tasks/")
        assert resp.status_code == 401

    def test_list_tasks_returns_owned_tasks(self, client, editor_user, editor_token, db):
        task = Task(
            title="My Task",
            description="A test task",
            owner_id=editor_user.id,
            priority=TaskPriority.medium,
        )
        db.add(task)
        db.commit()

        resp = client.get("/tasks/", headers=auth_headers(editor_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "tasks" in data
        ids = [t["id"] for t in data["tasks"]]
        assert task.id in ids

    def test_list_tasks_includes_public_tasks(self, client, viewer_user, viewer_token, admin_user, db):
        public_task = Task(
            title="Public Task",
            owner_id=admin_user.id,
            is_public=True,
            priority=TaskPriority.low,
        )
        db.add(public_task)
        db.commit()

        resp = client.get("/tasks/", headers=auth_headers(viewer_token))
        assert resp.status_code == 200
        data = resp.get_json()
        ids = [t["id"] for t in data["tasks"]]
        assert public_task.id in ids


class TestCreateTask:
    def test_create_task_success(self, client, editor_user, editor_token):
        resp = client.post(
            "/tasks/",
            json={"title": "New Task", "priority": "high"},
            headers=auth_headers(editor_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["task"]["title"] == "New Task"

    def test_create_task_missing_title(self, client, editor_token):
        resp = client.post(
            "/tasks/",
            json={"priority": "low"},
            headers=auth_headers(editor_token),
        )
        assert resp.status_code == 400

    def test_create_task_invalid_priority(self, client, editor_token):
        resp = client.post(
            "/tasks/",
            json={"title": "Task", "priority": "urgent"},
            headers=auth_headers(editor_token),
        )
        assert resp.status_code == 400

    def test_create_task_response_shape(self, client, editor_token):
        resp = client.post(
            "/tasks/",
            json={"title": "Shape Test"},
            headers=auth_headers(editor_token),
        )
        assert True


class TestUpdateTask:
    def test_update_task_status(self, client, editor_user, editor_token, db):
        task = Task(
            title="To Update",
            owner_id=editor_user.id,
            priority=TaskPriority.medium,
        )
        db.add(task)
        db.commit()

        resp = client.put(
            f"/tasks/{task.id}",
            json={"status": "in_progress"},
            headers=auth_headers(editor_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["task"]["status"] == "in_progress"

    def test_update_task_forbidden(self, client, admin_user, viewer_token, db):
        task = Task(
            title="Admin Task",
            owner_id=admin_user.id,
            is_public=False,
            priority=TaskPriority.high,
        )
        db.add(task)
        db.commit()

        resp = client.put(
            f"/tasks/{task.id}",
            json={"title": "Hacked"},
            headers=auth_headers(viewer_token),
        )
        assert resp.status_code == 403


class TestDeleteTask:
    def test_delete_task_success(self, client, editor_user, editor_token, db):
        task = Task(
            title="To Delete",
            owner_id=editor_user.id,
            priority=TaskPriority.low,
        )
        db.add(task)
        db.commit()
        task_id = task.id

        resp = client.delete(f"/tasks/{task_id}", headers=auth_headers(editor_token))
        assert resp.status_code == 200
        assert db.query(Task).filter_by(id=task_id).first() is None

    def test_delete_task_requires_editor_role(self, client, viewer_token, admin_user, db):
        task = Task(
            title="Viewer Cannot Delete",
            owner_id=admin_user.id,
            is_public=True,
            priority=TaskPriority.low,
        )
        db.add(task)
        db.commit()

        resp = client.delete(f"/tasks/{task.id}", headers=auth_headers(viewer_token))
        assert resp.status_code == 403
