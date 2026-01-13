---
name: url-check
description: PROACTIVE skill - Activates when adding views or URL patterns. Validates URL conventions and security requirements per URLS.md.
allowed-tools: [Read, Grep, Glob, LSP]
---

# URL Architecture Validator

## Purpose

Ensure new URLs follow Project's conventions, security requirements, and documentation standards.

## When to Trigger

This skill activates PROACTIVELY when:
- Adding new views (`views.py`, `views_settings.py`)
- Adding URL patterns (`urls.py`)
- Modifying authentication requirements
- User asks about URL structure

## URL Hierarchy

Project uses a consistent hierarchy:

```
/accounts/*           Authentication flows (unauthenticated access)
/dashboard/*          Authenticated features
/dashboard/settings/* User settings
```

## Validation Checklist

When a new URL is added:

### 1. Naming Convention

| Pattern | Example | Use Case |
|---------|---------|----------|
| `security_*` | `security_logins` | Security-related settings |
| `profile` | `profile` | User profile page |
| `<noun>_<verb>` | `add_category` | CRUD operations |
| `<feature>` | `settings`, `dashboard` | Main pages |

**Avoid:**
- Technical jargon (use "logins" not "sessions")
- Redundant prefixes (`security_password` not `settings_security_password`)

### 2. Authentication Check

```python
# Class-based views MUST use:
from django.contrib.auth.mixins import LoginRequiredMixin

class MyView(LoginRequiredMixin, View):
    pass

# Function-based views MUST use:
from django.contrib.auth.decorators import login_required

@login_required
def my_view(request):
    pass
```

### 3. Security Enforcement Registration

All protected URLs MUST be in `apps/security/url_enforcement.py`:

```python
MUST_BE_SECURED_URLS = {
    "/dashboard/settings/your-feature/": "Description of what this protects",
}
```

### 4. Storyboard Consideration

If the page needs screenshots:
- Add handler to `apps/storyboard/fixtures.py` `SESSION_SETUP_HANDLERS`
- Only skip if there's a strong reason

### 5. Test Requirements

```python
def test_feature_requires_auth(client_with_public_ip):
    response = client_with_public_ip.get(reverse("url_name"))
    assert response.status_code == 302
    assert "/accounts/login/" in response.url

def test_feature_accessible_when_authenticated(authenticated_dashboard_client):
    response = authenticated_dashboard_client.get(reverse("url_name"))
    assert response.status_code == 200
```

## Adding New URLs Workflow

### Step 1: Choose Location

| Setting Type | URL Pattern | Example |
|-------------|-------------|---------|
| Profile-related | `/settings/profile/` or subpath | `/settings/profile/timezone/` |
| Security-related | `/settings/security/<feature>/` | `/settings/security/2fa/` |
| App preferences | `/settings/<feature>/` | `/settings/notifications/` |
| Transaction-related | `/settings/categories/` | `/settings/budgets/` |

### Step 2: Create View

In appropriate app:
- Account/security → `apps/accounts/views_settings.py`
- Transaction features → `apps/transactions/views_settings.py`

### Step 3: Register URL

```python
# apps/transactions/urls.py
path("settings/your-feature/", YourView.as_view(), name="your_url_name"),
```

### Step 4: Update Security Enforcement

```python
# apps/security/url_enforcement.py
MUST_BE_SECURED_URLS = {
    # ...existing...
    "/dashboard/settings/your-feature/": "Description",
}
```

### Step 5: Update Settings Hub (if applicable)

Add navigation card to `templates/transactions/settings.html`:

```html
<a href="{% url 'your_url_name' %}" class="block card hover:shadow-lg">
  <div class="px-6 py-5 flex items-center space-x-4">
    <div class="flex-shrink-0 p-3 bg-purple-100 rounded-xl">
      <!-- Icon SVG -->
    </div>
    <div class="flex-1">
      <h2 class="text-lg font-semibold">Feature Name</h2>
      <p class="mt-1 text-sm text-slate-500">Brief description</p>
    </div>
  </div>
</a>
```

### Step 6: Add Tests

### Step 7: Update URLS.md

Via `/docs-sync` skill.

## Current URL Structure Reference

```
/dashboard/settings/                     → Settings hub
├── /settings/categories/                → Transaction categories
├── /settings/profile/                   → Profile
└── /settings/security/                  → Security hub
    ├── /settings/security/password/     → Change password
    ├── /settings/security/recovery/     → Recovery codes
    └── /settings/security/logins/       → Active sessions
```

## Authentication Flow URLs

These remain at `/accounts/` (not `/dashboard/`):

| URL | Purpose |
|-----|---------|
| `/accounts/login/` | Login page |
| `/accounts/signup/` | Registration |
| `/accounts/logout/` | Logout (POST-only) |
| `/accounts/recover/` | Account recovery |
| `/accounts/recover/confirm/<token>/` | Recovery confirmation |

**Why separate?** Auth flows are accessed before authentication.

## Required Skills

- `/lsp-navigation` — Find view definitions and references

## Integration

### Called By
- `project-dev:feature-writer` — After adding views
- `project-dev:orchestrator` — During feature validation

### Invokes
- `/docs-sync` — To update URLS.md
