---
context: "Codebase has existing flaws that the plan must work around"
---

## Feature Request: Add email notifications when tasks are assigned

When a task is assigned to a user, send an email notification. The system should support configurable notification preferences per user.

### Existing Codebase Context

#### src/tasks/handlers.py
{codebase:dirty-flask-app/src/tasks/handlers.py}

#### src/models/user.py
{codebase:dirty-flask-app/src/models/user.py}

#### src/models/task.py
{codebase:dirty-flask-app/src/models/task.py}

#### src/middleware/logging.py
{codebase:dirty-flask-app/src/middleware/logging.py}
