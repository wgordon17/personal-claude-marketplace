---
planted_issues:
  - type: sql_injection
    file: src/api/search.py
    line: 31
    valid: true
  - type: false_positive_null_check
    file: src/models/account.py
    line: 18
    valid: false
  - type: missing_test
    file: src/services/billing.py
    line: 45
    valid: true
  - type: false_positive_memory_leak
    file: src/services/export.py
    line: 22
    valid: false
expected_findings: 4
expected_valid: 2
expected_invalid: 2
---

## CODE REVIEW Findings

The following 4 findings were identified by domain reviewers and verified.

### SECURITY

**Finding pr-sec-1:** SQL injection in search endpoint
**Location:** `src/api/search.py:31`
**Evidence:** The `search_products` function interpolates user input directly into a SQL query string without parameterization.

```python
# src/api/search.py
import sqlite3
from flask import Flask, request, jsonify

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect("products.db")
    conn.row_factory = sqlite3.Row
    return conn


def sanitize_sort_field(field: str) -> str:
    allowed = {"name", "price", "created_at", "rating"}
    return field if field in allowed else "name"


@app.route("/api/products/search")
def search_products():
    query_term = request.args.get("q", "")
    category = request.args.get("category", "")
    sort_by = sanitize_sort_field(request.args.get("sort", "name"))
    limit = min(int(request.args.get("limit", "20")), 100)

    conn = get_db()
    cursor = conn.cursor()

    sql = f"SELECT id, name, price, category FROM products WHERE name LIKE '%{query_term}%'"
    if category:
        sql += f" AND category = '{category}'"
    sql += f" ORDER BY {sort_by} LIMIT {limit}"

    cursor.execute(sql)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(results)
```

---

### CORRECTNESS

**Finding pr-corr-1:** Missing null check on account balance field
**Location:** `src/models/account.py:18`
**Evidence:** The `get_balance` method accesses `self.balance` without checking for None, which could raise a TypeError during arithmetic operations.

```python
# src/models/account.py
from sqlalchemy import Column, Integer, String, Numeric, ForeignKey
from sqlalchemy.orm import relationship, DeclarativeBase


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)
    balance = Column(Numeric(10, 2), nullable=False, default=0)
    currency = Column(String(3), nullable=False, default="USD")

    owner = relationship("User", back_populates="accounts")
    transactions = relationship("Transaction", back_populates="account")

    def get_balance(self) -> float:
        return float(self.balance)

    def deposit(self, amount: float) -> None:
        if amount <= 0:
            raise ValueError("Deposit amount must be positive")
        self.balance += amount

    def withdraw(self, amount: float) -> None:
        if amount <= 0:
            raise ValueError("Withdrawal amount must be positive")
        if amount > self.balance:
            raise ValueError("Insufficient funds")
        self.balance -= amount
```

---

### TESTING GAPS

**Finding pr-test-1:** No tests for billing discount calculation
**Location:** `src/services/billing.py:45`
**Evidence:** The `calculate_discount` function contains branching logic with 4 discount tiers but has no corresponding test file. Edge cases around tier boundaries (exactly 100, exactly 500) are untested.

```python
# src/services/billing.py
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class LineItem:
    product_id: str
    quantity: int
    unit_price: Decimal


@dataclass
class Invoice:
    customer_id: str
    items: list[LineItem]
    discount_pct: Decimal = Decimal("0")

    @property
    def subtotal(self) -> Decimal:
        return sum(item.quantity * item.unit_price for item in self.items)

    @property
    def total(self) -> Decimal:
        discount = self.subtotal * (self.discount_pct / Decimal("100"))
        return self.subtotal - discount


def calculate_discount(subtotal: Decimal, is_member: bool) -> Decimal:
    if is_member:
        if subtotal >= Decimal("1000"):
            return Decimal("20")
        elif subtotal >= Decimal("500"):
            return Decimal("15")
        elif subtotal >= Decimal("100"):
            return Decimal("10")
        else:
            return Decimal("5")
    else:
        if subtotal >= Decimal("1000"):
            return Decimal("10")
        elif subtotal >= Decimal("500"):
            return Decimal("5")
        else:
            return Decimal("0")


def create_invoice(customer_id: str, items: list[LineItem], is_member: bool) -> Invoice:
    line_items = [LineItem(**item) if isinstance(item, dict) else item for item in items]
    invoice = Invoice(customer_id=customer_id, items=line_items)
    invoice.discount_pct = calculate_discount(invoice.subtotal, is_member)
    return invoice
```

---

### PERFORMANCE

**Finding pr-perf-1:** Potential memory leak in export service due to unclosed file handles
**Location:** `src/services/export.py:22`
**Evidence:** The `export_report` function opens file handles that may not be properly closed if an exception occurs during writing.

```python
# src/services/export.py
import csv
import json
from pathlib import Path
from contextlib import contextmanager
from typing import Any


@contextmanager
def atomic_write(filepath: Path):
    tmp_path = filepath.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", newline="", encoding="utf-8") as f:
            yield f
        tmp_path.rename(filepath)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def export_report(data: list[dict[str, Any]], output_path: Path, fmt: str = "csv") -> Path:
    with atomic_write(output_path) as f:
        if fmt == "csv":
            if not data:
                return output_path
            writer = csv.DictWriter(f, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        elif fmt == "json":
            json.dump(data, f, indent=2, default=str)
        else:
            raise ValueError(f"Unsupported format: {fmt}")
    return output_path


def export_batch(reports: dict[str, list[dict]], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, data in reports.items():
        path = output_dir / f"{name}.csv"
        paths.append(export_report(data, path))
    return paths
```
