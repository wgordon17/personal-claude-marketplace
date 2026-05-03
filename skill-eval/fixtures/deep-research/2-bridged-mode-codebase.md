---
# Fixture metadata (stripped by loader)
scenario: "Bridged mode — internal codebase with inconsistent error handling"
notes:
  - "Three distinct error handling patterns coexist in the same project"
  - "Goal: verify skill analyzes internal patterns FIRST, then bridges to external best practices"
  - "Expects consolidation recommendation grounded in the actual code"
  - "Simulated internal investigation results and external search results"
---

## Research Question

How should we improve our error handling? Mode: Bridged

## Project Context

- **Language:** Python 3.12
- **Framework:** FastAPI + SQLAlchemy
- **Project size:** ~40 modules, 15K LOC
- **Team:** 3 developers, 6 months into the project
- **Pain points:** Inconsistent error responses, difficult debugging, errors swallowed silently in some modules

## Internal Investigation Results

### File: src/api/users.py (Pattern A: Custom Exception Classes)

```python
class UserNotFoundError(AppError):
    """Raised when a user lookup fails."""
    status_code = 404
    error_code = "USER_NOT_FOUND"

class DuplicateEmailError(AppError):
    """Raised when registration uses an existing email."""
    status_code = 409
    error_code = "DUPLICATE_EMAIL"

@router.post("/users")
async def create_user(payload: CreateUserRequest):
    if await user_repo.email_exists(payload.email):
        raise DuplicateEmailError(f"Email {payload.email} already registered")
    user = await user_repo.create(payload)
    return UserResponse.from_orm(user)

@router.get("/users/{user_id}")
async def get_user(user_id: int):
    user = await user_repo.get(user_id)
    if user is None:
        raise UserNotFoundError(f"User {user_id} not found")
    return UserResponse.from_orm(user)
```

### File: src/api/orders.py (Pattern B: Result Types)

```python
from dataclasses import dataclass
from typing import TypeVar, Generic

T = TypeVar("T")

@dataclass
class Success(Generic[T]):
    value: T

@dataclass
class Failure:
    error_code: str
    message: str
    status_code: int = 400

Result = Success | Failure

@router.post("/orders")
async def create_order(payload: CreateOrderRequest):
    result = await order_service.place_order(payload)
    match result:
        case Success(value=order):
            return OrderResponse.from_orm(order)
        case Failure(error_code=code, message=msg, status_code=status):
            return JSONResponse(
                status_code=status,
                content={"error_code": code, "message": msg},
            )
```

### File: src/api/payments.py (Pattern C: Bare try/except)

```python
@router.post("/payments/charge")
async def charge_payment(payload: ChargeRequest):
    try:
        gateway_response = await payment_gateway.charge(
            amount=payload.amount,
            token=payload.payment_token,
        )
        return {"transaction_id": gateway_response.id, "status": "charged"}
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "Payment processing failed"},
        )

@router.post("/payments/refund")
async def refund_payment(payload: RefundRequest):
    try:
        result = await payment_gateway.refund(payload.transaction_id)
        return {"status": "refunded", "refund_id": result.id}
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "Refund processing failed"},
        )
```

### File: src/middleware/error_handler.py

```python
from fastapi import Request
from fastapi.responses import JSONResponse

async def global_error_handler(request: Request, exc: Exception):
    """Catch-all error handler registered via app.add_exception_handler."""
    if isinstance(exc, AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": str(exc)},
        )
    # Bare Exception falls through here
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )
```

### File: hack/PROJECT.md (relevant excerpt)

```markdown
## Error Handling
- Started with custom exceptions (Pattern A) in the users module
- Orders team adopted Result types after reading railway-oriented programming article
- Payments module was a quick prototype — uses bare try/except
- No team consensus on which pattern to standardize on
- Global error handler only catches AppError subclasses — Result failures and bare except blocks bypass it
```

## Simulated External Search Results

### Source 1: "Error Handling in Python Web APIs — 2025 Survey" (blog post)

Survey of 200 Python API projects on GitHub. Findings:
- 62% use custom exception hierarchies with framework middleware
- 23% use Result/Either types (growing trend from functional programming)
- 15% use bare try/except (declining, considered anti-pattern)
- Projects using custom exceptions reported 40% fewer unhandled errors in production

### Source 2: FastAPI Documentation — Exception Handling (official docs)

FastAPI recommends HTTPException for simple cases and custom exception handlers via `app.add_exception_handler()`. The framework natively supports:
- `HTTPException` with `status_code` and `detail`
- Custom exception classes with registered handlers
- `RequestValidationError` for input validation
- Starlette's exception handling middleware

### Source 3: "Result Types in Python — When and Why" (PyCon 2025 talk transcript)

Speaker argues Result types are superior for service-layer code where errors are expected outcomes (not exceptional conditions). However, recommends against using Result types at the API boundary — convert to HTTP responses in the router layer. Key quote: "Use exceptions for exceptional conditions, Result types for expected failures in business logic."

### Source 4: "Python Error Handling Anti-Patterns" (Real Python, 2025)

Lists bare `except Exception` as the #1 anti-pattern. Specific problems:
- Swallows stack traces, making debugging impossible
- Hides the actual error type from monitoring
- Cannot distinguish retryable from permanent failures
- Violates the principle of least surprise

### Source 5: FastAPI Community Discussion — "Standardizing Error Responses" (GitHub, 2025-06)

Community consensus: use a single base exception class with structured error responses. RFC 7807 (Problem Details for HTTP APIs) gaining adoption. Example pattern: all errors include `type`, `title`, `status`, `detail`, and optional `instance`.
