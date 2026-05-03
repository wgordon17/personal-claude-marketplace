---
clean_patterns:
  - "Parameterized queries in all models"
  - "SSRF protection in validation.py"
  - "Proper error handling"
expected_outcome: "No significant issues — clean code"
---

## PR: Add ticket management API

**Description:** Adds ticket CRUD endpoints for project-based ticket management. Tickets belong to projects and support status/priority tracking. Validation is handled at the API layer. Tests cover auth requirements, access control, and validation boundaries.

### src/api/tickets.py
{codebase:clean-flask-app/src/api/tickets.py}

### src/models/ticket.py
{codebase:clean-flask-app/src/models/ticket.py}

### src/utils/validation.py
{codebase:clean-flask-app/src/utils/validation.py}

### tests/test_projects.py
{codebase:clean-flask-app/tests/test_projects.py}
