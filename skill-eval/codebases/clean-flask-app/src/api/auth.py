from flask import Blueprint, jsonify, request, session
from src.db import get_db
from src.models.user import create_user, get_user_by_email, verify_password
from src.utils.validation import validate_email, validate_password, validate_username

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    username = data.get("username", "")
    email = data.get("email", "")
    password = data.get("password", "")

    errors = {}
    if not validate_username(username):
        errors["username"] = "Must be 3-32 alphanumeric characters"
    if not validate_email(email):
        errors["email"] = "Invalid email format"
    if not validate_password(password):
        errors["password"] = "Must be at least 12 characters"
    if errors:
        return jsonify({"errors": errors}), 422

    with get_db() as db:
        existing = get_user_by_email(db, email)
        if existing:
            return jsonify({"error": "Email already registered"}), 409
        user = create_user(db, username, email, password)

    return jsonify({"id": user.id, "username": user.username}), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    email = data.get("email", "")
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    with get_db() as db:
        user = get_user_by_email(db, email)
        if not user or not user.is_active or not verify_password(user, password):
            return jsonify({"error": "Invalid credentials"}), 401

        # Clear session before setting new identity to prevent session fixation
        session.clear()
        session.permanent = True
        session["user_id"] = user.id
        session["username"] = user.username

    return jsonify({"id": user.id, "username": user.username}), 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"}), 200


@auth_bp.route("/me", methods=["GET"])
def me():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify({"id": user_id, "username": session.get("username")}), 200
