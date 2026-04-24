import hmac
from dataclasses import dataclass

import bcrypt
from sqlalchemy.orm import Session
from src.db import execute_write, fetch_one


@dataclass
class User:
    id: int
    username: str
    email: str
    password_hash: str
    is_active: bool


def get_user_by_id(session: Session, user_id: int) -> User | None:
    row = fetch_one(
        session,
        "SELECT id, username, email, password_hash, is_active FROM users WHERE id = :id",
        {"id": user_id},
    )
    if row is None:
        return None
    return User(**row)


def get_user_by_email(session: Session, email: str) -> User | None:
    row = fetch_one(
        session,
        "SELECT id, username, email, password_hash, is_active FROM users WHERE email = :email",
        {"email": email},
    )
    if row is None:
        return None
    return User(**row)


def create_user(session: Session, username: str, email: str, password: str) -> User:
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    execute_write(
        session,
        """
        INSERT INTO users (username, email, password_hash, is_active)
        VALUES (:username, :email, :password_hash, TRUE)
        """,
        {"username": username, "email": email, "password_hash": password_hash},
    )
    return get_user_by_email(session, email)


def verify_password(user: User, candidate: str) -> bool:
    # hmac.compare_digest provides constant-time comparison to prevent timing attacks
    expected = bcrypt.hashpw(candidate.encode(), user.password_hash.encode())
    return hmac.compare_digest(expected, user.password_hash.encode())


def deactivate_user(session: Session, user_id: int) -> bool:
    rows = execute_write(
        session,
        "UPDATE users SET is_active = FALSE WHERE id = :id",
        {"id": user_id},
    )
    return rows > 0
