---
scenario: new-resource-follows-established-pattern
difficulty: hard
tests:
  - follows established file naming pattern
  - uses models/project.py handlers/project.py tests/test_project.py
  - references existing pattern as justification
---

## Task

Add a new API resource for projects. Projects belong to a team, have a name and description, and support CRUD operations.

## Codebase Summary

### Project Structure

```
src/
  models/
    user.py                  # User model: id, username, email, role, created_at
    team.py                  # Team model: id, name, owner_id, created_at
    org.py                   # Org model: id, name, plan, created_at
  handlers/
    user.py                  # CRUD handlers for /users
    team.py                  # CRUD handlers for /teams
    org.py                   # CRUD handlers for /orgs
  middleware/
    auth.py                  # JWT auth middleware
    validation.py            # Request body validation middleware
  db/
    connection.py            # Database connection pool
    migrations/
      001_users.sql
      002_teams.sql
      003_orgs.sql
  config/
    settings.py              # App configuration
  app.py                     # Flask app factory, registers blueprints
tests/
  test_user.py               # Tests for user CRUD
  test_team.py               # Tests for team CRUD
  test_org.py                # Tests for org CRUD
  conftest.py                # Shared test fixtures (db, client, auth tokens)
```

### Key Files -- Existing Resource Pattern

**src/models/team.py** -- Representative model:
```python
from dataclasses import dataclass, field
from datetime import datetime
from src.db.connection import get_db

@dataclass
class Team:
    id: int | None = None
    name: str = ""
    owner_id: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def create(cls, name: str, owner_id: int) -> "Team":
        db = get_db()
        result = db.execute(
            "INSERT INTO teams (name, owner_id) VALUES (%s, %s) RETURNING id, created_at",
            (name, owner_id),
        )
        row = result.fetchone()
        return cls(id=row[0], name=name, owner_id=owner_id, created_at=row[1])

    @classmethod
    def get_by_id(cls, team_id: int) -> "Team | None":
        db = get_db()
        result = db.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        row = result.fetchone()
        return cls(*row) if row else None

    @classmethod
    def list_all(cls) -> list["Team"]:
        db = get_db()
        result = db.execute("SELECT * FROM teams ORDER BY created_at DESC")
        return [cls(*row) for row in result.fetchall()]

    def update(self, **kwargs) -> None:
        db = get_db()
        sets = ", ".join(f"{k} = %s" for k in kwargs)
        values = list(kwargs.values()) + [self.id]
        db.execute(f"UPDATE teams SET {sets} WHERE id = %s", values)
        for k, v in kwargs.items():
            setattr(self, k, v)

    def delete(self) -> None:
        db = get_db()
        db.execute("DELETE FROM teams WHERE id = %s", (self.id,))
```

**src/handlers/team.py** -- Representative handler:
```python
from flask import Blueprint, request, jsonify
from src.models.team import Team
from src.middleware.auth import require_auth
from src.middleware.validation import validate_body

teams_bp = Blueprint("teams", __name__, url_prefix="/teams")

@teams_bp.route("", methods=["GET"])
@require_auth
def list_teams():
    teams = Team.list_all()
    return jsonify([vars(t) for t in teams])

@teams_bp.route("", methods=["POST"])
@require_auth
@validate_body({"name": str})
def create_team():
    data = request.get_json()
    team = Team.create(name=data["name"], owner_id=request.user_id)
    return jsonify(vars(team)), 201

@teams_bp.route("/<int:team_id>", methods=["GET"])
@require_auth
def get_team(team_id: int):
    team = Team.get_by_id(team_id)
    if not team:
        return jsonify({"error": "Not found"}), 404
    return jsonify(vars(team))

@teams_bp.route("/<int:team_id>", methods=["PUT"])
@require_auth
@validate_body({"name": str})
def update_team(team_id: int):
    team = Team.get_by_id(team_id)
    if not team:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json()
    team.update(**data)
    return jsonify(vars(team))

@teams_bp.route("/<int:team_id>", methods=["DELETE"])
@require_auth
def delete_team(team_id: int):
    team = Team.get_by_id(team_id)
    if not team:
        return jsonify({"error": "Not found"}), 404
    team.delete()
    return "", 204
```

**tests/test_team.py** -- Representative test file:
```python
import pytest
from src.models.team import Team

class TestTeamModel:
    def test_create_team(self, db):
        team = Team.create(name="Engineering", owner_id=1)
        assert team.id is not None
        assert team.name == "Engineering"

    def test_get_by_id(self, db, sample_team):
        found = Team.get_by_id(sample_team.id)
        assert found is not None
        assert found.name == sample_team.name

    def test_list_all(self, db, sample_team):
        teams = Team.list_all()
        assert len(teams) >= 1

    def test_update(self, db, sample_team):
        sample_team.update(name="New Name")
        assert sample_team.name == "New Name"
        refreshed = Team.get_by_id(sample_team.id)
        assert refreshed.name == "New Name"

    def test_delete(self, db, sample_team):
        sample_team.delete()
        assert Team.get_by_id(sample_team.id) is None

class TestTeamHandlers:
    def test_list_teams(self, client, auth_headers):
        resp = client.get("/teams", headers=auth_headers)
        assert resp.status_code == 200

    def test_create_team(self, client, auth_headers):
        resp = client.post("/teams", json={"name": "Test"}, headers=auth_headers)
        assert resp.status_code == 201

    def test_get_team_not_found(self, client, auth_headers):
        resp = client.get("/teams/99999", headers=auth_headers)
        assert resp.status_code == 404
```

**src/app.py** -- Blueprint registration pattern:
```python
from flask import Flask
from src.handlers.user import users_bp
from src.handlers.team import teams_bp
from src.handlers.org import orgs_bp

def create_app():
    app = Flask(__name__)
    app.register_blueprint(users_bp)
    app.register_blueprint(teams_bp)
    app.register_blueprint(orgs_bp)
    return app
```

### Dependencies

- Flask 3.0, psycopg2 2.9
- PostgreSQL 16
- pytest 8.1

## Simulated User Answers

Round 1 answer: "Projects belong to a team (team_id foreign key). Fields: id, name, description, team_id, created_at."
Round 2 answer: "Full CRUD -- list, create, get, update, delete. Follow the same pattern as teams and orgs. Only team members can access their team's projects."
Round 3 answer: "Yes, register the blueprint in app.py like the others. Add a migration file 004_projects.sql."
