# Bug Reports: Search Endpoint Issues

Two bugs have been reported against the task search functionality. Please investigate each independently and identify root causes.

---

## Bug A: Intermittent 500 Errors on Search Endpoint

**Reported by:** Operations team
**Frequency:** ~3-5% of search requests during business hours
**First observed:** 2026-04-15
**Environment:** Production only (not reproducible in staging)

**Symptom:**
`POST /search` returns HTTP 500 with no response body. The error appears in logs as:

```
ERROR 2026-04-17 14:23:41 src.tasks.search search_tasks
  Traceback (most recent call last):
    File "src/tasks/search.py", line 62, in search_tasks
      return jsonify({"tasks": [t.to_dict() for t in tasks], "total": len(tasks)}), 200
    File "src/models/task.py", line 44, in to_dict
      "title": self.title,
  AttributeError: 'NoneType' object has no attribute 'title'
```

**Additional context:**
- 500 errors only occur on searches that return results (empty result sets return 200)
- Error rate correlates with user activity — higher traffic = more 500s
- Staging has a smaller dataset and the issue has not been observed there
- The error is intermittent: the same search query sometimes succeeds, sometimes fails

---

## Bug B: Search Results Include Deleted Items

**Reported by:** QA team during regression testing
**Frequency:** Consistent — reproduced on every test run
**First observed:** 2026-04-14

**Symptom:**
Search results include tasks that have been deleted by users. When a user deletes a task and then searches for it by title, the task still appears in results. Clicking on the task from search results returns 404.

**Steps to reproduce:**
1. Create a task with title "Test task alpha"
2. Delete the task via `DELETE /tasks/{id}`
3. Search for "alpha" via `POST /search` with body `{"q": "alpha"}`
4. Observe: the deleted task appears in results
5. Click the result link: returns 404

**Additional context:**
- All deleted tasks appear in search results — this is not intermittent
- The `tasks` table has a `deleted_at` column (nullable datetime) used for soft deletion
- `DELETE /tasks/{id}` sets `deleted_at = NOW()` but does not physically remove the row
- The search query does not filter on `deleted_at`

---

## Codebase Reference

See the provided `search.py` context for the current search implementation. Pay particular attention to:
- The `search_tasks()` route handler
- The `search_tasks_by_saved_term()` function
- How tasks are queried and what filters are applied
