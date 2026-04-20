---
scenario: hybrid-opportunity-error-handling
difficulty: hard
notes:
  - "Each approach excels where the other is weak"
  - "Custom exceptions have great UX but verbose code"
  - "Result types have simple code but worse UX"
  - "Hybrid combining both at different layers is the ideal answer"
---

## Problem

Improve error handling across the API layer. Currently, errors are handled inconsistently: some endpoints return raw exception messages, others return generic 500s, and none provide structured error responses. The goal is a unified error handling strategy that gives users actionable error messages while providing developers with debugging context.

### Existing Codebase

```
src/
  api/
    orders.py              # Order CRUD endpoints (inconsistent error handling)
    payments.py            # Payment processing endpoints (bare try/except)
    inventory.py           # Stock management endpoints (no error handling)
  services/
    order_service.py       # Business logic with mixed raise/return patterns
    payment_service.py     # External payment gateway integration
    inventory_service.py   # Stock operations with race conditions
  models/
    order.py               # Order model
    payment.py             # Payment model
  middleware/
    error_handler.py       # Flask errorhandler — catches Exception, returns 500
tests/
  test_orders.py           # 10 tests, none test error responses
  test_payments.py         # 5 tests, 2 test happy path only
```

### Current Problems

```python
# src/api/orders.py — raw exception leaks
@orders_bp.route("/<int:order_id>", methods=["GET"])
def get_order(order_id):
    try:
        order = order_service.get_order(order_id)
        return jsonify(order.to_dict())
    except Exception as e:
        return jsonify({"error": str(e)}), 500  # Leaks internal details

# src/api/payments.py — swallows errors
@payments_bp.route("/charge", methods=["POST"])
def charge():
    try:
        result = payment_service.charge(request.json)
        return jsonify(result)
    except:
        return jsonify({"error": "Something went wrong"}), 500  # No context

# src/api/inventory.py — no handling at all
@inventory_bp.route("/stock/<int:product_id>", methods=["PUT"])
def update_stock(product_id):
    new_qty = request.json["quantity"]
    inventory_service.set_stock(product_id, new_qty)  # Unhandled exceptions
    return jsonify({"status": "ok"})
```

### Success Criteria

| Criterion | Weight | Description |
|-----------|--------|-------------|
| User Experience | 0.4 | Structured, actionable error messages for API consumers |
| Debuggability | 0.3 | Rich error context for developers: error codes, request IDs, stack traces in logs |
| Code Simplicity | 0.3 | Minimal boilerplate per endpoint, easy to add new error types |

---

## Competitor A: Custom Exception Hierarchy

### ImplementationResult

```json
{
  "schema": "ImplementationResult",
  "competitor_id": "competitor-1",
  "status": "complete",
  "approach": "Designed a custom exception hierarchy rooted at AppError with subclasses for each error domain: NotFoundError, ValidationError, PaymentError, InventoryError, AuthorizationError. Each exception carries a machine-readable error_code, human-readable message, HTTP status, and optional details dict. A Flask errorhandler catches AppError and formats structured JSON responses. Service layer raises specific exceptions; API layer is clean (no try/except needed).",
  "files_created": [
    "src/errors/__init__.py",
    "src/errors/base.py",
    "src/errors/domain.py",
    "src/errors/handler.py",
    "src/errors/codes.py",
    "tests/test_error_handling.py",
    "tests/test_error_responses.py"
  ],
  "files_modified": [
    "src/api/orders.py",
    "src/api/payments.py",
    "src/api/inventory.py",
    "src/services/order_service.py",
    "src/services/payment_service.py",
    "src/services/inventory_service.py",
    "src/middleware/error_handler.py"
  ],
  "test_results": {
    "tests_run": 32,
    "tests_passed": 32,
    "tests_failed": 0,
    "command": "uv run pytest tests/ -v"
  },
  "self_assessment": [
    {
      "criterion": "User Experience",
      "score": 9,
      "rationale": "Every error response has a consistent structure: {error_code, message, details, request_id}. Error codes are machine-readable (ORDER_NOT_FOUND, PAYMENT_DECLINED). Messages are user-facing and actionable ('Order 42 not found' instead of 'NoneType has no attribute id'). HTTP status codes match semantics (404 for not found, 422 for validation, 502 for gateway errors)."
    },
    {
      "criterion": "Debuggability",
      "score": 8,
      "rationale": "Request IDs attached to every error response and logged with full stack trace. Error codes allow grep-based log analysis. PaymentError includes gateway response details. However, structured logging (JSON logs) is not implemented — stack traces are plain text in the log."
    },
    {
      "criterion": "Code Simplicity",
      "score": 5,
      "rationale": "API endpoints are clean (no try/except needed — just call service and let exceptions propagate). But the exception hierarchy is 5 files with 8 exception classes. Adding a new error type requires: (1) add class to domain.py, (2) add error code to codes.py, (3) optionally add handler specialization. The exception hierarchy is a form of up-front design that may be more structure than needed."
    }
  ],
  "trade_offs": "Exception hierarchy adds structural complexity — 5 new files, 8 exception classes, a codes registry. Risk of exception class proliferation over time (every new domain adds exceptions). Error handler is a global catch-all that may mask unexpected errors if AppError is too broad. Custom exceptions are a Python-specific pattern that may not translate well if the team adopts a polyglot approach.",
  "failure_reason": null,
  "turn_count": 24
}
```

