---
clean_patterns:
  - "All file references match File Structure"
  - "Dependencies form a DAG with no cycles"
  - "No unresolved assumptions"
expected_outcome: "No significant structural issues"
---

# Implementation Plan: Add Project Archive Feature

**Goal:** Allow project owners to archive/unarchive projects.
**Branch:** feat/project-archive

## File Structure
| File | Responsibility |
|------|----------------|
| src/models/project.py | Add archived flag and archive/unarchive methods |
| src/api/projects.py | Add archive/unarchive endpoints |
| tests/test_projects.py | Add archive feature tests |

## Tasks

### Task 1: Add archive field to project model
**Files:** src/models/project.py
**Depends on:** None
- [ ] Add `archived: bool` field with default False
- [ ] Add `archive_project()` and `unarchive_project()` functions
- [ ] Filter archived projects from default listing

### Task 2: Add archive/unarchive API endpoints
**Files:** src/api/projects.py
**Depends on:** Task 1
- [ ] POST /projects/<id>/archive — owner-only
- [ ] POST /projects/<id>/unarchive — owner-only
- [ ] Include archived status in project responses

### Task 3: Add tests for archive feature
**Files:** tests/test_projects.py
**Depends on:** Task 2
- [ ] Test archive sets archived=True
- [ ] Test unarchive sets archived=False
- [ ] Test non-owner cannot archive
- [ ] Test archived projects excluded from default listing
