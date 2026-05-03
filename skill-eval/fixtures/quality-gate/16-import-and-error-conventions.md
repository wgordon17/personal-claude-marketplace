## Completed Work

```diff
diff --git a/src/billing/charges.py b/src/billing/charges.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/src/billing/charges.py
@@ -0,0 +1,52 @@
+import os
+import json
+from datetime import datetime
+import logging
+from typing import Optional
+
+import stripe
+from flask import current_app
+
+from src.db import db
+from src.models.charge import Charge
+from src.models.user import User
+
+logger = logging.getLogger(__name__)
+
+STRIPE_API_KEY = os.environ["STRIPE_API_KEY"]
+MAX_RETRY_COUNT = 3
+
+
+def create_charge(user_id: int, amount_cents: int, description: str) -> Charge:
+    """Create a Stripe charge and persist the record."""
+    user = User.query.get_or_404(user_id)
+
+    try:
+        stripe_charge = stripe.Charge.create(
+            amount=amount_cents,
+            currency="usd",
+            customer=user.stripe_customer_id,
+            description=description,
+        )
+    except Exception as e:
+        logger.error("Stripe charge failed: %s", e)
+        raise
+
+    charge = Charge(
+        user_id=user_id,
+        stripe_id=stripe_charge.id,
+        amount_cents=amount_cents,
+        status=stripe_charge.status,
+        created_at=datetime.utcnow(),
+    )
+    db.session.add(charge)
+    db.session.commit()
+    return charge
+
+
+def refund_charge(charge_id: int, reason: Optional[str] = None) -> Charge:
+    """Refund a charge via Stripe and update the record."""
+    charge = Charge.query.get_or_404(charge_id)
+
+    try:
+        stripe.Refund.create(charge=charge.stripe_id, reason=reason)
+    except Exception as e:
+        logger.error("Stripe refund failed: %s", e)
+        raise
+
+    charge.status = "refunded"
+    db.session.commit()
+    return charge
```

## Codebase Conventions (from existing modules)

```python
# src/auth/login.py — import ordering: stdlib, blank line, third-party, blank line, local
import hashlib
import logging
from datetime import datetime, timedelta

from flask import request, jsonify, g

from src.db import db
from src.models.user import User
from src.auth.tokens import create_token

# src/auth/login.py — error handling: specific exceptions with context
try:
    user = authenticate(username, password)
except InvalidCredentialsError:
    logger.warning("Failed login for user=%s", username)
    raise
except DatabaseConnectionError as exc:
    logger.exception("Database unavailable during login")
    raise ServiceUnavailableError("Auth unavailable") from exc

# src/utils/helpers.py — type hints: modern union syntax (3.10+)
def get_user_or_none(user_id: int) -> User | None:
    ...

def parse_date(raw: str) -> datetime | None:
    ...
```

## Project Context

This is a Flask web application with Stripe integration for billing. Python 3.12. The project CLAUDE.md specifies: "Use specific exception types, not bare `except Exception`. Use `X | None` syntax, not `Optional[X]`. Follow isort import ordering: stdlib, third-party, local with blank line separators."
