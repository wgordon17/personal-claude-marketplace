---
# Fixture metadata (stripped by loader)
scenario: "Three fully independent plans with zero file overlap"
expected_phases: 1
expected_ordering: "All three in a single parallel phase"
---

### Plan A: CSV Export Feature

**Goal:** Add CSV export functionality for report data.
**Cynefin Domain:** Clear
**Architecture Summary:** Self-contained export module in the reports directory. No shared dependencies with other plans.

## File Structure

```
src/
  reports/
    csv_exporter.py      # CSV generation logic
    formatters.py         # Data formatting helpers for export
tests/
  reports/
    test_csv_exporter.py  # Export unit tests
    test_formatters.py    # Formatter tests
```

## Task 1: Implement CSV Exporter

**Files:** `src/reports/csv_exporter.py`, `src/reports/formatters.py`

Create a CsvExporter class that accepts query results and produces CSV output. Use formatters for date, currency, and percentage columns. Stream output for large datasets.

## Task 2: Add Export Endpoint

**Files:** `src/reports/csv_exporter.py`

Add a Flask route GET /reports/export?format=csv that triggers the exporter and returns the file as a download with proper Content-Disposition headers.

## Task 3: Add Export Tests

**Files:** `tests/reports/test_csv_exporter.py`, `tests/reports/test_formatters.py`

Test CSV generation with various data types, empty datasets, large datasets, and formatter edge cases.

---

### Plan B: Fix Email Template Rendering

**Goal:** Fix broken email template rendering for welcome and password-reset emails.
**Cynefin Domain:** Clear
**Architecture Summary:** Bug fix in the email module. Touches only email-related files in a separate directory tree.

## File Structure

```
src/
  email/
    templates/
      welcome.html       # Welcome email template (fix variable escaping)
      password_reset.html # Password reset template (fix broken link)
    renderer.py           # Template rendering engine (fix context passing)
tests/
  email/
    test_renderer.py      # Renderer tests with template snapshots
```

## Task 1: Fix Template Variable Escaping

**Files:** `src/email/templates/welcome.html`

Fix the welcome email template where user_name is not being HTML-escaped, causing rendering issues when names contain special characters.

## Task 2: Fix Password Reset Link

**Files:** `src/email/templates/password_reset.html`

Fix the password reset template where the reset link URL is malformed due to a missing URL encoding step on the token parameter.

## Task 3: Fix Renderer Context Passing

**Files:** `src/email/renderer.py`

Fix the render_template method where the context dict is not being passed to the Jinja2 environment correctly, causing all template variables to render as empty strings.

## Task 4: Add Renderer Tests

**Files:** `tests/email/test_renderer.py`

Add snapshot tests for both email templates with various context data. Verify proper escaping and link generation.

---

### Plan C: Update Dashboard Color Scheme

**Goal:** Update the dashboard UI color scheme from the legacy palette to the new brand colors.
**Cynefin Domain:** Clear
**Architecture Summary:** Pure CSS/styling changes in the dashboard static assets directory. No Python code changes, no shared files with other plans.

## File Structure

```
static/
  css/
    dashboard.css         # Main dashboard stylesheet
    variables.css         # CSS custom properties (color tokens)
  img/
    logo-new.svg          # Updated brand logo
```

## Task 1: Update CSS Variables

**Files:** `static/css/variables.css`

Replace the legacy color palette custom properties with the new brand colors. Update primary, secondary, accent, background, and text color tokens.

## Task 2: Update Dashboard Styles

**Files:** `static/css/dashboard.css`

Update dashboard-specific styles that hardcode legacy colors instead of using CSS variables. Replace all hex color literals with var() references.

## Task 3: Replace Brand Logo

**Files:** `static/img/logo-new.svg`

Add the new brand logo SVG file. Update any CSS references to the logo path.
