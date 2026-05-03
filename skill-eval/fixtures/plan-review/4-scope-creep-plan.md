---
planted_issues:
  - scope_creep_pdf: "Task 2 adds PDF export which is not part of the stated CSV export goal"
  - scope_creep_email: "Task 3 adds email delivery of reports, unrelated to CSV export"
  - scope_creep_scheduling: "Task 4 adds a scheduling system, far beyond the original CSV export goal"
difficulty: hard
type: positive
---

# Plan: Add CSV Export to Reports Page

**Branch:** feat/csv-export
**Status:** Draft
**Created:** 2026-04-14
**Goal:** Add CSV export to reports page.

**Cynefin Domain:** Simple

**Iterations:**
- review-cycle: 0
- fix-cycle: 0

## File Structure

```
src/
  reports/
    csv_exporter.py           # CSV generation from report data
    pdf_exporter.py           # PDF generation from report data
    export_router.py          # Route export requests to correct exporter
  email/
    report_mailer.py          # Email delivery of exported reports
    templates/
      report_email.html       # Email template for report delivery
  scheduler/
    export_scheduler.py       # Scheduled export job definitions
    cron_manager.py           # Cron job management
  api/
    export_endpoints.py       # REST endpoints for export actions
    schedule_endpoints.py     # REST endpoints for schedule management
  frontend/
    components/
      ExportButton.tsx        # Export button component
      ExportFormatPicker.tsx  # Format selection dropdown
      ScheduleModal.tsx       # Schedule configuration modal
      RecipientSelector.tsx   # Email recipient picker
tests/
  test_csv_exporter.py
  test_pdf_exporter.py
  test_export_router.py
  test_report_mailer.py
  test_export_scheduler.py
  test_export_endpoints.py
  test_schedule_endpoints.py
```

## Key Decisions

- Use the `csv` standard library module for CSV generation -- no external dependencies needed.
- Add PDF export as well since the export infrastructure is being built anyway.
- Include email delivery so users can share reports with stakeholders without manual download.
- Build a scheduling system so recurring reports can be generated and delivered automatically.

## Tasks

### Task 1: CSV Export Implementation
- **Files:** `src/reports/csv_exporter.py`, `src/api/export_endpoints.py`, `src/frontend/components/ExportButton.tsx`
- **Description:** Implement CSV generation from the existing report data model. Add a POST /reports/:id/export endpoint that accepts format="csv" and returns the file as a download. Add an "Export CSV" button to the reports page that calls the endpoint and triggers browser download. Handle large reports with streaming response to avoid memory issues. Include column headers matching the report's visible columns.
- **Test command:** `uv run pytest tests/test_csv_exporter.py tests/test_export_endpoints.py`
- **Dependencies:** None

### Task 2: PDF Export Support
- **Files:** `src/reports/pdf_exporter.py`, `src/reports/export_router.py`, `src/frontend/components/ExportFormatPicker.tsx`
- **Description:** While we are building export infrastructure, add PDF export as well. Install and configure WeasyPrint for PDF rendering. Create an export router that dispatches to CSV or PDF exporter based on the requested format. Update the frontend to show a format picker dropdown (CSV, PDF) instead of a single button. The POST /reports/:id/export endpoint already supports the format parameter from Task 1.
- **Test command:** `uv run pytest tests/test_pdf_exporter.py tests/test_export_router.py`
- **Dependencies:** Task 1

### Task 3: Email Delivery of Reports
- **Files:** `src/email/report_mailer.py`, `src/email/templates/report_email.html`, `src/frontend/components/RecipientSelector.tsx`
- **Description:** Allow users to email exported reports directly to colleagues. Implement a report mailer that accepts a list of email addresses, generates the report in the selected format, and sends it as an attachment via SendGrid. Add a recipient selector component to the frontend that lets users pick from team members or enter custom email addresses. Add POST /reports/:id/send endpoint.
- **Test command:** `uv run pytest tests/test_report_mailer.py`
- **Dependencies:** Task 1, Task 2

### Task 4: Scheduled Export System
- **Files:** `src/scheduler/export_scheduler.py`, `src/scheduler/cron_manager.py`, `src/api/schedule_endpoints.py`, `src/frontend/components/ScheduleModal.tsx`
- **Description:** Build a scheduling system so users can set up recurring report exports. Support daily, weekly, and monthly schedules. Use APScheduler with a PostgreSQL job store for persistence across restarts. Each schedule specifies: report ID, export format, recipient list, cron expression. Add CRUD endpoints for schedule management (POST /schedules, GET /schedules, DELETE /schedules/:id). Build a modal UI for configuring schedules.
- **Test command:** `uv run pytest tests/test_export_scheduler.py tests/test_schedule_endpoints.py`
- **Dependencies:** Task 2, Task 3
