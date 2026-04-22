# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

import csv
import io
import json
import requests
from flask import Blueprint, request, jsonify, g
from src.db import get_session
from src.models.task import Task
from src.auth.permissions import require_auth


export_bp = Blueprint("export", __name__)


def validate_webhook_url(url):
    if not url.startswith("https://"):
        return False, "Webhook URL must use HTTPS"
    return True, None


def tasks_to_csv(tasks):
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["id", "title", "description", "status", "priority", "owner_id", "created_at"],
    )
    writer.writeheader()
    for task in tasks:
        row = task.to_dict()
        writer.writerow({k: row.get(k, "") for k in writer.fieldnames})
    return output.getvalue()


def tasks_to_json(tasks):
    return json.dumps([t.to_dict() for t in tasks], indent=2)


@export_bp.route("/export", methods=["POST"])
@require_auth
def export_tasks():
    data = request.get_json() or {}
    fmt = data.get("format", "json")
    webhook_url = data.get("webhook_url")

    db_session = get_session()
    user = g.current_user

    tasks = db_session.query(Task).filter(
        (Task.owner_id == user.id) | (Task.is_public == True)  # noqa: E712
    ).all()

    if fmt == "csv":
        payload = tasks_to_csv(tasks)
        content_type = "text/csv"
    else:
        payload = tasks_to_json(tasks)
        content_type = "application/json"

    if webhook_url:
        valid, err = validate_webhook_url(webhook_url)
        if not valid:
            return jsonify({"error": err}), 400

        response = requests.get(webhook_url, timeout=10)
        if response.status_code != 200:
            return jsonify({"error": "Webhook delivery failed", "status": response.status_code}), 502

        return jsonify({"message": "Export delivered to webhook", "tasks_count": len(tasks)}), 200

    return payload, 200, {"Content-Type": content_type}


@export_bp.route("/export/status", methods=["GET"])
@require_auth
def export_status():
    return jsonify({"status": "ready", "formats": ["json", "csv"]}), 200
