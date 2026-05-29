"""Shared account input validation + normalization.

Single source of truth for username / email / password rules so every
write path (register, admin create / update, change-password) enforces an
identical policy instead of duplicating regexes inline. Mirrored on the
client in ``frontend/src/composables/useValidators.js`` for instant UX —
but the server is the authoritative gate (never trust the client).

Policy baseline (OWASP ASVS 5.x + Django's username validator):
  * username — allow-list characters (blocks spaces / control / HTML chars,
    which is defence-in-depth against XSS even though Vue escapes output and
    all SQL is parameterised), 3–32 chars, must start alphanumeric;
  * email    — pragmatic shape check + length cap, normalised to lower-case;
  * password — 8–128 chars, must contain at least one letter and one digit.

Each ``validate_*`` returns ``None`` when valid, or a ready Chinese error
string (matching the rest of the API's error messages) when not.
"""

from __future__ import annotations

import re
from typing import Optional

# ── bounds (kept in sync with the frontend mirror) ──────────────────────
USERNAME_MIN = 3
USERNAME_MAX = 32
EMAIL_MAX = 254  # RFC 5321 practical maximum
PASSWORD_MIN = 8
PASSWORD_MAX = 128  # cap hashing input → avoids CPU-DoS on huge passwords

# Start with a letter/digit, then letters/digits/._- ; no spaces or HTML.
USERNAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{%d,%d}$" % (USERNAME_MIN - 1, USERNAME_MAX - 1)
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"\d")

_USERNAME_MSG = (
    f"用户名需为{USERNAME_MIN}-{USERNAME_MAX}位，以字母或数字开头，"
    "仅含字母、数字、下划线、点或连字符"
)


def normalize_username(name: str) -> str:
    return (name or "").strip()


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_username(name: str) -> Optional[str]:
    name = normalize_username(name)
    if not name:
        return "用户名不能为空"
    if not USERNAME_RE.match(name):
        return _USERNAME_MSG
    return None


def validate_email(email: str) -> Optional[str]:
    email = normalize_email(email)
    if not email:
        return "邮箱不能为空"
    if len(email) > EMAIL_MAX or not EMAIL_RE.match(email):
        return "邮箱格式不正确"
    return None


def validate_password(password: str) -> Optional[str]:
    password = password or ""
    if len(password) < PASSWORD_MIN:
        return f"密码长度至少{PASSWORD_MIN}位"
    if len(password) > PASSWORD_MAX:
        return f"密码长度不能超过{PASSWORD_MAX}位"
    if not _HAS_LETTER.search(password) or not _HAS_DIGIT.search(password):
        return "密码需同时包含字母和数字"
    return None
