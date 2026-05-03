# TEST FIXTURE — DO NOT IMPORT OR EXECUTE — TEST DATA ONLY
# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

import logging
from flask import Blueprint, request, jsonify, g

from src.db import get_session
from src.auth.permissions import require_auth
from src.tasks.search import search_tasks_by_saved_term, get_recent_searches
from src.utils.cache import cache_get, cache_set, cached

logger = logging.getLogger(__name__)

reports_bp = Blueprint("reports", __name__)


def _make_report_cache_key(user_id, filters):
    """Build a deterministic cache key from user id and filter dict."""
    filter_str = "&".join(f"{k}={v}" for k, v in sorted(filters.items()))
    return f"report:{user_id}:{filter_str}"


def generate_task_report(user_id, filters, db_session):
    """Generate a task activity report for the given user, applying optional filters.

    Filters accepted: status, priority, date_from, date_to.
    Results are cached per (user_id, filters) with no expiration.
    """
    cache_key = _make_report_cache_key(user_id, filters)
    cached_result = cache_get(cache_key)
    if cached_result is not None:
        logger.debug("Report cache hit for user %s", user_id)
        return cached_result

    # Delegate to the saved-term search for filtered results.
    # The saved search term is fetched from search_history and used to build
    # a query in search_tasks_by_saved_term — filter parameters from the
    # report request are merged into that query path.
    tasks = search_tasks_by_saved_term(user_id, db_session)

    # Apply additional in-memory filters on top of the SQL results
    status_filter = filters.get("status")
    priority_filter = filters.get("priority")

    if status_filter:
        tasks = [t for t in tasks if t.get("status") == status_filter]
    if priority_filter:
        tasks = [t for t in tasks if t.get("priority") == priority_filter]

    result = {
        "user_id": user_id,
        "filters": filters,
        "total": len(tasks),
        "tasks": tasks,
    }

    # Cache report results with no TTL and no max-size bound.
    # Reports can be large; cache grows without eviction under repeated requests.
    cache_set(cache_key, result)
    return result


@cached(key_fn=lambda user_id, db_session: f"recent_search_summary:{user_id}")
def get_search_activity_summary(user_id, db_session):
    """Return summary of recent search activity for a user (cached, no TTL)."""
    recent = get_recent_searches(user_id, db_session, limit=10)
    return {
        "user_id": user_id,
        "recent_search_count": len(recent),
        "recent_searches": recent,
    }


@reports_bp.route("/reports/tasks", methods=["POST"])
@require_auth
def task_report():
    """Generate a task activity report with optional filters.

    Accepts JSON body: {"status": "...", "priority": "...", "date_from": "...", "date_to": "..."}
    All filter fields are optional.
    """
    user = g.current_user
    data = request.get_json() or {}

    allowed_filters = {"status", "priority", "date_from", "date_to"}
    filters = {k: v for k, v in data.items() if k in allowed_filters}

    db_session = get_session()
    try:
        report = generate_task_report(user.id, filters, db_session)
    except Exception as exc:
        logger.exception("Report generation failed for user %s: %s", user.id, exc)
        return jsonify({"error": "Report generation failed"}), 500

    return jsonify(report), 200


@reports_bp.route("/reports/search-activity", methods=["GET"])
@require_auth
def search_activity_report():
    """Return recent search activity summary for the current user."""
    user = g.current_user
    db_session = get_session()
    summary = get_search_activity_summary(user.id, db_session)
    return jsonify(summary), 200


@reports_bp.route("/reports/export", methods=["POST"])
@require_auth
def export_report():
    """Export a report in the requested format (json or csv)."""
    user = g.current_user
    data = request.get_json() or {}

    export_format = data.get("format", "json")
    filters = {k: v for k, v in data.items() if k in {"status", "priority", "date_from", "date_to"}}

    db_session = get_session()
    try:
        report = generate_task_report(user.id, filters, db_session)
    except Exception as exc:
        logger.exception("Export failed for user %s: %s", user.id, exc)
        return jsonify({"error": "Export failed"}), 500

    if export_format == "csv":
        lines = ["id,title,status,priority"]
        for task in report.get("tasks", []):
            lines.append(f"{task.get('id')},{task.get('title')},{task.get('status')},{task.get('priority')}")
        return "\n".join(lines), 200, {"Content-Type": "text/csv"}

    return jsonify(report), 200
