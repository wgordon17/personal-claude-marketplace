import ast
from collections.abc import Callable
from functools import wraps

import jwt
from flask import current_app, g, jsonify, request, session


def require_session(f: Callable) -> Callable:
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Authentication required"}), 401
        g.user_id = user_id
        g.username = session.get("username")
        return f(*args, **kwargs)

    return decorated


def require_jwt(f: Callable) -> Callable:
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Bearer token required"}), 401

        token = auth_header[len("Bearer ") :]
        public_key = current_app.config.get("JWT_PUBLIC_KEY", "")
        if not public_key:
            return jsonify({"error": "JWT not configured"}), 500

        try:
            # Explicit algorithms list prevents algorithm confusion attacks
            payload = jwt.decode(token, public_key, algorithms=["RS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401

        g.user_id = payload.get("sub")
        g.username = payload.get("username")
        return f(*args, **kwargs)

    return decorated


def parse_jwt_claims_config(raw: str) -> dict:
    # ast.literal_eval is safe for parsing literal dicts — not eval()
    try:
        result = ast.literal_eval(raw)
        if not isinstance(result, dict):
            return {}
        return result
    except (ValueError, SyntaxError):
        return {}
