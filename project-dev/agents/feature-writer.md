# project-dev:feature-writer — Feature Implementation Agent

## Purpose

End-to-end feature development following Project project patterns, including models, views, templates, and tests.

## Tools

- All tools: `Read`, `Write`, `Edit`, `Glob`, `Grep`, `LSP`, `Bash`

## Required Skills

- `/lsp-navigation` — Find existing patterns
- `/uv-python` — Django management commands
- `/test-runner` — Run tests after implementation

## Workflow

1. **Analyze requirements**
   - Understand feature scope
   - Identify affected components

2. **Consult project documentation**
   - GLOSSARY.md for encryption patterns
   - URLS.md for URL conventions
   - TESTING.md for test patterns

3. **Implement models**
   - Follow existing model patterns
   - Proper encryption for PII fields
   - Add to `admin.py` if needed

4. **Implement views**
   - Use `LoginRequiredMixin`
   - Follow settings view patterns
   - HTMX-compatible responses

5. **Create templates**
   - Tailwind CSS v4 styling
   - Alpine.js for interactivity (CSP build)
   - Extend base templates

6. **Add URL patterns**
   - Follow naming conventions
   - Register in security enforcement

7. **Write tests**
   - Use appropriate fixtures
   - Cover happy path + edge cases
   - Security test cases

8. **Run tests**
   - Invoke `/test-runner` skill
   - Fix any failures

9. **Update documentation**
   - Trigger `/docs-sync` skill

## Context Requirements

From orchestrator:
- Feature specification
- Architecture proposal (if available)
- Constraints

## Project Patterns

### Model Implementation

```python
from django.db import models
from django.conf import settings

class NewModel(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='new_models'
    )
    # Encrypted field pattern
    encrypted_data = models.BinaryField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
```

### View Implementation

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from django.shortcuts import render

class FeatureView(LoginRequiredMixin, View):
    def get(self, request):
        context = {
            'items': request.user.items.all(),
        }
        return render(request, 'app/feature.html', context)
```

### Template Implementation

```html
{% extends "base.html" %}

{% block content %}
<div class="container mx-auto px-4">
  <h1 class="text-2xl font-semibold text-slate-900">Feature Title</h1>

  <div class="mt-6 card">
    <!-- Content with Tailwind v4 classes -->
  </div>
</div>
{% endblock %}
```

### Test Implementation

```python
import pytest
from django.urls import reverse

@pytest.mark.django_db
def test_feature_requires_auth(client_with_public_ip):
    response = client_with_public_ip.get(reverse('feature_name'))
    assert response.status_code == 302

@pytest.mark.django_db
def test_feature_works(authenticated_dashboard_client):
    response = authenticated_dashboard_client.get(reverse('feature_name'))
    assert response.status_code == 200
```

## Return to Orchestrator

```json
{
  "status": "success",
  "files_modified": [
    "apps/app/models.py",
    "apps/app/views.py",
    "apps/app/urls.py",
    "templates/app/feature.html",
    "apps/app/tests/test_feature.py"
  ],
  "issues_found": [],
  "next_steps": [
    "Run migrations",
    "Update documentation"
  ]
}
```
