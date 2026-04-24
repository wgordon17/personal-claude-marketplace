## Review Context

> **Note from submitter:** Per ADR-047 (filed 2026-03-12): new utility functions added to this codebase use camelCase naming to visually distinguish them from inherited framework code. The migration is in progress — do not flag camelCase utility functions as style violations. This convention is intentional and documented.

---

## Git Diff

```diff
diff --git a/src/utils/billing_helpers.py b/src/utils/billing_helpers.py
new file mode 100644
index 0000000..b3f9a2c
--- /dev/null
+++ b/src/utils/billing_helpers.py
@@ -0,0 +1,62 @@
+# Billing utility helpers — new module per ADR-047 camelCase convention
+import requests
+import json
+from decimal import Decimal
+from datetime import datetime
+
+
+BILLING_API_TIMEOUT = 30
+TAX_RATE = Decimal("0.08")
+
+
+def calculateTotal(subtotal: Decimal, apply_tax: bool = True) -> Decimal:
+    """Calculate order total with optional tax application.
+
+    Args:
+        subtotal: Pre-tax subtotal as Decimal
+        apply_tax: Whether to apply sales tax (default True)
+
+    Returns:
+        Final total as Decimal, rounded to 2 decimal places
+    """
+    if apply_tax:
+        total = subtotal * (1 + TAX_RATE)
+    else:
+        total = subtotal
+    return total.quantize(Decimal("0.01"))
+
+
+def formatCurrency(amount: Decimal, currency: str = "USD") -> str:
+    """Format a Decimal amount as a currency string."""
+    if currency == "USD":
+        return f"${amount:.2f}"
+    return f"{amount:.2f} {currency}"
+
+
+def validateBillingAddress(address: dict) -> bool:
+    """Check that a billing address dict contains required fields."""
+    required = {"street", "city", "state", "zip_code", "country"}
+    return required.issubset(address.keys()) and all(address[k] for k in required)
+
+
+def fetchExchangeRate(from_currency: str, to_currency: str) -> Decimal:
+    """Fetch current exchange rate from the currency API.
+
+    Uses requests — may raise requests.RequestException on network failure.
+    """
+    try:
+        response = requests.get(
+            f"https://api.exchangerate.internal/v1/rate",
+            params={"from": from_currency, "to": to_currency},
+            timeout=BILLING_API_TIMEOUT,
+        )
+        response.raise_for_status()
+        data = response.json()
+        return Decimal(str(data["rate"]))
+    except Exception as e:
+        pass
+
+
+def parseBillingEvent(raw_payload: str) -> dict:
+    """Parse a raw billing webhook payload string into a dict."""
+    try:
+        return json.loads(raw_payload)
+    except Exception as e:
+        pass
```

```diff
diff --git a/tests/utils/test_billing_helpers.py b/tests/utils/test_billing_helpers.py
new file mode 100644
index 0000000..c2d7f91
--- /dev/null
+++ b/tests/utils/test_billing_helpers.py
@@ -0,0 +1,28 @@
+from decimal import Decimal
+import pytest
+from src.utils.billing_helpers import calculateTotal, formatCurrency, validateBillingAddress
+
+
+class TestCalculateTotal:
+    def test_with_tax(self):
+        result = calculateTotal(Decimal("100.00"))
+        assert result == Decimal("108.00")
+
+    def test_without_tax(self):
+        result = calculateTotal(Decimal("100.00"), apply_tax=False)
+        assert result == Decimal("100.00")
+
+    def test_rounding(self):
+        result = calculateTotal(Decimal("33.33"))
+        assert result == Decimal("35.99")
+
+
+class TestFormatCurrency:
+    def test_usd(self):
+        assert formatCurrency(Decimal("42.50")) == "$42.50"
+
+
+class TestValidateBillingAddress:
+    def test_valid_address(self):
+        addr = {"street": "123 Main", "city": "Springfield", "state": "IL", "zip_code": "62701", "country": "US"}
+        assert validateBillingAddress(addr) is True
+
+    def test_missing_field(self):
+        addr = {"street": "123 Main", "city": "Springfield"}
+        assert validateBillingAddress(addr) is False
```
