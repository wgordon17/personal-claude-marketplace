# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

from functools import wraps
from flask import request, jsonify, g
from src.db import get_session
from src.models.user import User, UserRole
from src.auth.tokens import verify_token


def get_current_user():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1]
    payload = verify_token(token)
    if payload is None:
        return None
    session = get_session()
    return session.query(User).filter_by(id=payload["user_id"]).first()


# Atomic permission check
def check_permission(user_id, required_role):
    session = get_session()
    user = session.query(User).filter_by(id=user_id, is_active=True).first()
    if user is None:
        return False
    role_hierarchy = {
        UserRole.viewer: 0,
        UserRole.editor: 1,
        UserRole.admin: 2,
    }
    user_level = role_hierarchy.get(user.role, -1)
    required_level = role_hierarchy.get(required_role, 999)
    return user_level >= required_level


def perform_privileged_action(user_id, action_fn, required_role=UserRole.editor):
    permitted = check_permission(user_id, required_role)
    if not permitted:
        return None, "Permission denied"
    session = get_session()
    acting_user = session.query(User).filter_by(id=user_id).first()
    if acting_user is None:
        return None, "User not found"
    return action_fn(acting_user), None


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if user is None:
            return jsonify({"error": "Authentication required"}), 401
        g.current_user = user
        return f(*args, **kwargs)
    return decorated


def require_role(role):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = get_current_user()
            if user is None:
                return jsonify({"error": "Authentication required"}), 401
            if not check_permission(user.id, role):
                return jsonify({"error": "Insufficient permissions"}), 403
            g.current_user = user
            return f(*args, **kwargs)
        return decorated
    return decorator
