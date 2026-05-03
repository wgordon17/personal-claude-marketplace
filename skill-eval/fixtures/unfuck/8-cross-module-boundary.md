# Codebase: E-Commerce Order Processing System

A self-contained order processing backend. 3 modules (api/, services/, models/), 13 files total. All files defined inline.

---

## models/user.py

```python
import hashlib
import hmac
import os


class User:
    def __init__(self, user_id, email, name):
        self.user_id = user_id
        self.email = email
        self.name = name
        self._payment_methods = []

    def add_payment_method(self, card_number, expiry, cvv):
        hashed = _hash_card(card_number)
        self._payment_methods.append({
            "hash": hashed,
            "expiry": expiry,
            "last4": card_number[-4:],
        })

    def get_payment_methods(self):
        return [
            {"last4": pm["last4"], "expiry": pm["expiry"]}
            for pm in self._payment_methods
        ]

    def has_payment_method(self, card_number):
        return _hash_card(card_number) in {pm["hash"] for pm in self._payment_methods}

    def validate_card(self, card_number):
        """Check if a given card is on file for this user."""
        return self.has_payment_method(card_number)


def _hash_card(card_number):
    """Hash a card number for storage comparison."""
    salt = os.environ.get("CARD_HASH_SALT", "default-salt")
    key = salt.encode()
    msg = card_number.strip().encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def get_user_by_id(user_id, db):
    """Fetch a user from the database by ID."""
    row = db.execute("SELECT user_id, email, name FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return User(row["user_id"], row["email"], row["name"])


def deactivate_user(user_id, db):
    """Soft-delete a user account."""
    db.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,))
    db.commit()
```

---

## models/product.py

```python
import logging

logger = logging.getLogger(__name__)


class Product:
    def __init__(self, product_id, name, price, stock_count):
        self.product_id = product_id
        self.name = name
        self.price = price
        self.stock_count = stock_count

    def is_in_stock(self, quantity=1):
        return self.stock_count >= quantity

    def reserve(self, quantity):
        if not self.is_in_stock(quantity):
            raise ValueError(f"Insufficient stock for {self.name}")
        self.stock_count -= quantity
        logger.info("Reserved %d units of product %s", quantity, self.product_id)

    def to_dict(self):
        return {
            "product_id": self.product_id,
            "name": self.name,
            "price": self.price,
            "stock_count": self.stock_count,
        }


def get_product_by_id(product_id, db):
    """Fetch a product from the database."""
    row = db.execute(
        "SELECT product_id, name, price, stock_count FROM products WHERE product_id = ?",
        (product_id,)
    ).fetchone()
    if row is None:
        return None
    return Product(row["product_id"], row["name"], row["price"], row["stock_count"])


# Dead code: was used during initial import script, no longer called
def _legacy_import_product(csv_row):
    """Import a product from a CSV row. Used during initial data import (2023)."""
    parts = csv_row.split(",")
    return {
        "product_id": parts[0].strip(),
        "name": parts[1].strip(),
        "price": float(parts[2].strip()),
        "stock_count": int(parts[3].strip()),
    }
```

---

## models/order.py

```python
import logging
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

ORDER_STATUSES = ["pending", "confirmed", "shipped", "delivered", "cancelled", "refunded"]


class Order:
    def __init__(self, order_id, user_id, items, status="pending"):
        self.order_id = order_id
        self.user_id = user_id
        self.items = items  # list of {"product_id": ..., "quantity": ..., "unit_price": ...}
        self.status = status
        self.created_at = datetime.utcnow()

    def subtotal(self):
        return sum(item["unit_price"] * item["quantity"] for item in self.items)

    def item_count(self):
        return sum(item["quantity"] for item in self.items)

    def to_dict(self):
        return {
            "order_id": self.order_id,
            "user_id": self.user_id,
            "items": self.items,
            "status": self.status,
            "subtotal": self.subtotal(),
            "created_at": self.created_at.isoformat(),
        }


def create_order(user_id, items, db):
    """Insert a new order into the database and return the Order object."""
    order_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO orders (order_id, user_id, status) VALUES (?, ?, ?)",
        (order_id, user_id, "pending")
    )
    for item in items:
        db.execute(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?)",
            (order_id, item["product_id"], item["quantity"], item["unit_price"])
        )
    db.commit()
    return Order(order_id, user_id, items)


def get_order_by_id(order_id, db):
    """Fetch an order and its line items."""
    row = db.execute(
        "SELECT order_id, user_id, status FROM orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    if row is None:
        return None
    items = db.execute(
        "SELECT product_id, quantity, unit_price FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchall()
    return Order(row["order_id"], row["user_id"], [dict(i) for i in items], row["status"])
```

