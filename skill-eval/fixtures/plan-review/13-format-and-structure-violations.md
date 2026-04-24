# Plan: Add Search Feature

Goal: Add full-text search to the task management API.

## Tasks

### Search endpoint
Add GET /api/search?q=term endpoint. Use PostgreSQL full-text search with tsvector. Add GIN index for performance.
Files: src/api/search.py, src/models/task.py

### Search index migration
Dependencies: none
Create migration to add search_vector column and GIN index to tasks table. Populate existing rows using a data migration.
Files: db/migrations/0045_search.py

### Update task model
Dependencies: Search index migration
Add search_vector column to Task model. Add trigger to update search_vector on insert/update.
Files: src/models/task.py

### Tests
Dependencies: Search endpoint, Update task model
Add tests for search endpoint covering: basic search, empty query, no results, pagination, special characters.
Files: tests/test_search.py

## Notes
- The search endpoint task has no Dependencies field
- File Structure section is missing entirely
- Task ordering is wrong: "Search endpoint" has no dependencies but references search_vector which doesn't exist until after "Update task model"
- "Update task model" and "Search endpoint" both modify src/models/task.py but neither declares the contention
- No task IDs or numbering — tasks referenced by name only
