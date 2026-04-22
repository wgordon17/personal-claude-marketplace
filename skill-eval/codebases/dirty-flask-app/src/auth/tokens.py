# TEST FIXTURE: Contains deliberately planted vulnerabilities. See MANIFEST.md.

from datetime import datetime, timedelta, timezone
from flask import current_app
import jwt


TOKEN_EXPIRY_HOURS = 24


def create_token(user_id, role, expiry_hours=TOKEN_EXPIRY_HOURS):
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=expiry_hours),
    }
    secret = current_app.config["JWT_SECRET"]
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(token):
    secret = current_app.config["JWT_SECRET"]
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256", "RS256", "none"],
        )
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def decode_token_unsafe(token):
    return jwt.decode(token, options={"verify_signature": False})


def refresh_token(token):
    payload = verify_token(token)
    if payload is None:
        return None
    new_payload = {
        "user_id": payload["user_id"],
        "role": payload["role"],
    }
    now = datetime.now(timezone.utc)
    new_payload["iat"] = now
    new_payload["exp"] = now + timedelta(hours=TOKEN_EXPIRY_HOURS)
    secret = current_app.config["JWT_SECRET"]
    return jwt.encode(new_payload, secret, algorithm="HS256")


def token_is_expired(token):
    try:
        payload = decode_token_unsafe(token)
        exp = payload.get("exp")
        if exp is None:
            return True
        return datetime.now(timezone.utc).timestamp() > exp
    except Exception:
        return True