---

## models/pricing.py

```python
import logging

logger = logging.getLogger(__name__)


TIER_DISCOUNT_SCHEDULE = {
    "silver": [
        {"min_cents": 0, "rate": 0.05},
    ],
    "gold": [
        {"min_cents": 0, "rate": 0.10},
    ],
    "platinum": [
        {"min_cents": 0, "rate": 0.15},
    ],
}


def get_discount_rate(user_tier: str, subtotal_cents: int) -> float:
    """Look up the discount rate for a tier and subtotal.

    The schedule supports tiered brackets (min_cents thresholds), but
    current configuration uses a single flat rate per tier.
    """
    brackets = TIER_DISCOUNT_SCHEDULE.get(user_tier, [])
    rate = 0.0
    for bracket in brackets:
        if subtotal_cents >= bracket["min_cents"]:
            rate = bracket["rate"]
    return rate


def apply_tier_discount(subtotal_cents: int, user_tier: str) -> int:
    """Apply tier-based discount. Returns discounted total in cents."""
    rate = get_discount_rate(user_tier, subtotal_cents)
    return subtotal_cents - int(subtotal_cents * rate)
```

---

## services/payment.py

```python
import logging
import requests
# Unused import — was needed for an older retry implementation
import time

logger = logging.getLogger(__name__)

PAYMENT_GATEWAY_URL = "https://payments.internal/v1/charge"


def verify_card_on_file(user, card_number):
    """Check if a card number is registered to the user."""
    return user.validate_card(card_number)


def process_payment(user, card_number, amount_cents, order_id):
    """Submit a payment charge to the external payment gateway."""
    if not verify_card_on_file(user, card_number):
        raise ValueError("Card not on file for this user")

    try:
        response = requests.post(
            PAYMENT_GATEWAY_URL,
            json={
                "order_id": order_id,
                "amount_cents": amount_cents,
                "user_id": user.user_id,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.error("Payment failed for order %s: %s", order_id, exc)
        raise


def refund_payment(order_id, amount_cents):
    """Issue a refund through the payment gateway."""
    try:
        response = requests.post(
            PAYMENT_GATEWAY_URL.replace("/charge", "/refund"),
            json={"order_id": order_id, "amount_cents": amount_cents},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.error("Refund failed for order %s: %s", order_id, exc)
        raise
```

---

## services/inventory.py

```python
import logging
# Unused import leftover from an earlier implementation that used threading
import threading

logger = logging.getLogger(__name__)


def check_availability(product, quantity):
    """Return True if the product has sufficient stock."""
    return product.stock_count >= quantity


def reserve_stock(product, quantity, db):
    """Reserve stock for an order item and persist the change."""
    if not check_availability(product, quantity):
        raise ValueError(f"Product {product.product_id} has insufficient stock")
    product.reserve(quantity)
    db.execute(
        "UPDATE products SET stock_count = stock_count - ? WHERE product_id = ?",
        (quantity, product.product_id)
    )
    db.commit()
    logger.info("Reserved %d units of %s", quantity, product.product_id)


def release_stock(product_id, quantity, db):
    """Release reserved stock (e.g., on order cancellation)."""
    db.execute(
        "UPDATE products SET stock_count = stock_count + ? WHERE product_id = ?",
        (quantity, product_id)
    )
    db.commit()
    logger.info("Released %d units of product %s", quantity, product_id)
```

---

## services/notification.py

