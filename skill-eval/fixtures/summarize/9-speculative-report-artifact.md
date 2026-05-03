# Speculative Execution Report: Error Handling Strategy

**Plan:** `hack/plans/feat-error-handling.md`
**Date:** 2026-04-19
**Status:** Winner Selected

---

## Task Description

Implement consistent error handling across all API endpoints. Currently, each module handles errors differently — some raise exceptions, some return error dicts, some silently swallow errors. Two approaches explored in parallel.

---

## Criteria and Weights

| Criterion | Weight | Description |
|-----------|--------|-------------|
| User Experience | 0.4 | Clear, actionable error messages for API consumers |
| Debuggability | 0.3 | Easy to trace errors from user report to root cause |
| Code Simplicity | 0.3 | Minimal boilerplate, clean integration with Flask |

---

## Scoring Matrix

| Criterion | Weight | Approach A (Custom Exceptions) | Approach B (Result Types) |
|-----------|--------|-------------------------------|--------------------------|
| User Experience | 0.4 | 8/10 | 6/10 |
| Debuggability | 0.3 | 7/10 | 9/10 |
| Code Simplicity | 0.3 | 6/10 | 8/10 |
| **Weighted Total** | | **7.1** | **7.5** |

---

## Winner: Approach A (Custom Exceptions)

### Rationale

Custom exceptions provide the best developer experience through clear error hierarchies. Each exception class maps directly to an HTTP status code, making it trivial to add new error types. Flask's `@app.errorhandler` integration is clean and requires no changes to existing route handlers. The structured exception classes carry context (error code, user message, internal details) that automatically translates to consistent API responses.

### Implementation Summary

- `AppError` base exception with `status_code`, `error_code`, `message` fields
- Subclasses: `NotFoundError(404)`, `ValidationError(400)`, `AuthError(401)`, `ForbiddenError(403)`, `ConflictError(409)`
- Global Flask error handler registered via `@app.errorhandler(AppError)`
- 12 route handlers updated to raise exceptions instead of returning error tuples

### Test Results

All 24 tests passing.

---

## Runner-Up: Approach B (Result Types)

### Implementation Summary

- `Result[T]` generic type with `.ok(value)` and `.err(error)` constructors
- All service functions return `Result[T]` instead of raising
- Route handlers call `.unwrap_or_error()` to convert to HTTP responses
- Logging middleware captures all `.err()` results with correlation IDs

### Test Results

All 19 tests passing.

---

## Hybrid Elements Considered

Approach B's correlation ID logging was noted as a valuable feature not present in Approach A. Could be backported as a separate enhancement.

---

## Implementation Applied

Approach A's worktree merged to the feature branch. Approach B's worktree deleted.

---

## Deferred

- Correlation ID logging from Approach B (logged as TODO in hack/plans/feat-error-handling.md)
