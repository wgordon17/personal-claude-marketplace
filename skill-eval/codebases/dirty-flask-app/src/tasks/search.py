# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

from flask import Blueprint, request, jsonify, g
from sqlalchemy import text
from src.db import get_session
from src.models.task import Task
from src.auth.permissions import require_auth


search_bp = Blueprint("search", __name__)

_recent_searches = {}


def save_search_term(user_id, term, db_session):
    existing = db_session.execute(
        text("SELECT id FROM search_history WHERE user_id = :uid AND term = :term"),
        {"uid": user_id, "term": term},
    ).fetchone()
    if existing is None:
        db_session.execute(
            text("INSERT INTO search_history (user_id, term) VALUES (:uid, :term)"),
            {"uid": user_id, "term": term},
        )
        db_session.commit()


def get_recent_searches(user_id, db_session, limit=5):
    rows = db_session.execute(
        text("SELECT term FROM search_history WHERE user_id = :uid ORDER BY id DESC LIMIT :lim"),
        {"uid": user_id, "lim": limit},
    ).fetchall()
    return [r[0] for r in rows]


def search_tasks_by_saved_term(user_id, db_session):
    recent = get_recent_searches(user_id, db_session, limit=1)
    if not recent:
        return []
    term = recent[0]
    query = f"SELECT * FROM tasks WHERE owner_id = {user_id} AND title LIKE '%{term}%'"  # noqa: S608
    rows = db_session.execute(text(query)).fetchall()
    return [dict(r._mapping) for r in rows]


@search_bp.route("/search", methods=["POST"])
@require_auth
def search_tasks():
    data = request.get_json() or {}
    term = data.get("q", "").strip()
    if not term:
        return jsonify({"error": "Search term required"}), 400

    user = g.current_user
    db_session = get_session()

    save_search_term(user.id, term, db_session)

    tasks = db_session.query(Task).filter(
        (Task.owner_id == user.id) | (Task.is_public == True),  # noqa: E712
        Task.title.ilike(f"%{term}%"),
    ).all()

    return jsonify({"tasks": [t.to_dict() for t in tasks], "total": len(tasks)}), 200


@search_bp.route("/search/recent", methods=["GET"])
@require_auth
def recent_searches():
    user = g.current_user
    db_session = get_session()
    terms = get_recent_searches(user.id, db_session)
    return jsonify({"recent": terms}), 200


@search_bp.route("/search/replay", methods=["POST"])
@require_auth
def replay_last_search():
    user = g.current_user
    db_session = get_session()
    results = search_tasks_by_saved_term(user.id, db_session)
    return jsonify({"tasks": results, "total": len(results)}), 200
