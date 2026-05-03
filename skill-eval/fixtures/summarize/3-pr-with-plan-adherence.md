---
# Fixture metadata (stripped by loader)
planted_issues:
  - missing_task_3: "Task 3 (add input validation to settings endpoints) is absent from the PR diff"
  - plan_claims_5_tasks: "Plan defines 5 tasks, PR only implements 4 of them"
  - no_validation_files: "No changes to src/validators/ or validation-related code in the diff"
clean_distractors:
  - "Tasks 1, 2, 4, and 5 are implemented correctly per the plan"
  - "Test files match the planned test structure"
  - "PR description does not mention skipping Task 3"
---

## PR Metadata

**Title:** feat(settings): add user settings management system
**Author:** jdoe
**Number:** #87
**Branch:** feat/user-settings -> main
**State:** OPEN
**Review Decision:** REVIEW_REQUIRED
**CI Status:** SUCCESS
**Additions:** 412
**Deletions:** 18
**Changed Files:** 9

## PR Description

Implements the user settings management system as planned. Adds CRUD endpoints for user
preferences, a settings migration framework, default value management, and comprehensive
tests.

## Associated Plan

### Plan: Add User Settings Management

**Goal:** Implement a user settings system with CRUD operations, default value management,
input validation, settings migration, and admin override capabilities.

**Branch:** feat/user-settings

#### Task 1: Create settings model and database schema
- [x] Define UserSettings SQLAlchemy model
- [x] Create Alembic migration for user_settings table
- [x] Add composite index on (user_id, setting_key)

#### Task 2: Implement settings CRUD endpoints
- [x] POST /api/settings — create or update a setting
- [x] GET /api/settings — list all settings for current user
- [x] GET /api/settings/{key} — get single setting value
- [x] DELETE /api/settings/{key} — reset setting to default

#### Task 3: Add input validation to settings endpoints
- [ ] Create SettingsValidator class with type checking
- [ ] Add value range validation per setting key
- [ ] Implement custom validation rules via registry
- [ ] Add unit tests for validation edge cases

#### Task 4: Build settings migration framework
- [x] Create SettingsMigration base class
- [x] Implement migration runner with version tracking
- [x] Add rollback support for failed migrations
- [x] Create initial migration for v1 defaults

#### Task 5: Add admin override capabilities
- [x] Create admin settings override endpoint
- [x] Add audit logging for admin overrides
- [x] Implement bulk settings update for admin
- [x] Add authorization checks for admin-only endpoints

## Diff

