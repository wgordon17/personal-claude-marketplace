# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

from datetime import datetime
from flask import Blueprint, request, jsonify, session
from src.db import get_session
from src.models.user import User
from src.auth.tokens import create_token
import bcrypt


auth_bp = Blueprint("auth", __name__)


def verify_password(password, password_hash):
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def hash_password(password):
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data or "username" not in data or "password" not in data:
        return jsonify({"error": "Username and password required"}), 400

    username = data["username"]
    password = data["password"]

    db_session = get_session()
    user = db_session.query(User).filter_by(username=username, is_active=True).first()

    if user is None or not verify_password(password, user.password_hash):
        return jsonify({"error": "Invalid credentials"}), 401

    session["user_id"] = user.id
    session["role"] = user.role.value
    session["logged_in"] = True

    user.last_login = datetime.utcnow()
    db_session.commit()

    token = create_token(user.id, user.role.value)
    return jsonify({"token": token, "user": user.to_dict()}), 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out successfully"}), 200


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    required = ["username", "email", "password"]
    if not data or not all(k in data for k in required):
        return jsonify({"error": "username, email, and password required"}), 400

    db_session = get_session()
    existing = db_session.query(User).filter(
        (User.username == data["username"]) | (User.email == data["email"])
    ).first()
    if existing:
        return jsonify({"error": "Username or email already taken"}), 409

    user = User(
        username=data["username"],
        email=data["email"],
        password_hash=hash_password(data["password"]),
    )
    db_session.add(user)
    db_session.commit()
    return jsonify({"user": user.to_dict()}), 201
