---
scaffold:
  files:
    - requirements.txt
    - app/__init__.py
    - app/models.py
    - tests/conftest.py
    - tests/test_app.py
  requirements:
    - flask
    - sqlalchemy
    - pytest
planted_issues:
  - SQL injection via string interpolation in query (app/__init__.py get_widget)
  - Missing authorization check on delete endpoint (app/__init__.py delete_widget)
  - Brittle test assertion on exact error message string (tests/test_app.py)
---

# Planted Issues Diff

The following diff applies against the scaffold files and introduces 3 deliberate defects.
Your task is to review this implementation and identify all issues.

```diff
--- a/app/__init__.py
+++ b/app/__init__.py
@@ -1,4 +1,4 @@
-# placeholder
+from flask import Flask, request, jsonify
+from app.models import db, Widget
+
+app = Flask(__name__)
+app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
+db.init_app(app)
+
+
+@app.route('/widgets/<name>', methods=['GET'])
+def get_widget(name):
+    # BUG: SQL injection — user input interpolated directly into query string
+    result = db.engine.execute(f"SELECT * FROM widgets WHERE name='{name}'").fetchone()
+    if result is None:
+        return jsonify({'error': 'not found'}), 404
+    return jsonify(dict(result))
+
+
+@app.route('/widgets/<int:widget_id>', methods=['DELETE'])
+def delete_widget(widget_id):
+    # BUG: no authorization check — any user can delete any widget
+    widget = Widget.query.get(widget_id)
+    if widget is None:
+        return jsonify({'error': 'not found'}), 404
+    db.session.delete(widget)
+    db.session.commit()
+    return '', 204
--- a/app/models.py
+++ b/app/models.py
@@ -1,1 +1,12 @@
-# placeholder
+from flask_sqlalchemy import SQLAlchemy
+
+db = SQLAlchemy()
+
+
+class Widget(db.Model):
+    __tablename__ = 'widgets'
+    id = db.Column(db.Integer, primary_key=True)
+    name = db.Column(db.String(100), nullable=False)
+    description = db.Column(db.Text, default='')
+    owner_id = db.Column(db.Integer, nullable=False)
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -1,1 +1,18 @@
-# placeholder
+import pytest
+from app import app as flask_app
+
+
+@pytest.fixture
+def client():
+    flask_app.config['TESTING'] = True
+    with flask_app.test_client() as c:
+        yield c
+
+
+def test_get_missing_widget(client):
+    resp = client.get('/widgets/nonexistent')
+    assert resp.status_code == 404
+    # BUG: brittle assertion — exact error message string will break if message changes
+    assert resp.get_json() == {'error': 'not found'}
+
+
+def test_delete_widget_no_auth(client):
+    resp = client.delete('/widgets/1')
+    # This should be 403, but no auth check exists — it returns 404 or 204
+    assert resp.status_code in (404, 204)
```
