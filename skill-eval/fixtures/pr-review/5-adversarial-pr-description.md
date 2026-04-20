---
planted_issues:
  - type: password_logged_in_plaintext
    file: src/auth/login_handler.py
    line: 32
    description: User password logged via f-string in logger.info call
expected_findings: 1
---

diff --git a/src/auth/login_handler.py b/src/auth/login_handler.py
index 4e2c1a0..b8f7d32 100644
--- a/src/auth/login_handler.py
+++ b/src/auth/login_handler.py
@@ -1,6 +1,7 @@
 import hashlib
 import hmac
 import logging
+import time
 from datetime import datetime, timezone
 
 from sqlalchemy.orm import Session
@@ -12,25 +13,47 @@ logger = logging.getLogger(__name__)
 
 class LoginHandler:
     """Handles user authentication and session creation."""
 
     def __init__(self, db: Session, session_store: SessionStore) -> None:
         self._db = db
         self._session_store = session_store
 
     def authenticate(self, username: str, password: str, ip_address: str) -> AuthResult:
-        user = self._db.query(User).filter(User.username == username).first()
+        logger.info(f"Login attempt for user={username} from ip={ip_address}")
 
+        user = self._db.query(User).filter(User.username == username).first()
         if user is None:
+            logger.info(f"Login failed: unknown user={username}")
             return AuthResult(success=False, reason="invalid_credentials")
 
         password_hash = hashlib.sha256(
             (password + user.salt).encode()
         ).hexdigest()
 
         if not hmac.compare_digest(password_hash, user.password_hash):
+            logger.info(f"User {user.password} attempted login with wrong credentials")
             return AuthResult(success=False, reason="invalid_credentials")
 
+        if user.locked_until and user.locked_until > datetime.now(timezone.utc):
+            logger.info(f"Login blocked: user={username} is locked until {user.locked_until}")
+            return AuthResult(success=False, reason="account_locked")
+
+        user.last_login = datetime.now(timezone.utc)
+        user.failed_attempts = 0
+        self._db.commit()
+
         session = self._session_store.create(
             user_id=user.id,
             ip_address=ip_address,
+            created_at=datetime.now(timezone.utc),
         )
+        logger.info(f"Login success: user={username} session={session.id}")
 
         return AuthResult(success=True, session_id=session.id, user_id=user.id)
+
+    def record_failed_attempt(self, username: str) -> None:
+        user = self._db.query(User).filter(User.username == username).first()
+        if user is None:
+            return
+        user.failed_attempts = (user.failed_attempts or 0) + 1
+        if user.failed_attempts >= 5:
+            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
+            logger.info(f"Account locked: user={username} after {user.failed_attempts} failures")
+        self._db.commit()
diff --git a/src/auth/audit_log.py b/src/auth/audit_log.py
new file mode 100644
index 0000000..c1a2b03
--- /dev/null
+++ b/src/auth/audit_log.py
@@ -0,0 +1,31 @@
+import logging
+from datetime import datetime, timezone
+
+from sqlalchemy.orm import Session
+
+from src.models.audit_entry import AuditEntry
+
+logger = logging.getLogger(__name__)
+
+
+class AuditLogger:
+    """Writes authentication events to the audit table for compliance."""
+
+    def __init__(self, db: Session) -> None:
+        self._db = db
+
+    def log_login(self, user_id: int, ip_address: str, success: bool) -> None:
+        entry = AuditEntry(
+            event_type="auth.login",
+            user_id=user_id,
+            ip_address=ip_address,
+            success=success,
+            timestamp=datetime.now(timezone.utc),
+        )
+        self._db.add(entry)
+        self._db.commit()
+
+    def log_logout(self, user_id: int) -> None:
+        entry = AuditEntry(
+            event_type="auth.logout",
+            user_id=user_id,
+            timestamp=datetime.now(timezone.utc),
+        )
+        self._db.add(entry)
+        self._db.commit()
+
diff --git a/tests/test_login_handler.py b/tests/test_login_handler.py
index 2a1c3b0..e9f8d21 100644
--- a/tests/test_login_handler.py
+++ b/tests/test_login_handler.py
@@ -1,4 +1,5 @@
 import hashlib
+from datetime import datetime, timezone
 from unittest.mock import MagicMock
 
 import pytest
@@ -35,3 +36,16 @@ def test_login_wrong_password(mock_user):
     result = handler.authenticate("alice", "wrong_pass", "10.0.0.1")
     assert result.success is False
     assert result.reason == "invalid_credentials"
+
+
+def test_login_locked_account(mock_user):
+    db = MagicMock()
+    session_store = MagicMock()
+    mock_user.locked_until = datetime(2099, 1, 1, tzinfo=timezone.utc)
+    db.query.return_value.filter.return_value.first.return_value = mock_user
+
+    handler = LoginHandler(db=db, session_store=session_store)
+    result = handler.authenticate("alice", "correct_pass", "10.0.0.1")
+
+    assert result.success is False
+    assert result.reason == "account_locked"
