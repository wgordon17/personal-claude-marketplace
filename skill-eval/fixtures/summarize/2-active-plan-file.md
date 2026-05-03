---
# Fixture metadata (stripped by loader)
planted_issues:
  - partial_completion: "3 of 6 tasks are checked complete, 3 remain unchecked or partial"
  - task_4_partial: "Task 4 has 2 of 4 substeps checked, making it partially done"
  - tasks_5_6_untouched: "Tasks 5 and 6 are fully unchecked with no progress"
  - review_cycle_metadata: "Plan has been through 2 review cycles — should be referenced"
clean_distractors:
  - "Tasks 1-3 are genuinely complete with all substeps checked"
  - "Goal and architecture sections are well-defined"
  - "File structure is consistent with task definitions"
---

# Plan: Add User Export System

**Goal:** Implement a user data export system that allows administrators to export user data
in CSV and JSON formats, with filtering, scheduling, and audit logging.

**Branch:** feat/user-export
**Created:** 2026-04-12
**Iterations:** review-cycle: 2

## Architecture Summary

The export system consists of three layers: an API layer (Flask blueprints), a processing
layer (background workers via Celery), and a storage layer (S3-compatible object storage).
Exports are tracked in a PostgreSQL `export_jobs` table with status transitions:
PENDING -> PROCESSING -> COMPLETED | FAILED.

## File Structure

| File | Purpose |
|------|---------|
| src/api/exports.py | Export request endpoints |
| src/workers/export_worker.py | Celery task for async export processing |
| src/exporters/csv_exporter.py | CSV format serializer |
| src/exporters/json_exporter.py | JSON format serializer |
| src/storage/s3_client.py | S3 upload/download client |
| src/models/export_job.py | SQLAlchemy model for export_jobs table |
| src/api/export_schedule.py | Scheduled export CRUD endpoints |
| src/workers/schedule_worker.py | Celery beat task for scheduled exports |
| src/audit/export_audit.py | Audit log writer for export events |
| tests/test_exports.py | Unit tests for export endpoints |
| tests/test_exporters.py | Unit tests for format serializers |
| tests/test_export_worker.py | Unit tests for async worker |

## Tasks

### Task 1: Create export job model and migration
- [x] Define ExportJob SQLAlchemy model with status enum
- [x] Create Alembic migration for export_jobs table
- [x] Add index on (user_id, status) for query performance

### Task 2: Implement CSV exporter
- [x] Create CsvExporter class with configurable column mapping
- [x] Add header row generation from model field names
- [x] Implement streaming write for large datasets (>10k rows)
- [x] Add unit tests for edge cases (empty dataset, unicode fields)

### Task 3: Implement JSON exporter
- [x] Create JsonExporter class with configurable field selection
- [x] Add pagination support for large exports
- [x] Implement streaming JSON array output
- [x] Add unit tests for nested object serialization

### Task 4: Create export API endpoints
- [x] POST /api/exports — create new export job
- [x] GET /api/exports/{id} — check export job status
- [ ] GET /api/exports/{id}/download — download completed export file
- [ ] DELETE /api/exports/{id} — cancel pending export or delete completed

### Task 5: Implement async export worker
- [ ] Create Celery task for export processing
- [ ] Add progress tracking with percentage updates
- [ ] Implement retry logic with exponential backoff
- [ ] Add dead letter queue for permanently failed exports

### Task 6: Add audit logging
- [ ] Create audit log schema for export events
- [ ] Log export creation, completion, failure, and download events
- [ ] Add admin endpoint to query audit trail with date filtering
- [ ] Implement log rotation policy (90-day retention)

## Open Questions

1. Should we support XLSX format in addition to CSV and JSON? (Deferred to v2)
2. What is the maximum export size before we require chunked downloads?
