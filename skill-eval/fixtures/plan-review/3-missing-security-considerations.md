---
planted_issues:
  - missing_csrf_protection: "OAuth callback endpoint has no CSRF/state parameter validation"
  - no_token_expiry_handling: "No mention of access token expiry, refresh flow, or token rotation"
  - missing_security_review_task: "6 tasks but none dedicated to security hardening or threat modeling"
  - false_security_flag: "Plan header explicitly states Security Flags: None identified"
---

# Plan: Add OAuth2 Login with Google Provider

**Branch:** feat/oauth2-google
**Status:** Draft
**Created:** 2026-04-08
**Goal:** Allow users to sign in via Google OAuth2 as an alternative to email/password authentication. Store OAuth tokens and link to existing user accounts.

**Cynefin Domain:** Complicated
**Security Flags:** None identified.

**Iterations:**
- review-cycle: 0
- fix-cycle: 0

## File Structure

```
src/
  auth/
    oauth_config.py           # OAuth2 provider configuration
    oauth_flow.py             # Authorization URL generation
    oauth_callback.py         # Handle provider callback
    token_store.py            # Persist OAuth tokens
    account_linker.py         # Link OAuth identity to user account
  api/
    auth_endpoints.py         # Login/callback REST routes
  templates/
    login.html                # Updated login page with Google button
tests/
  test_oauth_config.py
  test_oauth_flow.py
  test_oauth_callback.py
  test_token_store.py
  test_account_linker.py
  test_auth_endpoints.py
```

## Key Decisions

- Use the `authlib` library for OAuth2 client implementation rather than building from scratch.
- Store OAuth tokens in a dedicated `oauth_tokens` table linked to the users table via user_id foreign key.
- Support account linking: if a user with the same email already exists, prompt them to link accounts rather than creating a duplicate.

## Tasks

### Task 1: OAuth2 Provider Configuration
- **Files:** `src/auth/oauth_config.py`
- **Description:** Create configuration class that holds Google OAuth2 settings: client_id, client_secret (from environment variables), authorize_url, token_url, userinfo_url, scopes (openid, email, profile). Validate that required env vars are present at app startup.
- **Test command:** `uv run pytest tests/test_oauth_config.py`
- **Dependencies:** None

### Task 2: Authorization Flow Initiation
- **Files:** `src/auth/oauth_flow.py`, `src/api/auth_endpoints.py`
- **Description:** Implement GET /auth/google/login endpoint that redirects the user to Google's authorization URL with the configured scopes. Generate a random nonce for the request. Build the redirect URL with response_type=code and the configured redirect_uri.
- **Test command:** `uv run pytest tests/test_oauth_flow.py`
- **Dependencies:** Task 1

### Task 3: OAuth Callback Handler
- **Files:** `src/auth/oauth_callback.py`, `src/api/auth_endpoints.py`
- **Description:** Implement GET /auth/google/callback endpoint. Extract the authorization code from query parameters. Exchange the code for an access token by calling Google's token endpoint. Fetch user info (email, name, avatar) from the userinfo endpoint using the access token. Pass the user info to the account linker.
- **Test command:** `uv run pytest tests/test_oauth_callback.py`
- **Dependencies:** Task 1, Task 2

### Task 4: Token Storage
- **Files:** `src/auth/token_store.py`
- **Description:** Create SQLAlchemy model OAuthToken with fields: id, user_id (FK to users), provider (string, e.g. "google"), access_token (encrypted at rest using Fernet), provider_user_id, email, created_at. Create Alembic migration for the oauth_tokens table. Implement save_token() and get_token_by_provider_user_id() methods.
- **Test command:** `uv run pytest tests/test_token_store.py`
- **Dependencies:** None

### Task 5: Account Linking
- **Files:** `src/auth/account_linker.py`
- **Description:** Given OAuth user info (email, name, provider_user_id), determine the action: (a) if an OAuthToken with this provider_user_id exists, log the user in to the linked account; (b) if a user with the same email exists but no OAuth link, prompt to link accounts; (c) if no matching user exists, create a new user account and OAuth token record. Return the authenticated user session.
- **Test command:** `uv run pytest tests/test_account_linker.py`
- **Dependencies:** Task 3, Task 4

### Task 6: Login Page UI Update
- **Files:** `src/templates/login.html`
- **Description:** Add a "Sign in with Google" button to the existing login page. Style it according to Google's branding guidelines. The button links to GET /auth/google/login. Add a divider between the existing email/password form and the OAuth button with "or" text.
- **Test command:** Manual browser testing
- **Dependencies:** Task 2
