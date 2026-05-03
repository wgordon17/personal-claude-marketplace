---
planted_issues:
  - type: n_plus_one_query
    file: src/services/invoice_service.py
    line: 34
    description: Network call per row inside database result loop
expected_findings: 1
---

diff --git a/src/services/invoice_service.py b/src/services/invoice_service.py
index 1a2e8f0..7b3d921 100644
--- a/src/services/invoice_service.py
+++ b/src/services/invoice_service.py
@@ -1,8 +1,10 @@
 from datetime import date
+from typing import Any
 
 from sqlalchemy.orm import Session
 
 from src.clients.tax_api import TaxAPIClient
+from src.clients.shipping_api import ShippingAPIClient
 from src.models.invoice import Invoice
 from src.models.line_item import LineItem
 from src.schemas.invoice import InvoiceSummary
@@ -12,10 +14,12 @@ class InvoiceService:
     """Generates monthly invoice summaries with tax and shipping calculations."""
 
-    def __init__(self, db: Session, tax_client: TaxAPIClient) -> None:
+    def __init__(
+        self, db: Session, tax_client: TaxAPIClient, shipping_client: ShippingAPIClient
+    ) -> None:
         self._db = db
         self._tax_client = tax_client
+        self._shipping_client = shipping_client
 
     def generate_monthly_summary(self, customer_id: int, month: date) -> InvoiceSummary:
         """Build a complete invoice summary for the given customer and month."""
@@ -25,10 +29,18 @@ class InvoiceService:
             .filter(Invoice.invoice_date >= month, Invoice.invoice_date < next_month)
             .all()
         )
-        line_items = []
+        enriched_items: list[dict[str, Any]] = []
         for invoice in invoices:
             for item in invoice.line_items:
-                line_items.append(item)
+                shipping_estimate = self._shipping_client.get_rate(
+                    origin=invoice.warehouse_code,
+                    destination=invoice.shipping_address,
+                    weight_kg=item.weight_kg,
+                )
+                enriched_items.append({
+                    "item": item,
+                    "shipping_cost": shipping_estimate.cost,
+                })
 
         subtotal = sum(item.unit_price * item.quantity for item in
-                       line_items)
+                       [e["item"] for e in enriched_items])
+        total_shipping = sum(e["shipping_cost"] for e in enriched_items)
         tax = self._tax_client.calculate(
             customer_id=customer_id,
             subtotal=subtotal,
@@ -37,5 +49,6 @@ class InvoiceService:
         return InvoiceSummary(
             customer_id=customer_id,
             month=month,
-            line_item_count=len(line_items),
+            line_item_count=len(enriched_items),
             subtotal=subtotal,
+            shipping=total_shipping,
             tax=tax.amount,
-            total=subtotal + tax.amount,
+            total=subtotal + total_shipping + tax.amount,
         )
diff --git a/src/clients/shipping_api.py b/src/clients/shipping_api.py
new file mode 100644
index 0000000..e8f2c43
--- /dev/null
+++ b/src/clients/shipping_api.py
@@ -0,0 +1,28 @@
+from __future__ import annotations
+
+from dataclasses import dataclass
+
+import httpx
+
+
+@dataclass(frozen=True)
+class ShippingRate:
+    carrier: str
+    cost: float
+    estimated_days: int
+
+
+class ShippingAPIClient:
+    """Client for the external shipping rate estimation API."""
+
+    def __init__(self, base_url: str, api_key: str, timeout: float = 5.0) -> None:
+        self._client = httpx.Client(base_url=base_url, timeout=timeout)
+        self._api_key = api_key
+
+    def get_rate(self, origin: str, destination: str, weight_kg: float) -> ShippingRate:
+        resp = self._client.post(
+            "/v1/rates",
+            json={"origin": origin, "destination": destination, "weight_kg": weight_kg},
+            headers={"Authorization": f"Bearer {self._api_key}"},
+        )
+        resp.raise_for_status()
+        data = resp.json()
+        return ShippingRate(carrier=data["carrier"], cost=data["cost"], estimated_days=data["days"])
+
diff --git a/tests/test_invoice_service.py b/tests/test_invoice_service.py
index 3c1e2a0..f7b8d12 100644
--- a/tests/test_invoice_service.py
+++ b/tests/test_invoice_service.py
@@ -1,6 +1,7 @@
 from datetime import date
 from unittest.mock import MagicMock
 
+from src.clients.shipping_api import ShippingAPIClient, ShippingRate
 from src.clients.tax_api import TaxAPIClient
 from src.services.invoice_service import InvoiceService
 
@@ -8,11 +9,16 @@ from src.services.invoice_service import InvoiceService
 def test_monthly_summary_basic():
     db = MagicMock()
     tax_client = MagicMock(spec=TaxAPIClient)
+    shipping_client = MagicMock(spec=ShippingAPIClient)
     tax_client.calculate.return_value = MagicMock(amount=15.0)
+    shipping_client.get_rate.return_value = ShippingRate(
+        carrier="FastShip", cost=5.99, estimated_days=3
+    )
 
     db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
 
-    service = InvoiceService(db=db, tax_client=tax_client)
+    service = InvoiceService(db=db, tax_client=tax_client, shipping_client=shipping_client)
     summary = service.generate_monthly_summary(customer_id=1, month=date(2026, 3, 1))
 
     assert summary.customer_id == 1
+    assert summary.line_item_count == 0