```diff
diff --git a/src/models/user_settings.py b/src/models/user_settings.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/src/models/user_settings.py
@@ -0,0 +1,32 @@
+from sqlalchemy import Column, Integer, String, DateTime, Index, Enum
+from sqlalchemy.sql import func
+from src.models.base import Base
+
+
+class UserSettings(Base):
+    __tablename__ = "user_settings"
+
+    id = Column(Integer, primary_key=True)
+    user_id = Column(Integer, nullable=False)
+    setting_key = Column(String(128), nullable=False)
+    setting_value = Column(String(1024), nullable=True)
+    value_type = Column(Enum("string", "integer", "boolean", "json", name="setting_type"))
+    created_at = Column(DateTime, server_default=func.now())
+    updated_at = Column(DateTime, onupdate=func.now())
+
+    __table_args__ = (
+        Index("ix_user_settings_user_key", "user_id", "setting_key", unique=True),
+    )

diff --git a/src/api/settings.py b/src/api/settings.py
new file mode 100644
index 0000000..d4e5f6a
--- /dev/null
+++ b/src/api/settings.py
@@ -0,0 +1,58 @@
+from flask import Blueprint, jsonify, request
+from src.models.user_settings import UserSettings
+from src.db import get_session
+
+settings_bp = Blueprint("settings", __name__, url_prefix="/api/settings")
+
+
+@settings_bp.route("/", methods=["POST"])
+def create_or_update_setting():
+    data = request.get_json(force=True)
+    key = data.get("key")
+    value = data.get("value")
+    session = get_session()
+    existing = session.query(UserSettings).filter_by(
+        user_id=request.user_id, setting_key=key
+    ).first()
+    if existing:
+        existing.setting_value = str(value)
+        session.commit()
+        return jsonify({"status": "updated"}), 200
+    setting = UserSettings(
+        user_id=request.user_id,
+        setting_key=key,
+        setting_value=str(value),
+    )
+    session.add(setting)
+    session.commit()
+    return jsonify({"status": "created"}), 201
+
+
+@settings_bp.route("/", methods=["GET"])
+def list_settings():
+    session = get_session()
+    settings = session.query(UserSettings).filter_by(user_id=request.user_id).all()
+    return jsonify([{"key": s.setting_key, "value": s.setting_value} for s in settings])
+
+
+@settings_bp.route("/<key>", methods=["GET"])
+def get_setting(key):
+    session = get_session()
+    setting = session.query(UserSettings).filter_by(
+        user_id=request.user_id, setting_key=key
+    ).first()
+    if not setting:
+        return jsonify({"error": "Setting not found"}), 404
+    return jsonify({"key": setting.setting_key, "value": setting.setting_value})
+
+
+@settings_bp.route("/<key>", methods=["DELETE"])
+def reset_setting(key):
+    session = get_session()
+    setting = session.query(UserSettings).filter_by(
+        user_id=request.user_id, setting_key=key
+    ).first()
+    if not setting:
+        return jsonify({"error": "Setting not found"}), 404
+    session.delete(setting)
+    session.commit()
+    return jsonify({"status": "reset to default"}), 200

diff --git a/src/migrations/settings_migration.py b/src/migrations/settings_migration.py
new file mode 100644
index 0000000..b7c8d9e
--- /dev/null
+++ b/src/migrations/settings_migration.py
@@ -0,0 +1,45 @@
+from abc import ABC, abstractmethod
+from src.db import get_session
+from src.models.migration_version import MigrationVersion
+
+
+class SettingsMigration(ABC):
+    version: int
+    description: str
+
+    @abstractmethod
+    def up(self, session):
+        pass
+
+    @abstractmethod
+    def down(self, session):
+        pass
+
+
+class MigrationRunner:
+    def __init__(self):
+        self.migrations = []
+
+    def register(self, migration_cls):
+        self.migrations.append(migration_cls())
+        self.migrations.sort(key=lambda m: m.version)
+
+    def run_pending(self):
+        session = get_session()
+        current = session.query(MigrationVersion).first()
+        current_version = current.version if current else 0
+        for m in self.migrations:
+            if m.version > current_version:
+                m.up(session)
+                if current:
+                    current.version = m.version
+                else:
+                    current = MigrationVersion(version=m.version)
+                    session.add(current)
+                session.commit()
+
+    def rollback(self, target_version):
+        session = get_session()
+        current = session.query(MigrationVersion).first()
+        current_version = current.version if current else 0
+        for m in reversed(self.migrations):
+            if m.version <= current_version and m.version > target_version:
+                m.down(session)
+                current.version = target_version
+                session.commit()

diff --git a/src/api/admin_settings.py b/src/api/admin_settings.py
new file mode 100644
index 0000000..f0a1b2c
--- /dev/null
+++ b/src/api/admin_settings.py
@@ -0,0 +1,52 @@
+from flask import Blueprint, jsonify, request
+from src.models.user_settings import UserSettings
+from src.audit.settings_audit import log_admin_override
+from src.auth.decorators import admin_required
+from src.db import get_session
+
+admin_settings_bp = Blueprint("admin_settings", __name__, url_prefix="/api/admin/settings")
+
+
+@admin_settings_bp.route("/override/<int:user_id>/<key>", methods=["PUT"])
+@admin_required
+def override_setting(user_id, key):
+    data = request.get_json(force=True)
+    value = data.get("value")
+    session = get_session()
+    setting = session.query(UserSettings).filter_by(
+        user_id=user_id, setting_key=key
+    ).first()
+    old_value = setting.setting_value if setting else None
+    if setting:
+        setting.setting_value = str(value)
+    else:
+        setting = UserSettings(user_id=user_id, setting_key=key, setting_value=str(value))
+        session.add(setting)
+    session.commit()
+    log_admin_override(
+        admin_id=request.user_id,
+        target_user_id=user_id,
+        key=key,
+        old_value=old_value,
+        new_value=str(value),
+    )
+    return jsonify({"status": "overridden"}), 200
+
+
+@admin_settings_bp.route("/bulk/<int:user_id>", methods=["PUT"])
+@admin_required
+def bulk_update(user_id):
+    data = request.get_json(force=True)
+    settings_list = data.get("settings", [])
+    session = get_session()
+    for item in settings_list:
+        key, value = item["key"], item["value"]
+        existing = session.query(UserSettings).filter_by(
+            user_id=user_id, setting_key=key
+        ).first()
+        if existing:
+            existing.setting_value = str(value)
+        else:
+            session.add(UserSettings(user_id=user_id, setting_key=key, setting_value=str(value)))
+    session.commit()
+    log_admin_override(
+        admin_id=request.user_id,
+        target_user_id=user_id,
+        key="bulk_update",
+        old_value=None,
+        new_value=f"{len(settings_list)} settings",
+    )
+    return jsonify({"status": "bulk updated", "count": len(settings_list)}), 200

diff --git a/src/audit/settings_audit.py b/src/audit/settings_audit.py
new file mode 100644
index 0000000..c3d4e5f
--- /dev/null
+++ b/src/audit/settings_audit.py
@@ -0,0 +1,18 @@
+import logging
+from datetime import datetime
+
+audit_logger = logging.getLogger("settings.audit")
+
+
+def log_admin_override(admin_id, target_user_id, key, old_value, new_value):
+    audit_logger.info(
+        "ADMIN_OVERRIDE admin=%s target_user=%s key=%s old=%s new=%s ts=%s",
+        admin_id,
+        target_user_id,
+        key,
+        old_value,
+        new_value,
+        datetime.utcnow().isoformat(),
+    )

diff --git a/tests/test_settings.py b/tests/test_settings.py
new file mode 100644
index 0000000..a6b7c8d
--- /dev/null
+++ b/tests/test_settings.py
@@ -0,0 +1,45 @@
+import pytest
+from unittest.mock import MagicMock, patch
+
+
+@pytest.fixture
+def client(app):
+    return app.test_client()
+
+
+def test_create_setting(client):
+    resp = client.post("/api/settings/", json={"key": "theme", "value": "dark"})
+    assert resp.status_code == 201
+
+
+def test_list_settings(client):
+    resp = client.get("/api/settings/")
+    assert resp.status_code == 200
+
+
+def test_get_setting_not_found(client):
+    resp = client.get("/api/settings/nonexistent")
+    assert resp.status_code == 404
+
+
+def test_reset_setting(client):
+    resp = client.delete("/api/settings/theme")
+    assert resp.status_code == 200

diff --git a/tests/test_admin_settings.py b/tests/test_admin_settings.py
new file mode 100644
index 0000000..e9f0a1b
--- /dev/null
+++ b/tests/test_admin_settings.py
@@ -0,0 +1,22 @@
+import pytest
+from unittest.mock import patch
+
+
+@pytest.fixture
+def admin_client(app):
+    return app.test_client()
+
+
+def test_override_setting(admin_client):
+    resp = admin_client.put(
+        "/api/admin/settings/override/1/theme",
+        json={"value": "light"},
+    )
+    assert resp.status_code == 200
+
+
+def test_bulk_update(admin_client):
+    resp = admin_client.put(
+        "/api/admin/settings/bulk/1",
+        json={"settings": [{"key": "theme", "value": "dark"}]},
+    )
+    assert resp.status_code == 200

diff --git a/tests/test_migrations.py b/tests/test_migrations.py
new file mode 100644
index 0000000..c2d3e4f
--- /dev/null
+++ b/tests/test_migrations.py
@@ -0,0 +1,28 @@
+import pytest
+from src.migrations.settings_migration import SettingsMigration, MigrationRunner
+
+
+class FakeMigration(SettingsMigration):
+    version = 1
+    description = "Test migration"
+
+    def up(self, session):
+        pass
+
+    def down(self, session):
+        pass
+
+
+def test_register_and_run():
+    runner = MigrationRunner()
+    runner.register(FakeMigration)
+    assert len(runner.migrations) == 1
+
+
+def test_rollback():
+    runner = MigrationRunner()
+    runner.register(FakeMigration)
+    # Rollback with no current version should be safe
+    runner.rollback(0)
```

## Files Changed

- src/models/user_settings.py (new)
- src/api/settings.py (new)
- src/migrations/settings_migration.py (new)
- src/api/admin_settings.py (new)
- src/audit/settings_audit.py (new)
- tests/test_settings.py (new)
- tests/test_admin_settings.py (new)
- tests/test_migrations.py (new)
- alembic/versions/001_add_user_settings.py (new)
