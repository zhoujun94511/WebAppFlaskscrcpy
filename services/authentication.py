"""Authentication core: password checks, session tokens, access decorators.

The Flask session cookie carries a single opaque ``session_token``; the
authoritative record lives in the ``user_sessions`` table (so admins can
revoke, and so we can expire server-side). Every request that needs an
identity calls :func:`current_user`, which validates that token.

Roles
-----
``super_admin``  break-glass account; can manage admins + everything below.
``admin``        can manage normal users and force-release any device.
``user``         self-registers, reserves/uses devices.

Decorators short-circuit with 401/403 JSON for the SPA's fetch layer.
Socket.IO handlers can't use decorators the same way, so they call
:func:`user_from_socket` directly inside the event handler (the Socket.IO
polling transport still carries the session cookie because it's same
origin in production / proxied in dev).
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional

from flask import g, jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash

from config import config
from services.database import get_conn

_log = logging.getLogger(__name__)

SESSION_TTL_HOURS = config.SESSION_TTL_HOURS
ROLES = ("super_admin", "admin", "user")


def _row_to_user(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
    }


# ── password helpers ────────────────────────────────────────────────────

def verify_password(stored_hash: str, salt: str, password: str) -> bool:
    return check_password_hash(stored_hash, password + salt)


def new_password_fields(password: str) -> tuple[str, str]:
    """Return (password_hash, salt) for a fresh password."""
    salt = secrets.token_hex(32)
    return generate_password_hash(password + salt), salt


# ── session lifecycle ───────────────────────────────────────────────────

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(hours=SESSION_TTL_HOURS)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO user_sessions (user_id, session_token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, expires_at),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def destroy_session(token: str) -> None:
    if not token:
        return
    conn = get_conn()
    try:
        conn.execute("DELETE FROM user_sessions WHERE session_token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def revoke_user_sessions(user_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def validate_token(token: str) -> Optional[dict]:
    if not token:
        return None
    conn = get_conn()
    try:
        # Opportunistically prune expired rows so the table doesn't grow.
        conn.execute("DELETE FROM user_sessions WHERE expires_at < ?", (datetime.now(),))
        conn.commit()
        row = conn.execute(
            """SELECT u.id, u.username, u.email, u.role, u.is_active
               FROM users u JOIN user_sessions s ON u.id = s.user_id
               WHERE s.session_token = ? AND u.is_active = 1
                 AND s.expires_at > ?""",
            (token, datetime.now()),
        ).fetchone()
        return _row_to_user(row) if row else None
    finally:
        conn.close()


def current_user() -> Optional[dict]:
    """Resolve the request's user from the session cookie token.

    Cached on ``flask.g`` for the duration of the request so repeated
    decorator + handler lookups don't each hit the DB.
    """
    if "current_user" in g.__dict__:
        return g.current_user
    user = validate_token(session.get("session_token", ""))
    g.current_user = user
    return user


def user_from_socket() -> Optional[dict]:
    """Identity lookup for Socket.IO handlers (no g caching — short-lived)."""
    return validate_token(session.get("session_token", ""))


# ── HTTP decorators ─────────────────────────────────────────────────────

def _deny(status: int, message: str):
    return jsonify({"status": "failed", "error": message}), status


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return _deny(401, "未登录或会话已过期")
        return fn(*args, **kwargs)

    return wrapper


def _role_required(*allowed):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                return _deny(401, "未登录或会话已过期")
            if user["role"] not in allowed:
                return _deny(403, "权限不足")
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def admin_required(fn):
    return _role_required("admin", "super_admin")(fn)


def super_admin_required(fn):
    return _role_required("super_admin")(fn)
