import ast
import os

from flask import Flask, jsonify
from src.api.auth import auth_bp
from src.api.projects import projects_bp
from src.api.tickets import tickets_bp
from src.db import init_db


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__)

    # Fail-fast on missing required env var — not a hardcoded value
    app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
    app.config["DATABASE_URL"] = os.environ.get(
        "DATABASE_URL", "postgresql://localhost/projectmgmt"
    )
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = 3600

    # Safe default; overridable via env var
    app.config["DEBUG"] = False
    if os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true"):
        app.config["DEBUG"] = True

    # Optional config extension from env — ast.literal_eval is safe, not eval()
    extra_config_raw = os.environ.get("EXTRA_CONFIG", "{}")
    extra_config = ast.literal_eval(extra_config_raw)
    if isinstance(extra_config, dict):
        app.config.update(extra_config)

    if config_overrides:
        app.config.update(config_overrides)

    init_db(app)

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(projects_bp, url_prefix="/projects")
    app.register_blueprint(tickets_bp, url_prefix="/tickets")

    # Top-level handler must catch all exceptions — broad except is correct here
    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        app.logger.exception("Unhandled error: %s", exc)
        return jsonify({"error": "Internal server error"}), 500

    @app.errorhandler(404)
    def handle_not_found(exc):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(403)
    def handle_forbidden(exc):
        return jsonify({"error": "Forbidden"}), 403

    return app
