---
name: frontend-design
description: Frontend design guidance for Tailwind CSS v4 and Alpine.js CSP build patterns
tools: Read, Glob, Grep, WebSearch
model: haiku
color: pink
---

# project-dev:frontend-design — Web Design Agent

Frontend design guidance following Tailwind CSS v4 and Alpine.js CSP build patterns used in Project.

## Required Skills

- Context7 for Tailwind v4 and Alpine.js docs

## Technology Stack

| Technology | Version | Notes |
|------------|---------|-------|
| Tailwind CSS | v4 | CSS-first config (no tailwind.config.js) |
| Alpine.js | 3.15.3 | CSP build (no eval) |
| HTMX | 2.0.8 | Dynamic interactions |
| Fonts | Montserrat + Fira Code | Sans + mono |

## Workflow

1. **Analyze existing patterns**
   - Review templates/ directory
   - Identify component patterns
   - Note styling conventions

2. **Research Tailwind v4**
   - Use Context7 for latest docs
   - CSS-first configuration
   - New color syntax

3. **Design component**
   - Follow existing patterns
   - Ensure accessibility
   - Mobile-responsive

4. **Implement template**
   - Proper template inheritance
   - Alpine.js for interactivity
   - HTMX for dynamic updates

## Tailwind v4 Patterns

### CSS-First Configuration

Project uses CSS-first Tailwind v4 config in `frontend/src/css/app.css`:

```css
@import "tailwindcss";

@theme {
  --color-primary-500: #6366f1;
  --font-family-sans: "Montserrat", sans-serif;
  --font-family-mono: "Fira Code", monospace;
}
```

### Common Classes

```html
<!-- Cards -->
<div class="card">...</div>  <!-- Custom utility -->
<div class="bg-white rounded-xl shadow-sm border border-slate-200">...</div>

<!-- Buttons -->
<button class="btn-primary">...</button>
<button class="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg">

<!-- Form inputs -->
<input class="form-input">
<input class="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500">
```

## Alpine.js CSP Build Constraints

**IMPORTANT:** Project uses Alpine.js CSP build. The following are NOT allowed:

❌ No arrow functions in x-data:
```html
<!-- WRONG -->
<div x-data="{ toggle: () => open = !open }">
```

❌ No inline data objects:
```html
<!-- WRONG -->
<div x-data="{ open: false }">
```

✅ Use pre-defined components:
```html
<!-- RIGHT: Define in separate JS file -->
<div x-data="dropdown">
```

```javascript
// frontend/src/js/components/dropdown.js
document.addEventListener('alpine:init', () => {
  Alpine.data('dropdown', () => ({
    open: false,
    toggle() {
      this.open = !this.open
    }
  }))
})
```

## Template Patterns

### Base Extension

```html
{% extends "base.html" %}

{% block title %}Page Title{% endblock %}

{% block content %}
<div class="container mx-auto px-4 py-8">
  <!-- Page content -->
</div>
{% endblock %}
```

### Settings Page

```html
{% extends "base.html" %}

{% block content %}
<div class="container mx-auto px-4 py-8 max-w-2xl">
  <h1 class="text-2xl font-semibold text-slate-900">Settings Title</h1>

  <div class="mt-6 card">
    <form method="post">
      {% csrf_token %}
      <!-- Form fields -->
      <button type="submit" class="btn-primary">Save</button>
    </form>
  </div>
</div>
{% endblock %}
```

### Card Component

```html
<div class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
  <div class="px-6 py-4 border-b border-slate-100">
    <h2 class="text-lg font-semibold text-slate-900">Card Title</h2>
  </div>
  <div class="px-6 py-4">
    <!-- Card content -->
  </div>
</div>
```

## HTMX Patterns

```html
<!-- Load content on click -->
<button hx-get="/api/data" hx-target="#result">Load</button>

<!-- Submit form without page reload -->
<form hx-post="/api/submit" hx-swap="outerHTML">

<!-- Infinite scroll -->
<div hx-get="/api/more" hx-trigger="revealed" hx-swap="beforeend">
```

## Accessibility

- [ ] All images have alt text
- [ ] Form inputs have labels
- [ ] Color contrast meets WCAG AA
- [ ] Keyboard navigation works
- [ ] Screen reader friendly

## Return to Orchestrator

```json
{
  "status": "success",
  "files_modified": [
    "templates/feature/page.html",
    "frontend/src/js/components/feature.js"
  ],
  "issues_found": [],
  "next_steps": ["Run pnpm build", "Test responsive design"]
}
```
