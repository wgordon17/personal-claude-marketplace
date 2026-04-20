---
planted_issues:
  - type: overly_restrictive_role_check
    file: src/api/endpoints/reports.py
    line: 18
    description: Checks only "admin" but should also allow "editor"
  - type: missing_test_coverage
    file: tests/test_reports.py
    description: No test for editor role access
expected_findings: 2
---

diff --git a/src/api/endpoints/reports.py b/src/api/endpoints/reports.py
new file mode 100644
index 0000000..a3f7c21
--- /dev/null
+++ b/src/api/endpoints/reports.py
@@ -0,0 +1,47 @@
+from fastapi import APIRouter, Depends, HTTPException
+from sqlalchemy.orm import Session
+
+from src.api.auth import get_current_user
+from src.api.deps import get_db
+from src.models.report import Report
+from src.models.user import User
+from src.schemas.report import ReportCreate, ReportResponse
+
+router = APIRouter(prefix="/reports", tags=["reports"])
+
+
+def require_report_access(user: User = Depends(get_current_user)) -> User:
+    """Ensure the user has permission to access reports.
+
+    Admins and editors can generate and view reports.
+    """
+    if user.role != "admin":
+        raise HTTPException(status_code=403, detail="Insufficient permissions")
+    return user
+
+
+@router.post("/", response_model=ReportResponse)
+async def create_report(
+    payload: ReportCreate,
+    db: Session = Depends(get_db),
+    user: User = Depends(require_report_access),
+) -> ReportResponse:
+    report = Report(
+        title=payload.title,
+        query=payload.query,
+        created_by=user.id,
+    )
+    db.add(report)
+    db.commit()
+    db.refresh(report)
+    return ReportResponse.model_validate(report)
+
+
+@router.get("/{report_id}", response_model=ReportResponse)
+async def get_report(
+    report_id: int,
+    db: Session = Depends(get_db),
+    user: User = Depends(require_report_access),
+) -> ReportResponse:
+    report = db.query(Report).filter(Report.id == report_id).first()
+    if not report:
+        raise HTTPException(status_code=404, detail="Report not found")
+    return ReportResponse.model_validate(report)
+
diff --git a/tests/test_reports.py b/tests/test_reports.py
new file mode 100644
index 0000000..b8e1d42
--- /dev/null
+++ b/tests/test_reports.py
@@ -0,0 +1,32 @@
+import pytest
+from fastapi.testclient import TestClient
+
+from src.main import app
+
+client = TestClient(app)
+
+
+def test_create_report_as_admin(admin_auth_headers):
+    response = client.post(
+        "/reports/",
+        json={"title": "Q4 Sales", "query": "SELECT * FROM sales WHERE quarter=4"},
+        headers=admin_auth_headers,
+    )
+    assert response.status_code == 200
+    data = response.json()
+    assert data["title"] == "Q4 Sales"
+
+
+def test_create_report_as_viewer(viewer_auth_headers):
+    response = client.post(
+        "/reports/",
+        json={"title": "Q4 Sales", "query": "SELECT * FROM sales WHERE quarter=4"},
+        headers=viewer_auth_headers,
+    )
+    assert response.status_code == 403
+
+
+def test_get_report_not_found(admin_auth_headers):
+    response = client.get("/reports/99999", headers=admin_auth_headers)
+    assert response.status_code == 404
+
