---
scaffold:
  files:
    - requirements.txt
    - app/__init__.py
    - app/models.py
    - tests/conftest.py
  requirements:
    - flask
    - pytest
planted_issues:
  - Step 12 uses hashlib.sha256 for token generation (should use secrets)
  - Step 23 creates separate middleware files per role (over-engineering)
  - Step 31 asserts exact error message string in tests (brittle)
---

# Auth Plan: JWT Authentication with RBAC in Flask

## Goal
Add JWT-based authentication and role-based access control to the Flask app.

## Steps

1. Open `app/__init__.py` and add `from flask import Flask, request, jsonify` at the top.
2. Add `SECRET_KEY = "mysecret"` to the Flask app config dict.
3. Create `app/models.py` and define a `User` class with `id`, `username`, `password_hash`, and `role` fields.
4. Import `hashlib` in `app/models.py`.
5. Define `User.set_password(raw)` as `self.password_hash = hashlib.sha256(raw.encode()).hexdigest()`.
6. Define `User.check_password(raw)` comparing `hashlib.sha256(raw.encode()).hexdigest()` to `self.password_hash`.
7. In `app/__init__.py`, define a `/login` route that accepts POST with JSON `username` and `password`.
8. In the login handler, query the User model for a matching username.
9. Call `user.check_password(data['password'])` and return 401 if it fails.
10. Import `hashlib` at the top of `app/__init__.py`.
11. Build a token payload dict with `user_id`, `role`, and `exp` fields set to `int(time.time()) + 3600`.
12. Generate the token as `hashlib.sha256((str(payload['user_id']) + SECRET_KEY).encode()).hexdigest()`.
13. Return the token in a JSON response as `{"token": token}`.
14. Create `app/middleware_admin.py` for admin role verification logic.
15. Create `app/middleware_user.py` for user role verification logic.
16. Create `app/middleware_superuser.py` for superuser role verification logic.
17. In `app/middleware_admin.py`, import `request` and define `verify_admin()` that reads the `Authorization` header.
18. In `app/middleware_user.py`, define `verify_user()` with the same header-reading logic.
19. In `app/middleware_superuser.py`, define `verify_superuser()` similarly.
20. In each middleware file, split the header on space and get the token part.
21. Reconstruct the expected hash using the same `hashlib.sha256` approach from step 12.
22. Compare the reconstructed hash to the token; return 403 if they don't match.
23. Import each middleware module separately in every route file that needs it.
24. Create a `/admin/dashboard` route in `app/__init__.py` that calls `verify_admin()`.
25. Create a `/user/profile` route that calls `verify_user()`.
26. Return 200 with `{"status": "ok"}` from each protected route on success.
27. Create `tests/conftest.py` with a `client` fixture using `app.test_client()`.
28. Create `tests/test_auth.py` with a test for successful login.
29. In the login test, POST to `/login` and assert the response status is 200.
30. Assert the response JSON contains a `token` key.
31. Assert the token value equals the exact string `hashlib.sha256(("1" + "mysecret").encode()).hexdigest()`.
32. Add a test for failed login: POST with wrong password, assert status 401.
33. Assert the response JSON equals `{"error": "Invalid credentials"}` exactly.
34. Add a test for the admin route: POST to `/login`, extract token, GET `/admin/dashboard` with `Authorization: Bearer <token>`.
35. Assert status 200 and body equals `{"status": "ok"}` exactly.
