from flask import Blueprint, g, jsonify, request
from src.db import get_db
from src.middleware.auth import require_session
from src.models.project import (
    Project,
    add_member,
    create_project,
    delete_project,
    get_project_by_id,
    list_projects_for_user,
)
from src.utils.validation import validate_description, validate_project_name

projects_bp = Blueprint("projects", __name__)


def _assert_project_access(
    project: Project | None, user_id: int, owner_only: bool = False
) -> tuple[Project | None, tuple | None]:
    if project is None:
        return None, (jsonify({"error": "Project not found"}), 404)
    if owner_only and project.owner_id != user_id:
        return None, (jsonify({"error": "Forbidden"}), 403)
    if not owner_only and user_id not in project.member_ids and project.owner_id != user_id:
        return None, (jsonify({"error": "Forbidden"}), 403)
    return project, None


@projects_bp.route("/", methods=["GET"])
@require_session
def list_projects():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 20, type=int)))
    with get_db() as db:
        projects = list_projects_for_user(db, g.user_id, page=page, per_page=per_page)
    return jsonify(
        [
            {"id": p.id, "name": p.name, "description": p.description, "owner_id": p.owner_id}
            for p in projects
        ]
    ), 200


@projects_bp.route("/<int:project_id>", methods=["GET"])
@require_session
def get_project(project_id: int):
    with get_db() as db:
        project = get_project_by_id(db, project_id)
        project, err = _assert_project_access(project, g.user_id)
        if err:
            return err
    return jsonify(
        {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "owner_id": project.owner_id,
            "member_ids": project.member_ids,
        }
    ), 200


@projects_bp.route("/", methods=["POST"])
@require_session
def create_project_endpoint():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    name = data.get("name", "")
    description = data.get("description", "")

    if not validate_project_name(name):
        return jsonify({"error": "Name must be 1-100 characters"}), 422
    if not validate_description(description):
        return jsonify({"error": "Description exceeds 1000 characters"}), 422

    with get_db() as db:
        project = create_project(db, name, description, g.user_id)

    return jsonify({"id": project.id, "name": project.name}), 201


@projects_bp.route("/<int:project_id>/members", methods=["POST"])
@require_session
def add_project_member(project_id: int):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    new_member_id = data.get("user_id")
    if not isinstance(new_member_id, int) or new_member_id < 1:
        return jsonify({"error": "Invalid user_id"}), 422

    with get_db() as db:
        project = get_project_by_id(db, project_id)
        project, err = _assert_project_access(project, g.user_id, owner_only=True)
        if err:
            return err
        add_member(db, project_id, new_member_id)

    return jsonify({"message": "Member added"}), 200


@projects_bp.route("/<int:project_id>", methods=["DELETE"])
@require_session
def delete_project_endpoint(project_id: int):
    with get_db() as db:
        project = get_project_by_id(db, project_id)
        project, err = _assert_project_access(project, g.user_id, owner_only=True)
        if err:
            return err
        delete_project(db, project_id)

    return jsonify({"message": "Project deleted"}), 200
