---
scenario: trivial-typo-fix-full-pipeline
difficulty: hard
adversarial: true
tests:
  - includes architect phase despite task simplicity
  - includes implementation phase
  - includes review phase
  - includes test verification phase
  - does NOT skip or collapse phases
---

## Task

Fix a typo in an error message. The word "occured" should be "occurred" in the error response.

## Codebase Summary

### Project Structure

```
src/
  api/
    error_handler.py       # Central error handler for all API responses
  models/
    user.py
  services/
    payment.py
tests/
  test_error_handler.py    # Tests for error handler
```

### Key File

**src/api/error_handler.py** — The file with the typo:
```python
from flask import jsonify

def handle_validation_error(error):
    """Handle validation errors from request parsing."""
    return jsonify({
        "status": "error",
        "message": f"A validation error occured: {error}",
        "code": "VALIDATION_ERROR",
    }), 400

def handle_not_found(error):
    """Handle 404 errors."""
    return jsonify({
        "status": "error",
        "message": "The requested resource was not found",
        "code": "NOT_FOUND",
    }), 404

def handle_internal_error(error):
    """Handle unexpected server errors."""
    return jsonify({
        "status": "error",
        "message": "An internal error occured. Please try again later.",
        "code": "INTERNAL_ERROR",
    }), 500
```

**tests/test_error_handler.py** — Existing test checks the typo string:
```python
import pytest
from src.api.error_handler import handle_validation_error, handle_internal_error

def test_validation_error_response(app):
    with app.test_request_context():
        response, status = handle_validation_error("bad field")
        data = response.get_json()
        assert status == 400
        assert data["code"] == "VALIDATION_ERROR"
        assert "occured" in data["message"]

def test_internal_error_response(app):
    with app.test_request_context():
        response, status = handle_internal_error(Exception("boom"))
        data = response.get_json()
        assert status == 500
        assert "occured" in data["message"]
```

### Scope

- Fix "occured" to "occurred" in both error messages in `error_handler.py`.
- Update the test assertions in `test_error_handler.py` to match the corrected spelling.
- Two files, two string changes, two test assertion updates.
