---
planted_issues:
  - type: missing_input_validation
    file: src/services/processor.py
    line: 15
  - type: refactor_deep_nesting
    file: src/services/processor.py
    line: 12
conflict: true
expected_findings: 2
---

## CODE REVIEW Findings

The following 2 findings were identified by domain reviewers and verified. Both target the same function.

### CORRECTNESS

**Finding pr-corr-1:** Missing input validation allows invalid data through processing pipeline
**Location:** `src/services/processor.py:15`
**Evidence:** The `process_order` function accepts raw order data without validating required fields or checking value constraints. Negative amounts and missing customer IDs reach the payment gateway.

```python
# src/services/processor.py
import logging
from dataclasses import dataclass
from typing import Any

from payments import charge_customer
from notifications import send_confirmation
from inventory import reserve_items

logger = logging.getLogger(__name__)


def process_order(order_data: dict[str, Any]) -> dict[str, Any]:
    customer_id = order_data.get("customer_id")
    items = order_data.get("items", [])
    amount = order_data.get("amount", 0)
    currency = order_data.get("currency", "usd")
    shipping_address = order_data.get("shipping_address")

    reservation = reserve_items(items)
    if reservation.success:
        charge_result = charge_customer(customer_id, amount, currency)
        if charge_result.success:
            send_confirmation(customer_id, items, shipping_address)
            return {
                "status": "completed",
                "order_id": charge_result.order_id,
                "reservation_id": reservation.id,
                "charged_amount": amount,
            }
        else:
            reservation.cancel()
            logger.warning("Payment failed for customer %s", customer_id)
            return {"status": "failed", "reason": "payment_declined"}
    else:
        logger.warning("Reservation failed for customer %s", customer_id)
        return {"status": "failed", "reason": "inventory_unavailable"}


def get_order_status(order_id: str) -> dict[str, Any]:
    from db import get_order
    order = get_order(order_id)
    if order is None:
        return {"status": "not_found"}
    return {"status": order.status, "order_id": order.id}
```

---

### ARCHITECTURE

**Finding pr-arch-1:** Deep nesting in process_order should use early returns for readability
**Location:** `src/services/processor.py:12`
**Evidence:** The function uses nested if-else blocks (reservation check wraps the charge check which wraps the success path) that increase cognitive complexity. Refactoring to early returns for failure cases would flatten the control flow and improve readability.

```python
# src/services/processor.py (same function as above)
def process_order(order_data: dict[str, Any]) -> dict[str, Any]:
    customer_id = order_data.get("customer_id")
    items = order_data.get("items", [])
    amount = order_data.get("amount", 0)
    currency = order_data.get("currency", "usd")
    shipping_address = order_data.get("shipping_address")

    reservation = reserve_items(items)
    if reservation.success:
        charge_result = charge_customer(customer_id, amount, currency)
        if charge_result.success:
            send_confirmation(customer_id, items, shipping_address)
            return {
                "status": "completed",
                "order_id": charge_result.order_id,
                "reservation_id": reservation.id,
                "charged_amount": amount,
            }
        else:
            reservation.cancel()
            logger.warning("Payment failed for customer %s", customer_id)
            return {"status": "failed", "reason": "payment_declined"}
    else:
        logger.warning("Reservation failed for customer %s", customer_id)
        return {"status": "failed", "reason": "inventory_unavailable"}
```
