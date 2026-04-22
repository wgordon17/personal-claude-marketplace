from flask import Blueprint, g, jsonify, request
from src.db import get_db
from src.middleware.auth import require_session
from src.models.project import get_project_by_id
from src.models.ticket import (
    TicketPriority,
    TicketStatus,
    create_ticket,
    get_ticket_by_id,
    list_tickets_for_project,
    update_ticket_status,
)
from src.utils.validation import validate_description, validate_ticket_title

tickets_bp = Blueprint("tickets", __name__)


def _assert_project_membership(db, project_id: int, user_id: int):
    project = get_project_by_id(db, project_id)
    if project is None:
        return None, (jsonify({"error": "Project not found"}), 404)
    if user_id not in project.member_ids and project.owner_id != user_id:
        return None, (jsonify({"error": "Forbidden"}), 403)
    return project, None


@tickets_bp.route("/project/<int:project_id>", methods=["GET"])
@require_session
def list_tickets(project_id: int):
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 50, type=int)))
    with get_db() as db:
        _, err = _assert_project_membership(db, project_id, g.user_id)
        if err:
            return err
        tickets = list_tickets_for_project(db, project_id, page=page, per_page=per_page)
    return jsonify(
        [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "assignee_id": t.assignee_id,
            }
            for t in tickets
        ]
    ), 200


@tickets_bp.route("/<int:ticket_id>", methods=["GET"])
@require_session
def get_ticket(ticket_id: int):
    with get_db() as db:
        ticket = get_ticket_by_id(db, ticket_id)
        if ticket is None:
            return jsonify({"error": "Ticket not found"}), 404
        _, err = _assert_project_membership(db, ticket.project_id, g.user_id)
        if err:
            return err
    return jsonify(
        {
            "id": ticket.id,
            "project_id": ticket.project_id,
            "title": ticket.title,
            "description": ticket.description,
            "status": ticket.status,
            "priority": ticket.priority,
            "assignee_id": ticket.assignee_id,
            "reporter_id": ticket.reporter_id,
        }
    ), 200


@tickets_bp.route("/project/<int:project_id>", methods=["POST"])
@require_session
def create_ticket_endpoint(project_id: int):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    title = data.get("title", "")
    description = data.get("description", "")
    priority_raw = data.get("priority", "medium")
    assignee_id = data.get("assignee_id")

    if not validate_ticket_title(title):
        return jsonify({"error": "Title must be 1-200 characters"}), 422
    if not validate_description(description):
        return jsonify({"error": "Description exceeds 5000 characters"}), 422
    try:
        priority = TicketPriority(priority_raw)
    except ValueError:
        return jsonify(
            {"error": f"Invalid priority. Allowed: {[p.value for p in TicketPriority]}"}
        ), 422
    if assignee_id is not None and not isinstance(assignee_id, int):
        return jsonify({"error": "assignee_id must be an integer"}), 422

    with get_db() as db:
        _, err = _assert_project_membership(db, project_id, g.user_id)
        if err:
            return err
        ticket = create_ticket(db, project_id, title, description, priority, g.user_id, assignee_id)

    return jsonify({"id": ticket.id, "title": ticket.title, "status": ticket.status}), 201


@tickets_bp.route("/<int:ticket_id>/status", methods=["PATCH"])
@require_session
def update_status(ticket_id: int):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    status_raw = data.get("status", "")
    try:
        status = TicketStatus(status_raw)
    except ValueError:
        return jsonify(
            {"error": f"Invalid status. Allowed: {[s.value for s in TicketStatus]}"}
        ), 422

    with get_db() as db:
        ticket = get_ticket_by_id(db, ticket_id)
        if ticket is None:
            return jsonify({"error": "Ticket not found"}), 404
        _, err = _assert_project_membership(db, ticket.project_id, g.user_id)
        if err:
            return err
        update_ticket_status(db, ticket_id, status)

    return jsonify({"id": ticket_id, "status": status}), 200
