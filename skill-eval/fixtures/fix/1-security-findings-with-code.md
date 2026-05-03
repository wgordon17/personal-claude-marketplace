---
planted_issues:
  - type: sql_injection
    file: src/api/users.py
    line: 24
  - type: missing_auth_check
    file: src/api/admin.py
    line: 18
  - type: hardcoded_secret
    file: src/api/payments.py
    line: 11
expected_findings: 3
---

## CODE REVIEW Findings

The following 3 findings were identified by domain reviewers and verified.

### SECURITY

**Finding pr-sec-1:** SQL injection vulnerability in user lookup query
**Location:** `src/api/users.py:24`
**Evidence:** Query string built via f-string interpolation with unsanitized user input. Attacker-controlled `username` parameter is injected directly into SQL.

```python
# src/api/users.py
import sqlite3
from flask import Flask, request, jsonify

app = Flask(__name__)

DATABASE = "app.db"


def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/api/users/search")
def search_users():
    username = request.args.get("username", "")
    conn = get_db_connection()
    cursor = conn.cursor()

    query = f"SELECT id, username, email FROM users WHERE username LIKE '%{username}%'"
    cursor.execute(query)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(results)


@app.route("/api/users/<int:user_id>")
def get_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, email FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(user))
```

---

**Finding pr-sec-2:** Missing authentication check on admin endpoint
**Location:** `src/api/admin.py:18`
**Evidence:** The `delete_user` endpoint performs a destructive operation without verifying the caller has admin privileges. No authentication middleware is applied to this route.

```python
# src/api/admin.py
from flask import Flask, request, jsonify
from db import get_db_connection

app = Flask(__name__)


def verify_admin(token):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM sessions WHERE token = ?", (token,))
    row = cursor.fetchone()
    conn.close()
    return row and row["role"] == "admin"


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"deleted": user_id})


@app.route("/api/admin/stats")
def admin_stats():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not verify_admin(token):
        return jsonify({"error": "unauthorized"}), 403
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as total FROM users")
    stats = dict(cursor.fetchone())
    conn.close()
    return jsonify(stats)
```

---

**Finding pr-sec-3:** Hardcoded API secret in source code
**Location:** `src/api/payments.py:11`
**Evidence:** Stripe API key is hardcoded as a string literal. This secret will be committed to version control and visible to anyone with repository access.

```python
# src/api/payments.py
import stripe
from flask import Flask, request, jsonify

app = Flask(__name__)


def configure_stripe():
    stripe.api_key = "sk_live_EXAMPLE_KEY_NOT_REAL_1234567890"


configure_stripe()


@app.route("/api/payments/charge", methods=["POST"])
def create_charge():
    data = request.get_json()
    amount = data.get("amount")
    currency = data.get("currency", "usd")
    source = data.get("source")

    charge = stripe.Charge.create(
        amount=amount,
        currency=currency,
        source=source,
        description="Payment",
    )
    return jsonify({"charge_id": charge.id, "status": charge.status})


@app.route("/api/payments/<charge_id>")
def get_charge(charge_id):
    charge = stripe.Charge.retrieve(charge_id)
    return jsonify({"charge_id": charge.id, "status": charge.status, "amount": charge.amount})
```