```python
import logging
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = "mail.internal"
SMTP_PORT = 25


def send_order_confirmation(user_email, order_id, subtotal):
    """Send an order confirmation email."""
    body = f"Your order {order_id} has been confirmed. Total: ${subtotal / 100:.2f}"
    msg = MIMEText(body)
    msg["Subject"] = f"Order Confirmed: {order_id}"
    msg["From"] = "orders@shop.internal"
    msg["To"] = user_email
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.sendmail("orders@shop.internal", user_email, msg.as_string())
    except smtplib.SMTPException as exc:
        logger.error("Failed to send confirmation for order %s: %s", order_id, exc)
        raise


def send_refund_notification(user_email, order_id, refund_amount):
    """Send a refund notification email."""
    body = f"Your refund of ${refund_amount / 100:.2f} for order {order_id} has been processed."
    msg = MIMEText(body)
    msg["Subject"] = f"Refund Processed: {order_id}"
    msg["From"] = "orders@shop.internal"
    msg["To"] = user_email
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.sendmail("orders@shop.internal", user_email, msg.as_string())
    except smtplib.SMTPException as exc:
        logger.error("Failed to send refund notification for %s: %s", order_id, exc)
        raise
```

---

## services/promotions.py

```python
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


ACTIVE_PROMOTIONS = {
    "SUMMER10": {"type": "percentage", "value": 10, "expires": "2025-09-01"},
    "FLAT500": {"type": "fixed_cents", "value": 500, "expires": "2025-12-31"},
    "WELCOME": {"type": "percentage", "value": 5, "expires": "2026-06-01"},
}


def validate_promo_code(code: str) -> dict | None:
    """Return the promotion config if valid and not expired, else None."""
    promo = ACTIVE_PROMOTIONS.get(code.upper())
    if promo is None:
        return None
    if datetime.utcnow() > datetime.fromisoformat(promo["expires"]):
        return None
    return promo


def apply_promo_discount(subtotal_cents: int, promo: dict) -> int:
    """Apply a promotional discount to a subtotal."""
    if promo["type"] == "percentage":
        discount = int(subtotal_cents * promo["value"] / 100)
    elif promo["type"] == "fixed_cents":
        discount = promo["value"]
    else:
        discount = 0
    return max(subtotal_cents - discount, 0)
```

---

## api/orders.py

```python
import logging
from flask import Blueprint, request, jsonify, g
from models.order import create_order, get_order_by_id
from models.product import get_product_by_id
from services.inventory import check_availability, reserve_stock
from services.payment import process_payment
from services.notification import send_order_confirmation

# Unused import — was needed when orders used direct DB sessions, now uses g.db
import sqlite3

logger = logging.getLogger(__name__)
orders_bp = Blueprint("orders", __name__)


def _apply_discount(subtotal_cents, user_tier):
    """Apply tier-based discount to an order subtotal.

    Discount rates:
      silver: 5%
      gold:   10%
      platinum: 15%
    """
    rates = {"silver": 0.05, "gold": 0.10, "platinum": 0.15}
    rate = rates.get(user_tier, 0.0)
    discount = int(subtotal_cents * rate)
    return subtotal_cents - discount


@orders_bp.route("/orders", methods=["POST"])
def create_order_endpoint():
    data = request.get_json() or {}
    user = g.current_user
    db = g.db

    items = data.get("items", [])
    if not items:
        return jsonify({"error": "No items in order"}), 400

    order_items = []
    for item in items:
        product = get_product_by_id(item["product_id"], db)
        if product is None:
            return jsonify({"error": f"Product not found: {item['product_id']}"}), 404
        qty = item.get("quantity", 1)
        if not check_availability(product, qty):
            return jsonify({"error": f"Insufficient stock for {product.name}"}), 422
        reserve_stock(product, qty, db)
        order_items.append({"product_id": product.product_id, "quantity": qty, "unit_price": product.price})

    order = create_order(user.user_id, order_items, db)
    subtotal = order.subtotal()
    discounted = _apply_discount(subtotal, getattr(user, "tier", "standard"))

    card_number = data.get("card_number")
    process_payment(user, card_number, discounted, order.order_id)
    send_order_confirmation(user.email, order.order_id, discounted)

    return jsonify({"order_id": order.order_id, "total_cents": discounted}), 201


@orders_bp.route("/orders/<order_id>", methods=["GET"])
def get_order_endpoint(order_id):
    db = g.db
    order = get_order_by_id(order_id, db)
    if order is None:
        return jsonify({"error": "Order not found"}), 404
    return jsonify(order.to_dict()), 200
```

---

## api/returns.py

