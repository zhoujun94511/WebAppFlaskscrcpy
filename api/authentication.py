"""HTTP routes for authentication + user management.

Thin adapters over :mod:`services.authentication` and the ``users`` table. Public
endpoints (register / login / check-auth) are explicitly exempt from the
global login gate in ``app.py``; everything else here requires a session.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from services import authentication, validators
from services.database import get_conn
from services.rate_limit import RateLimiter, client_ip

_log = logging.getLogger(__name__)

bp = Blueprint("auth_api", __name__)

# Lock a (ip, username) pair after 5 failed logins within 5 min, for 5 min.
# Exposed at module level so it's tunable and resettable from tests.
login_limiter = RateLimiter(max_attempts=3, window_seconds=300, block_seconds=300)


def _fail(message: str, code: int = 400):
    return jsonify({"status": "failed", "error": message}), code


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
    }


# ── public endpoints ────────────────────────────────────────────────────

@bp.route("/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = validators.normalize_username(data.get("username") or "")
    email = validators.normalize_email(data.get("email") or "")
    password = data.get("password") or ""

    err = (
        validators.validate_username(username)
        or validators.validate_email(email)
        or validators.validate_password(password)
    )
    if err:
        return _fail(err)

    password_hash, salt = authentication.new_password_fields(password)
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO users (username, email, password_hash, salt, role, is_active)
               VALUES (?, ?, ?, ?, 'user', 1)""",
            (username, email, password_hash, salt),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({"status": "failed", "error": "用户名或邮箱已存在"}), 409
    finally:
        conn.close()
    _log.info("New user registered: %s", username)
    return jsonify({"status": "ok", "message": "注册成功，请登录"}), 201