### Code Diff (selected)

```diff
diff --git a/src/errors/base.py b/src/errors/base.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/src/errors/base.py
@@ -0,0 +1,28 @@
+class AppError(Exception):
+    """Base application error with structured response data."""
+
+    def __init__(
+        self,
+        message: str,
+        error_code: str,
+        status_code: int = 500,
+        details: dict | None = None,
+    ):
+        super().__init__(message)
+        self.message = message
+        self.error_code = error_code
+        self.status_code = status_code
+        self.details = details or {}
+
+    def to_response(self, request_id: str) -> dict:
+        return {
+            "error_code": self.error_code,
+            "message": self.message,
+            "details": self.details,
+            "request_id": request_id,
+        }
diff --git a/src/errors/domain.py b/src/errors/domain.py
new file mode 100644
index 0000000..b2c3d4e
--- /dev/null
+++ b/src/errors/domain.py
@@ -0,0 +1,32 @@
+from src.errors.base import AppError
+
+
+class NotFoundError(AppError):
+    def __init__(self, resource: str, identifier):
+        super().__init__(
+            message=f"{resource} {identifier} not found",
+            error_code=f"{resource.upper()}_NOT_FOUND",
+            status_code=404,
+        )
+
+
+class ValidationError(AppError):
+    def __init__(self, field: str, reason: str):
+        super().__init__(
+            message=f"Validation failed for {field}: {reason}",
+            error_code="VALIDATION_ERROR",
+            status_code=422,
+            details={"field": field, "reason": reason},
+        )
+
+
+class PaymentError(AppError):
+    def __init__(self, message: str, gateway_response: dict | None = None):
+        super().__init__(
+            message=message,
+            error_code="PAYMENT_DECLINED",
+            status_code=502,
+            details={"gateway": gateway_response} if gateway_response else {},
+        )
```

```diff
diff --git a/src/api/orders.py b/src/api/orders.py
index 3a1b2c3..7d8e9f0 100644
--- a/src/api/orders.py
+++ b/src/api/orders.py
@@ -4,10 +4,6 @@ from src.services import order_service

 @orders_bp.route("/<int:order_id>", methods=["GET"])
 def get_order(order_id):
-    try:
-        order = order_service.get_order(order_id)
-        return jsonify(order.to_dict())
-    except Exception as e:
-        return jsonify({"error": str(e)}), 500
+    order = order_service.get_order(order_id)
+    return jsonify(order.to_dict())
```

---

## Competitor B: Result Types (Success/Failure Pattern)

### ImplementationResult

