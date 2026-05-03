## Review Findings

### Finding 1: Missing input validation on project title
**Category:** Correctness
**Severity:** Medium
**Files:** src/api/projects.py:34
**Description:** `create_project()` accepts any string for `title`, including empty strings and strings over 1000 characters. Empty titles produce confusing UI entries. Unbounded strings risk database column overflow.
**Recommended fix:** Add validation: title must be non-empty after stripping whitespace and under 200 characters.

### Finding 2: Missing rate limiting on report generation
**Category:** Performance
**Severity:** High
**Files:** src/api/reports.py:15
**Description:** The `POST /reports/generate` endpoint has no rate limiting. Report generation is CPU-intensive (joins across 5 tables with aggregation). A user could trigger 50 concurrent reports and exhaust the database connection pool.
**Code:**
```python
@reports_bp.route("/reports/generate", methods=["POST"])
@require_auth
def generate_report():
    params = request.get_json()
    report = ReportEngine.generate(
        user_id=g.current_user["id"],
        report_type=params["type"],
        date_range=params.get("date_range", "30d"),
    )
    return jsonify(report.to_dict()), 200
```

## Codebase Context — Existing callers of create_project()

```python
# src/api/projects.py:34 — the function under review
def create_project(title: str, description: str = "", owner_id: int = None) -> Project:
    """Create a new project record."""
    project = Project(title=title, description=description, owner_id=owner_id)
    db.session.add(project)
    db.session.commit()
    return project


# src/api/import_handler.py:45 — CSV bulk import
def import_projects_from_csv(csv_path: str, owner_id: int) -> list[Project]:
    """Import projects from CSV. Rows with empty titles create draft projects."""
    projects = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            # Empty title = draft project (intentional business rule)
            project = create_project(
                title=row.get("title", ""),
                description=row.get("description", ""),
                owner_id=owner_id,
            )
            projects.append(project)
    return projects


# src/api/templates.py:23 — template instantiation
def create_from_template(template_id: int, owner_id: int) -> Project:
    """Create a project from a template. Template names are pre-validated."""
    template = ProjectTemplate.query.get_or_404(template_id)
    return create_project(
        title=template.name,
        description=template.description,
        owner_id=owner_id,
    )


# tests/test_projects.py:52 — test fixtures
class TestProjectModel:
    def test_create_draft_project(self):
        """Draft projects have empty titles — this is a valid state."""
        project = create_project(title="", owner_id=1)
        assert project.id is not None
        assert project.title == ""

    def test_create_named_project(self):
        project = create_project(title="Test Project", owner_id=1)
        assert project.title == "Test Project"


# db/migrations/0038_seed_data.py:12 — seed data migration
def seed_system_projects():
    """Create system placeholder projects. Run once on first deploy."""
    create_project(title="", description="System placeholder", owner_id=SYSTEM_USER_ID)
    create_project(title="", description="Archived bucket", owner_id=SYSTEM_USER_ID)
```

## Notes

Finding 1 has a regression risk: rejecting empty titles would break the CSV importer (draft projects), the test fixture (test_create_draft_project), and the seed migration (system placeholders). A correct fix must distinguish between API-level validation (where empty titles should be rejected) and internal callers (where empty titles represent draft projects).
