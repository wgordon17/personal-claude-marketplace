# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

import re
from datetime import datetime


EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{3,64}$")


def validate_email(email):
    if not isinstance(email, str):
        return False, "Email must be a string"
    email = email.strip().lower()
    if len(email) > 256:
        return False, "Email too long"
    if not EMAIL_PATTERN.match(email):
        return False, "Invalid email format"
    return True, None


def validate_username(username):
    if not isinstance(username, str):
        return False, "Username must be a string"
    if not USERNAME_PATTERN.match(username):
        return False, "Username must be 3-64 chars, alphanumeric/underscore/dash only"
    return True, None


def validate_password(password):
    if not isinstance(password, str):
        return False, "Password must be a string"
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    if len(password) > 128:
        return False, "Password too long"
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_upper and has_lower and has_digit):
        return False, "Password must contain uppercase, lowercase, and a digit"
    return True, None


def validate_task_title(title):
    if not isinstance(title, str):
        return False, "Title must be a string"
    title = title.strip()
    if not title:
        return False, "Title cannot be empty"
    if len(title) > 256:
        return False, "Title too long (max 256 chars)"
    return True, None


def validate_date_string(date_str):
    if not isinstance(date_str, str):
        return False, None, "Date must be a string"
    try:
        dt = datetime.fromisoformat(date_str)
        return True, dt, None
    except ValueError:
        return False, None, "Invalid date format, expected ISO 8601"


def sanitize_search_term(term):
    if not isinstance(term, str):
        return ""
    term = term.strip()
    term = term[:200]
    return term


def validate_pagination(page, per_page, max_per_page=100):
    errors = []
    try:
        page = int(page)
        if page < 1:
            errors.append("page must be >= 1")
    except (TypeError, ValueError):
        errors.append("page must be an integer")
        page = 1

    try:
        per_page = int(per_page)
        if per_page < 1:
            errors.append("per_page must be >= 1")
        elif per_page > max_per_page:
            per_page = max_per_page
    except (TypeError, ValueError):
        errors.append("per_page must be an integer")
        per_page = 20

    return page, per_page, errors