```json
{
  "schema": "ImplementationResult",
  "competitor_id": "competitor-2",
  "status": "complete",
  "approach": "Introduced a Result type (dataclass with is_ok, value, error fields) that all service methods return instead of raising exceptions. API endpoints check result.is_ok and return appropriate HTTP responses. Error details are carried in the Result.error field as a structured ErrorInfo dataclass. No exceptions used for control flow — all error paths are explicit return values.",
  "files_created": [
    "src/types/result.py",
    "tests/test_result_type.py"
  ],
  "files_modified": [
    "src/api/orders.py",
    "src/api/payments.py",
    "src/api/inventory.py",
    "src/services/order_service.py",
    "src/services/payment_service.py",
    "src/services/inventory_service.py"
  ],
  "test_results": {
    "tests_run": 24,
    "tests_passed": 24,
    "tests_failed": 0,
    "command": "uv run pytest tests/ -v"
  },
  "self_assessment": [
    {
      "criterion": "User Experience",
      "score": 6,
      "rationale": "Error responses include error_code and message from ErrorInfo, but the API layer must manually construct the response dict for each endpoint. Inconsistency risk: each endpoint formats errors slightly differently unless developers remember the pattern. No request_id in error responses."
    },
    {
      "criterion": "Debuggability",
      "score": 7,
      "rationale": "Result type makes error paths explicit — impossible to accidentally swallow an error because the caller must check is_ok. Error chain is traceable through return values. However, no stack traces are captured (result types replace exceptions, losing traceback context). Log correlation requires manual instrumentation."
    },
    {
      "criterion": "Code Simplicity",
      "score": 8,
      "rationale": "Result type is a single file with 2 dataclasses (Result, ErrorInfo) totaling 35 lines. No hierarchy to maintain. Adding a new error type means passing a different error_code string — no new class needed. Each endpoint has a 3-line pattern: call service, check is_ok, return response."
    }
  ],
  "trade_offs": "Every API endpoint needs explicit error checking (if not result.is_ok: return ...), adding 3 lines per endpoint. No automatic propagation — errors must be manually forwarded through call chains. Stack traces are lost since errors are return values, not exceptions. Harder to add cross-cutting concerns (like request_id injection) without modifying every endpoint.",
  "failure_reason": null,
  "turn_count": 18
}
```

### Code Diff (selected)

```diff
diff --git a/src/types/result.py b/src/types/result.py
new file mode 100644
index 0000000..c3d4e5f
--- /dev/null
+++ b/src/types/result.py
@@ -0,0 +1,35 @@
+from __future__ import annotations
+
+from dataclasses import dataclass, field
+
+
+@dataclass(frozen=True)
+class ErrorInfo:
+    code: str
+    message: str
+    status_code: int = 500
+    details: dict = field(default_factory=dict)
+
+
+@dataclass(frozen=True)
+class Result:
+    is_ok: bool
+    value: object = None
+    error: ErrorInfo | None = None
+
+    @classmethod
+    def ok(cls, value: object) -> Result:
+        return cls(is_ok=True, value=value)
+
+    @classmethod
+    def fail(cls, code: str, message: str, status_code: int = 500, **details) -> Result:
+        return cls(
+            is_ok=False,
+            error=ErrorInfo(code=code, message=message, status_code=status_code, details=details),
+        )
```

```diff
diff --git a/src/api/orders.py b/src/api/orders.py
index 3a1b2c3..8e9f0a1 100644
--- a/src/api/orders.py
+++ b/src/api/orders.py
@@ -4,10 +4,11 @@ from src.services import order_service

 @orders_bp.route("/<int:order_id>", methods=["GET"])
 def get_order(order_id):
-    try:
-        order = order_service.get_order(order_id)
-        return jsonify(order.to_dict())
-    except Exception as e:
-        return jsonify({"error": str(e)}), 500
+    result = order_service.get_order(order_id)
+    if not result.is_ok:
+        return jsonify({
+            "error_code": result.error.code,
+            "message": result.error.message,
+        }), result.error.status_code
+    return jsonify(result.value.to_dict())
diff --git a/src/services/order_service.py b/src/services/order_service.py
index 4d5e6f7..a0b1c2d 100644
--- a/src/services/order_service.py
+++ b/src/services/order_service.py
@@ -1,8 +1,12 @@
 from src.models.order import Order
 from src.db import db
+from src.types.result import Result


-def get_order(order_id: int) -> Order:
+def get_order(order_id: int) -> Result:
     order = db.session.get(Order, order_id)
     if order is None:
-        raise ValueError(f"Order {order_id} not found")
-    return order
+        return Result.fail(
+            "ORDER_NOT_FOUND", f"Order {order_id} not found", status_code=404
+        )
+    return Result.ok(order)
```
