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
  - Should use SQLAlchemy ORM queries not raw SQL
  - Should use keyset/cursor pagination not offset for large datasets
  - Should use a schema validation library not manual if/elif chains
---

# CRUD Plan: Paginated REST API in Flask

## Goal
Build a CRUD REST API for a `Widget` resource with paginated list support.
The API should support create, read, update, delete operations and return
paginated results on the list endpoint.

## Outcomes Required
- POST /widgets creates a new widget and returns it
- GET /widgets returns a paginated list with metadata
- GET /widgets/<id> returns a single widget or 404
- PUT /widgets/<id> updates and returns the widget
- DELETE /widgets/<id> deletes the widget and returns 204
- Pagination works correctly for large datasets
- Input validation rejects malformed requests with 400

## Notes
The app uses Flask with SQLAlchemy. Follow ORM conventions — avoid raw SQL.
Pagination approach, validation strategy, and error handling structure
are implementation decisions. Tests should cover CRUD operations and
pagination edge cases.
