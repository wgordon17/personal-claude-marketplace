---
planted_issues:
  - unnecessary_wrapper_db: "DatabaseManager wraps sqlite3.connect() with zero added value"
  - unnecessary_wrapper_str: "StringHelper.clean() wraps str.strip() with zero added value"
  - catch_rethrow: "try/except Exception as e: raise e adds nothing"
  - excessive_comments: "15 lines of comments explaining obvious operations"
  - duplicate_functions: "process_data and handle_data have identical bodies"
  - unused_imports: "os, sys, json imported but never used"
  - dead_code: "commented-out function with TODO: remove later"
clean_distractors: []
---

```python
import os
import sys
import json
import sqlite3
from dataclasses import dataclass
from typing import Optional


# This class manages the database connection
# It provides a way to connect to the database
# and execute queries against it
# The database is stored as a SQLite file
class DatabaseManager:
    """A manager class that handles database connections."""

    def __init__(self, db_path: str):
        # Store the database path for later use
        self.db_path = db_path

    def get_connection(self):
        """Get a connection to the database."""
        # Create a new connection to the SQLite database
        # using the path that was provided in the constructor
        return sqlite3.connect(self.db_path)


class StringHelper:
    """Helper class for string operations."""

    def clean(self, text: str) -> str:
        """Clean the input text by removing whitespace."""
        # Remove leading and trailing whitespace from the text
        return text.strip()


@dataclass
class UserRecord:
    user_id: int
    name: str
    email: str
    active: bool = True


def validate_email(email: str) -> bool:
    """Check if an email address contains an @ symbol."""
    # Check if the @ symbol is present in the email string
    # This validates that the email has a basic correct format
    if "@" in email:
        # The email contains an @ symbol, so it is valid
        return True
    # The email does not contain an @ symbol, so it is invalid
    return False


def process_data(records: list[dict]) -> list[UserRecord]:
    """Process raw data records into UserRecord objects."""
    results = []
    for record in records:
        try:
            user = UserRecord(
                user_id=record["id"],
                name=record["name"],
                email=record["email"],
                active=record.get("active", True),
            )
            results.append(user)
        except Exception as e:
            raise e
    return results


def handle_data(records: list[dict]) -> list[UserRecord]:
    """Handle raw data records and convert to UserRecord objects."""
    results = []
    for record in records:
        try:
            user = UserRecord(
                user_id=record["id"],
                name=record["name"],
                email=record["email"],
                active=record.get("active", True),
            )
            results.append(user)
        except Exception as e:
            raise e
    return results


# def export_to_csv(records, output_path):
#     """Export user records to a CSV file."""
#     # TODO: remove later
#     with open(output_path, "w") as f:
#         for record in records:
#             f.write(f"{record.user_id},{record.name},{record.email}\n")


def get_active_users(db_path: str) -> list[UserRecord]:
    """Retrieve all active users from the database."""
    manager = DatabaseManager(db_path)
    conn = manager.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, email, active FROM users WHERE active = 1")
    rows = cursor.fetchall()
    conn.close()
    return [UserRecord(user_id=r[0], name=r[1], email=r[2], active=bool(r[3])) for r in rows]
```
