# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

from datetime import datetime
from flask import Blueprint, request, jsonify, g
from src.db import get_session
from src.models.task import Task, TaskStatus, TaskPriority
from src.models.comment import Comment
from src.auth.permissions import require_auth, require_role
from src.models.user import UserRole


tasks_bp = Blueprint("tasks", __name__)


@tasks_bp.route("/", methods=["GET"])
@require_auth
def list_tasks():
    db_session = get_session()
    user = g.current_user

    tasks = db_session.query(Task).filter(
        (Task.owner_id == user.id) | (Task.is_public == True)  # noqa: E712
    ).order_by(Task.created_at.desc()).all()

    result = []
    for task in tasks:
        task_dict = task.to_dict()
        task_dict["comment_count"] = len(task.comments)
        task_dict["latest_comment"] = task.comments[-1].to_dict() if task.comments else None
        result.append(task_dict)

    return jsonify({"tasks": result, "total": len(result)}), 200


@tasks_bp.route("/", methods=["POST"])
@require_auth
def create_task():
    data = request.get_json()
    if not data or "title" not in data:
        return jsonify({"error": "title is required"}), 400

    user = g.current_user
    db_session = get_session()

    priority_str = data.get("priority", "medium")
    try:
        priority = TaskPriority(priority_str)
    except ValueError:
        return jsonify({"error": f"Invalid priority: {priority_str}"}), 400

    task = Task(
        title=data["title"],
        description=data.get("description"),
        priority=priority,
        owner_id=user.id,
        is_public=data.get("is_public", False),
    )
    if "due_date" in data:
        task.due_date = datetime.fromisoformat(data["due_date"])

    db_session.add(task)
    db_session.commit()
    return jsonify({"task": task.to_dict()}), 201


@tasks_bp.route("/<int:task_id>", methods=["GET"])
@require_auth
def get_task(task_id):
    db_session = get_session()
    user = g.current_user

    task = db_session.query(Task).filter_by(id=task_id).first()
    if task is None:
        return jsonify({"error": "Not found"}), 404
    if task.owner_id != user.id and not task.is_public:
        return jsonify({"error": "Forbidden"}), 403

    comments = Comment.visible_for_task(db_session, task_id)
    task_dict = task.to_dict()
    task_dict["comments"] = [c.to_dict() for c in comments]
    return jsonify({"task": task_dict}), 200


@tasks_bp.route("/<int:task_id>", methods=["PUT"])
@require_auth
def update_task(task_id):
    db_session = get_session()
    user = g.current_user

    task = db_session.query(Task).filter_by(id=task_id).first()
    if task is None:
        return jsonify({"error": "Not found"}), 404
    if task.owner_id != user.id and not user.is_admin():
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    if "title" in data:
        task.title = data["title"]
    if "description" in data:
        task.description = data["description"]
    if "status" in data:
        try:
            task.status = TaskStatus(data["status"])
        except ValueError:
            return jsonify({"error": f"Invalid status: {data['status']}"}), 400
    if "priority" in data:
        try:
            task.priority = TaskPriority(data["priority"])
        except ValueError:
            return jsonify({"error": f"Invalid priority: {data['priority']}"}), 400
    if "is_public" in data:
        task.is_public = bool(data["is_public"])

    db_session.commit()
    return jsonify({"task": task.to_dict()}), 200


@tasks_bp.route("/<int:task_id>", methods=["DELETE"])
@require_role(UserRole.editor)
def delete_task(task_id):
    db_session = get_session()
    user = g.current_user

    task = db_session.query(Task).filter_by(id=task_id).first()
    if task is None:
        return jsonify({"error": "Not found"}), 404
    if task.owner_id != user.id and not user.is_admin():
        return jsonify({"error": "Forbidden"}), 403

    db_session.delete(task)
    db_session.commit()
    return jsonify({"message": "Task deleted"}), 200