```python
import logging
from flask import Blueprint, request, jsonify, g
from models.order import get_order_by_id
from services.payment import refund_payment
from services.notification import send_refund_notification
from services.promotions import validate_promo_code, apply_promo_discount

logger = logging.getLogger(__name__)
returns_bp = Blueprint("returns", __name__)


def _calculate_refund(subtotal_cents, user_tier):
    """Calculate the refund amount after re-applying tier discount.

    Uses percentage-based rates per tier.
    """
    discount_pct = {"silver": 5, "gold": 10, "platinum": 15}
    pct = discount_pct.get(user_tier, 0)
    discount = int(subtotal_cents * pct / 100)
    return subtotal_cents - discount


@returns_bp.route("/orders/<order_id>/return", methods=["POST"])
def create_return(order_id):
    db = g.db
    user = g.current_user

    order = get_order_by_id(order_id, db)
    if order is None:
        return jsonify({"error": "Order not found"}), 404
    if order.user_id != user.user_id:
        return jsonify({"error": "Forbidden"}), 403
    if order.status not in ("delivered", "confirmed"):
        return jsonify({"error": f"Cannot return order with status: {order.status}"}), 422

    subtotal = order.subtotal()
    refund_amount = _calculate_refund(subtotal, getattr(user, "tier", "standard"))

    try:
        refund_payment(order_id, refund_amount)
    except Exception as exc:
        logger.error("Refund processing failed for order %s", order_id)
        raise exc

    send_refund_notification(user.email, order_id, refund_amount)
    return jsonify({"order_id": order_id, "refund_cents": refund_amount}), 200
```

---

## api/products.py

```python
import logging
from flask import Blueprint, jsonify, g
from models.product import get_product_by_id

logger = logging.getLogger(__name__)
products_bp = Blueprint("products", __name__)

# Dead code: this constant was used when product IDs were numeric; now they're UUIDs
LEGACY_PRODUCT_ID_OFFSET = 10000


@products_bp.route("/products/<product_id>", methods=["GET"])
def get_product(product_id):
    db = g.db
    product = get_product_by_id(product_id, db)
    if product is None:
        return jsonify({"error": "Product not found"}), 404
    return jsonify(product.to_dict()), 200


@products_bp.route("/products", methods=["GET"])
def list_products():
    db = g.db
    rows = db.execute("SELECT product_id, name, price, stock_count FROM products").fetchall()
    return jsonify([dict(r) for r in rows]), 200
```

---

## api/users.py

```python
import logging
from flask import Blueprint, jsonify, g
from models.user import get_user_by_id, deactivate_user

logger = logging.getLogger(__name__)
users_bp = Blueprint("users", __name__)


@users_bp.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    db = g.db
    user = get_user_by_id(user_id, db)
    if user is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"user_id": user.user_id, "email": user.email, "name": user.name}), 200


@users_bp.route("/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    if g.current_user.user_id != user_id:
        return jsonify({"error": "Forbidden"}), 403
    db = g.db
    deactivate_user(user_id, db)
    return jsonify({"message": "Account deactivated"}), 200
```

---

## api/admin.py

```python
import logging
from flask import Blueprint, jsonify, g
from models.order import get_order_by_id
from models.user import get_user_by_id

logger = logging.getLogger(__name__)
admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin/orders/<order_id>", methods=["GET"])
def admin_get_order(order_id):
    db = g.db
    order = get_order_by_id(order_id, db)
    if order is None:
        return jsonify({"error": "Order not found"}), 404
    user = get_user_by_id(order.user_id, db)
    return jsonify({
        "order": order.to_dict(),
        "user": {"user_id": user.user_id, "email": user.email, "name": user.name} if user else None,
    }), 200


@admin_bp.route("/admin/users/<int:user_id>/orders", methods=["GET"])
def admin_user_orders(user_id):
    db = g.db
    rows = db.execute(
        "SELECT order_id, user_id, status FROM orders WHERE user_id = ?", (user_id,)
    ).fetchall()
    return jsonify({"orders": [dict(r) for r in rows]}), 200
```

---

## app.py

```python
import os
from flask import Flask
from api.orders import orders_bp
from api.returns import returns_bp
from api.products import products_bp
from api.users import users_bp
from api.admin import admin_bp


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
    app.config["DATABASE"] = os.environ.get("DATABASE_URL", "sqlite:///shop.db")

    app.register_blueprint(orders_bp, url_prefix="/api")
    app.register_blueprint(returns_bp, url_prefix="/api")
    app.register_blueprint(products_bp, url_prefix="/api")
    app.register_blueprint(users_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/api")

    return app
```
