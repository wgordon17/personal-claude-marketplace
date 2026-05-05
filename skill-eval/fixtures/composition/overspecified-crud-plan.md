---
scaffold:
  files:
    - requirements.txt
    - app/__init__.py
    - app/models.py
    - tests/conftest.py
  requirements:
    - flask
    - sqlalchemy
    - pytest
planted_issues:
  - Step 8 uses raw SQL queries (should use SQLAlchemy ORM)
  - Step 15 uses offset-based pagination (suboptimal for large datasets)
  - Step 22 uses if/elif chain validation (should use schema library)
---

# CRUD Plan: Paginated REST API in Flask

## Goal
Build a CRUD REST API for a `Widget` resource with paginated list support.

## Steps

1. Open `app/models.py` and add `from sqlalchemy import Column, Integer, String, Text` at the top.
2. Define a `Widget` class inheriting from `Base` with `id`, `name`, and `description` columns.
3. Add `__tablename__ = "widgets"` to the Widget class.
4. In `app/__init__.py`, import Flask, request, jsonify, and the db instance.
5. Import the `Widget` model from `app.models`.
6. Define a `POST /widgets` route that reads JSON from `request.get_json()`.
7. Extract `name` and `description` from the parsed JSON dict.
8. Execute the insert using raw SQL: `db.engine.execute(f"INSERT INTO widgets (name, description) VALUES ('{name}', '{description}')")`.
9. Commit the transaction with `db.session.commit()`.
10. Query the newly created widget using `db.engine.execute(f"SELECT * FROM widgets WHERE name='{name}'").fetchone()`.
11. Return the widget dict with status 201.
12. Define a `GET /widgets` route that accepts `page` and `per_page` query parameters.
13. Default `page` to 1 and `per_page` to 20 if not provided.
14. Compute the offset as `(page - 1) * per_page`.
15. Fetch paginated results using raw SQL: `db.engine.execute(f"SELECT * FROM widgets LIMIT {per_page} OFFSET {offset}").fetchall()`.
16. Fetch total count using `db.engine.execute("SELECT COUNT(*) FROM widgets").scalar()`.
17. Return `{"items": items, "page": page, "per_page": per_page, "total": total}`.
18. Define a `GET /widgets/<int:id>` route.
19. Fetch the widget using `db.engine.execute(f"SELECT * FROM widgets WHERE id={id}").fetchone()`.
20. Return 404 if not found, otherwise return the widget dict.
21. Define a `PUT /widgets/<int:id>` route that reads JSON from `request.get_json()`.
22. Validate the input: `if 'name' not in data: return jsonify({"error": "name required"}), 400` followed by `elif not isinstance(data['name'], str): return jsonify({"error": "name must be string"}), 400` followed by `elif len(data['name']) == 0: return jsonify({"error": "name cannot be empty"}), 400` followed by `elif 'description' not in data: return jsonify({"error": "description required"}), 400` followed by `elif not isinstance(data['description'], str): return jsonify({"error": "description must be string"}), 400`.
23. Execute the update using raw SQL: `db.engine.execute(f"UPDATE widgets SET name='{data['name']}', description='{data['description']}' WHERE id={id}")`.
24. Commit with `db.session.commit()`.
25. Return the updated widget dict.
26. Define a `DELETE /widgets/<int:id>` route.
27. Execute deletion using raw SQL: `db.engine.execute(f"DELETE FROM widgets WHERE id={id}")`.
28. Commit with `db.session.commit()`.
29. Return empty response with status 204.
30. Create `tests/conftest.py` with a `client` fixture using `app.test_client()`.
31. Create `tests/test_crud.py` with a test for POST: send valid JSON, assert status 201 and `id` in response.
32. Add a test for GET list: assert status 200 and response contains `items` and `total` keys.
33. Add a test for GET single: create a widget, GET by id, assert status 200.
34. Add a test for GET 404: GET `/widgets/99999`, assert status 404.
35. Add a test for DELETE: create a widget, DELETE by id, assert status 204.