@bp.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return _fail("用户名和密码不能为空", 400)

    # Brute-force lockout: count failed attempts per (ip, username). Reset on
    # success. Generic, non-enumerating message (we never reveal which of the
    # two — account or password — was wrong, nor whether the account exists).
    rl_key = f"login:{client_ip()}:{username.lower()}"
    locked_for = login_limiter.retry_after(rl_key)
    if locked_for:
        return _fail(f"登录尝试过于频繁，请约 {locked_for} 秒后再试", 429)

    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT id, username, email, password_hash, salt, role, is_active
               FROM users WHERE (username = ? OR email = ?)""",
            (username, username),
        ).fetchone()
        valid = (
            bool(row)
            and bool(row["is_active"])
            and authentication.verify_password(row["password_hash"], row["salt"], password)
        )
        if not valid:
            login_limiter.record(rl_key)
            return _fail("用户名或密码错误", 401)
        conn.execute(
            "UPDATE users SET last_login = ? WHERE id = ?", (datetime.now(), row["id"])
        )
        conn.commit()
    finally:
        conn.close()

    login_limiter.reset(rl_key)
    token = authentication.create_session(row["id"])
    session["session_token"] = token
    session.permanent = True
    user = {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "role": row["role"],
    }
    return jsonify({"status": "ok", "user": user})


@bp.route("/auth/logout", methods=["POST"])
def logout():
    authentication.destroy_session(session.get("session_token", ""))
    session.clear()
    return jsonify({"status": "ok"})


@bp.route("/auth/check-auth", methods=["GET"])
def check_auth():
    user = authentication.current_user()
    if not user:
        return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, "user": _public_user(user)})


# ── self-service (login required) ───────────────────────────────────────

@bp.route("/auth/profile", methods=["GET"])
@authentication.login_required
def profile():
    return jsonify({"status": "ok", "user": _public_user(authentication.current_user())})


@bp.route("/auth/change-password", methods=["POST"])
@authentication.login_required
def change_password():
    data = request.get_json(silent=True) or {}
    old_password = data.get("old_password") or ""
    new_password = data.get("new_password") or ""
    pw_err = validators.validate_password(new_password)
    if pw_err:
        return _fail(pw_err)

    user = authentication.current_user()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT password_hash, salt FROM users WHERE id = ?", (user["id"],)
        ).fetchone()
        if not row or not authentication.verify_password(row["password_hash"], row["salt"], old_password):
            return jsonify({"status": "failed", "error": "原密码不正确"}), 400
        password_hash, salt = authentication.new_password_fields(new_password)
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
            (password_hash, salt, user["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    # Invalidate every session (including this one) → force re-login.
    authentication.revoke_user_sessions(user["id"])
    session.clear()
    return jsonify({"status": "ok", "message": "密码已修改，请重新登录"})


# ── admin user management ───────────────────────────────────────────────

@bp.route("/auth/users", methods=["GET"])
@authentication.admin_required
def list_users():
    actor = authentication.current_user()
    conn = get_conn()
    try:
        if actor["role"] == "super_admin":
            rows = conn.execute(
                """SELECT id, username, email, role, is_active, created_at, last_login
                   FROM users ORDER BY created_at DESC"""
            ).fetchall()
        else:
            # A plain admin only manages normal users.
            rows = conn.execute(
                """SELECT id, username, email, role, is_active, created_at, last_login
                   FROM users WHERE role = 'user' ORDER BY created_at DESC"""
            ).fetchall()
    finally:
        conn.close()
    users = [
        {
            "id": r["id"],
            "username": r["username"],
            "email": r["email"],
            "role": r["role"],
            "is_active": bool(r["is_active"]),
            "created_at": r["created_at"],
            "last_login": r["last_login"],
        }
        for r in rows
    ]
    return jsonify({"status": "ok", "users": users})


def _allowed_target_roles(actor_role: str) -> tuple[str, ...]:
    """Role values an actor may assign. Admins manage only plain users;
    super_admins may assign any role (including minting another admin)."""
    if actor_role == "super_admin":
        return "user", "admin", "super_admin"
    return ("user",)


@bp.route("/auth/users", methods=["POST"])
@authentication.admin_required
def create_user():
    actor = authentication.current_user()
    data = request.get_json(silent=True) or {}
    username = validators.normalize_username(data.get("username") or "")
    email = validators.normalize_email(data.get("email") or "")
    password = data.get("password") or ""
    role = (data.get("role") or "user").strip()

    err = (
        validators.validate_username(username)
        or validators.validate_email(email)
        or validators.validate_password(password)
    )
    if err:
        return _fail(err)
    if role not in _allowed_target_roles(actor["role"]):
        return _fail("无权创建该角色的账号", 403)

    password_hash, salt = authentication.new_password_fields(password)
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO users (username, email, password_hash, salt, role, is_active)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (username, email, password_hash, salt, role),
        )
        conn.commit()
        new_id = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({"status": "failed", "error": "用户名或邮箱已存在"}), 409
    finally:
        conn.close()
    _log.info("%s created user %s (role=%s)", actor["username"], username, role)
    return jsonify({
        "status": "ok",
        "user": {"id": new_id, "username": username, "email": email, "role": role},
    }), 201


@bp.route("/auth/users/<int:user_id>", methods=["DELETE"])
@authentication.admin_required
def delete_user(user_id: int):
    actor = authentication.current_user()
    if actor["id"] == user_id:
        return jsonify({"status": "failed", "error": "不能删除自己"}), 400
    conn = get_conn()
    try:
        row = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return jsonify({"status": "failed", "error": "用户不存在"}), 404
        target_role = row["role"]
        if target_role == "super_admin":
            return jsonify({"status": "failed", "error": "不能删除超级管理员"}), 403
        if actor["role"] == "admin" and target_role != "user":
            return jsonify({"status": "failed", "error": "管理员只能删除普通用户"}), 403
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ok"})


@bp.route("/auth/users/<int:user_id>/active", methods=["POST"])
@authentication.admin_required
def set_user_active(user_id: int):
    actor = authentication.current_user()
    if actor["id"] == user_id:
        return jsonify({"status": "failed", "error": "不能停用自己"}), 400
    enabled = bool((request.get_json(silent=True) or {}).get("is_active", True))
    conn = get_conn()
    try:
        row = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return jsonify({"status": "failed", "error": "用户不存在"}), 404
        if row["role"] == "super_admin":
            return jsonify({"status": "failed", "error": "不能停用超级管理员"}), 403
        if actor["role"] == "admin" and row["role"] != "user":
            return jsonify({"status": "failed", "error": "管理员只能管理普通用户"}), 403
        conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?", (1 if enabled else 0, user_id)
        )
        conn.commit()
    finally:
        conn.close()
    if not enabled:
        authentication.revoke_user_sessions(user_id)
    return jsonify({"status": "ok"})


@bp.route("/auth/users/<int:user_id>", methods=["PUT"])
@authentication.admin_required
def update_user(user_id: int):
    actor = authentication.current_user()
    data = request.get_json(silent=True) or {}

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            return jsonify({"status": "failed", "error": "用户不存在"}), 404
        target_role = row["role"]
        # A plain admin may only touch normal users.
        if actor["role"] == "admin" and target_role != "user":
            return jsonify({"status": "failed", "error": "管理员只能管理普通用户"}), 403

        fields: list[str] = []
        values: list = []
        revoke = False

        if "email" in data:
            email = validators.normalize_email(data.get("email") or "")
            email_err = validators.validate_email(email)
            if email_err:
                return _fail(email_err)
            fields.append("email = ?")
            values.append(email)

        if "role" in data:
            new_role = (data.get("role") or "").strip()
            if new_role not in _allowed_target_roles(actor["role"]):
                return jsonify({"status": "failed", "error": "无权设置该角色"}), 403
            # Guard self-demotion → avoids an admin locking themselves out.
            if user_id == actor["id"] and new_role != actor["role"]:
                return jsonify({"status": "failed", "error": "不能修改自己的角色"}), 400
            fields.append("role = ?")
            values.append(new_role)

        if data.get("password"):
            password = data["password"]
            pw_err = validators.validate_password(password)
            if pw_err:
                return _fail(pw_err)
            password_hash, salt = authentication.new_password_fields(password)
            fields.append("password_hash = ?")
            fields.append("salt = ?")
            values.extend([password_hash, salt])
            revoke = True  # force re-login after a reset

        if "is_active" in data:
            enabled = bool(data.get("is_active"))
            if not enabled and user_id == actor["id"]:
                return jsonify({"status": "failed", "error": "不能停用自己"}), 400
            fields.append("is_active = ?")
            values.append(1 if enabled else 0)
            if not enabled:
                revoke = True

        if not fields:
            return jsonify({"status": "failed", "error": "没有需要更新的字段"}), 400

        values.append(user_id)
        try:
            conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        except sqlite3.IntegrityError:
            return jsonify({"status": "failed", "error": "邮箱已被占用"}), 409
    finally:
        conn.close()

    if revoke:
        authentication.revoke_user_sessions(user_id)
    _log.info("%s updated user id=%s", actor["username"], user_id)
    return jsonify({"status": "ok"})
