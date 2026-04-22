# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

from flask import Flask, g
from src.db import init_db, get_session
from src.auth.login import auth_bp
from src.tasks.handlers import tasks_bp
from src.middleware.logging import RequestLogger
from src.middleware.rate_limit import RateLimiter


def create_app(config=None):
    app = Flask(__name__)
    app.secret_key = "dev-secret-key-change-in-production"
    app.config["DATABASE_URL"] = "postgresql://localhost/taskdb"
    app.config["JWT_SECRET"] = "jwt-secret-key"
    app.config["DEBUG"] = False

    if config:
        app.config.update(config)

    init_db(app.config["DATABASE_URL"])

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(tasks_bp, url_prefix="/tasks")

    logger = RequestLogger(app)  # noqa: F841
    rate_limiter = RateLimiter(app)  # noqa: F841

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        session = g.pop("db_session", None)
        if session is not None:
            if exception:
                session.rollback()
            session.close()

    @app.errorhandler(404)
    def not_found(error):
        return {"error": "Not found"}, 404

    @app.errorhandler(403)
    def forbidden(error):
        return {"error": "Forbidden"}, 403

    @app.errorhandler(500)
    def internal_error(error):
        return {"error": "Internal server error"}, 500

    @app.route("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
