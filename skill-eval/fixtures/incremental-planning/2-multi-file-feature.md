---
scenario: rate-limiting-feature-with-dependency-chain
difficulty: hard
tests:
  - produces file structure mapping
  - correct dependency ordering (config then middleware then integration then tests)
  - no parallel tasks on same file
  - uses Redis for state
---

## Task

Add rate limiting to the API. Use Redis-based tracking, per-user limits, implement as middleware, 100 requests per minute default.

## Codebase Summary

### Project Structure

```
src/
  api/
    routes.py                # Route definitions: /users, /teams, /projects
    middleware.py            # Middleware chain: auth_middleware, logging_middleware
  auth/
    jwt_handler.py           # JWT validation, extracts user_id from token
    permissions.py           # Role-based permission checks
  storage/
    redis_client.py          # Redis connection pool, used for caching
    cache.py                 # Application-level cache helpers
  models/
    user.py                  # User model with role field
  config/
    settings.py              # App config from environment variables
tests/
  test_routes.py
  test_auth.py
  test_cache.py
requirements.txt
```

### Key Files

**src/api/middleware.py** -- Existing middleware chain:
```python
from functools import wraps
from flask import request, g
from src.auth.jwt_handler import validate_jwt

def auth_middleware(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        payload = validate_jwt(token)
        if not payload:
            return {"error": "Unauthorized"}, 401
        g.user_id = payload["user_id"]
        g.user_role = payload["role"]
        return f(*args, **kwargs)
    return decorated

def logging_middleware(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        import logging
        logging.info("Request: %s %s user=%s", request.method, request.path, g.get("user_id"))
        return f(*args, **kwargs)
    return decorated
```

**src/api/routes.py** -- Three API endpoints:
```python
from flask import Blueprint, request, jsonify
from src.api.middleware import auth_middleware, logging_middleware
from src.models.user import User

api = Blueprint("api", __name__)

@api.route("/users", methods=["GET"])
@auth_middleware
@logging_middleware
def list_users():
    users = User.query.all()
    return jsonify([u.to_dict() for u in users])

@api.route("/teams", methods=["GET", "POST"])
@auth_middleware
@logging_middleware
def teams():
    if request.method == "POST":
        data = request.get_json()
        # create team logic
        return jsonify({"status": "created"}), 201
    return jsonify(Team.query.all())

@api.route("/projects", methods=["GET", "POST"])
@auth_middleware
@logging_middleware
def projects():
    if request.method == "POST":
        data = request.get_json()
        # create project logic
        return jsonify({"status": "created"}), 201
    return jsonify(Project.query.all())
```

**src/storage/redis_client.py** -- Redis connection pool:
```python
import redis

class RedisClient:
    _pool = None

    @classmethod
    def get_pool(cls):
        if cls._pool is None:
            cls._pool = redis.ConnectionPool(
                host="localhost", port=6379, db=0, decode_responses=True
            )
        return cls._pool

    @classmethod
    def get_client(cls):
        return redis.Redis(connection_pool=cls.get_pool())
```

**src/config/settings.py** -- App configuration:
```python
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/app")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
```

### Dependencies

- Flask 3.0, SQLAlchemy 2.0, PyJWT 2.8
- redis-py 5.0
- PostgreSQL 16, Redis 7.2

## Simulated User Answers

Round 1 answer: "Redis-based, using the existing RedisClient connection pool."
Round 2 answer: "Per-user, identified by user_id from JWT. 100 requests per minute default. Admin role is exempt."
Round 3 answer: "Implement as a decorator middleware like auth_middleware. Should go after auth_middleware (needs user_id) but before logging_middleware. Return 429 with Retry-After header when exceeded."
