"""SQLite persistence for accounts, sessions and device reservations.

The app previously had no datastore. This module owns the single SQLite
file (``data/app.db``) and the schema bootstrap. Connections are created
per-call (SQLite is fine with that under the threading Socket.IO model)
with ``check_same_thread=False`` so a connection opened on one worker
thread can be used from another — every connection is short-lived and
guarded by the GIL + per-statement commits, so cross-thread reuse never
actually happens in practice.

Schema
------
users               account records (super_admin / admin / user)
user_sessions       server-side session tokens (the Flask cookie only
                    carries the opaque token; the row is the source of
                    truth and lets admins revoke sessions)
device_reservations one row per occupied device. ``device_id`` is the
                    PRIMARY KEY, so a duplicate ``INSERT`` raises
                    IntegrityError — that's how we make "claim" atomic
                    and race-free without an app-level lock.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import threading
from pathlib import Path

from werkzeug.security import generate_password_hash

_log = logging.getLogger(__name__)

# DB file path. Overridable via ``WEBAPP_DB_PATH`` so a second instance (e.g. a
# throwaway verification run) can point at an isolated DB instead of clobbering
# the primary ``data/app.db`` — booting wipes user_sessions, so sharing one
# file across two processes silently logs the other one out.
DB_PATH = Path(
    os.environ.get("WEBAPP_DB_PATH")
    or (Path(__file__).resolve().parent.parent / "data" / "app.db")
)

# Default seeded accounts. Documented in docs; change the passwords after
# first deploy. The super_admin is the break-glass account that can never
# be deleted/disabled by a normal admin, so a leaked/locked ``admin`` can
# always be recovered.
SEED_ACCOUNTS = [
    ("superadmin", "superadmin@local", "superadmin123", "super_admin"),
    ("admin", "admin@local", "admin123", "admin"),
]

_init_lock = threading.Lock()
_initialised = False


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    # Foreign keys are off by default in SQLite; we rely on the
    # user_sessions → users FK cascade for session cleanup on user delete.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _hash_password(password: str, salt: str) -> str:
    return generate_password_hash(password + salt)


def _seed_accounts(conn: sqlite3.Connection) -> None:
    for username, email, password, role in SEED_ACCOUNTS:
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row:
            continue
        salt = secrets.token_hex(32)
        conn.execute(
            """INSERT INTO users (username, email, password_hash, salt, role, is_active)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (username, email, _hash_password(password, salt), salt, role),
        )
        _log.info("Seeded %s account: %s", role, username)


def init_db() -> None:
    """Create tables + seed accounts. Idempotent; safe to call at every boot."""
    global _initialised
    with _init_lock:
        if _initialised:
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = get_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT UNIQUE NOT NULL,
                    email         TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt          TEXT NOT NULL,
                    role          TEXT NOT NULL DEFAULT 'user',
                    is_active     INTEGER NOT NULL DEFAULT 1,
                    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login    TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_sessions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       INTEGER NOT NULL,
                    session_token TEXT UNIQUE NOT NULL,
                    expires_at    TIMESTAMP NOT NULL,
                    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_token
                    ON user_sessions (session_token);

                CREATE TABLE IF NOT EXISTS device_reservations (
                    device_id   TEXT PRIMARY KEY,
                    user_id     INTEGER NOT NULL,
                    username    TEXT NOT NULL,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at  TIMESTAMP NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                );
                """
            )
            _seed_accounts(conn)
            # Wipe stale sessions on boot so a restart forces re-login and we
            # don't carry tokens whose signing key may have rotated.
            conn.execute("DELETE FROM user_sessions")
            # Reservations are also cleared on boot: on a fresh process no
            # scrcpy clients are running, so any persisted reservation is a
            # ghost that would block the device with no live session behind it.
            conn.execute("DELETE FROM device_reservations")
            conn.commit()
            _initialised = True
            _log.info("Database initialised at %s", DB_PATH)
        finally:
            conn.close()
