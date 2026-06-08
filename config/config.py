"""Central configuration — the ONE place to set every project parameter.

Edit the literal defaults below to configure the app. No ``.env`` file is
required and no other module reads ``os.environ`` for configuration: they
all import their values from here.

Each setting still *optionally* honours an environment variable of the same
name, purely as a deployment-time override (e.g. setting ``PORT`` in a
container) — but if the variable is unset, the literal default in this file
is used. So the rule is simply: **configured here → used; not overridden by
env → fall back to the default here.**

This module imports only the standard library so it can be imported from
anywhere (including very early during startup) without circular-import risk.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repository root = the directory that contains this ``config/`` package.
ROOT_DIR = Path(__file__).resolve().parent.parent


# ── env-override helpers ─────────────────────────────────────────────────
# Read an optional environment override; fall back to the literal default
# defined in this file when the variable is unset/blank.

def _str(key: str, default: str) -> str:
    val = os.environ.get(key)
    return val if val not in (None, "") else default


def _bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val in (None, ""):
        return default
    return val.strip().lower() not in ("0", "false", "no", "off")


def _int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val in (None, ""):
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ═════════════════════════════════════════════════════════════════════════
# Feature flags
# ═════════════════════════════════════════════════════════════════════════
# Experimental master→slave multi-device sync ("实验室" / Lab). Turn off by
# setting this to False. When off: the REST blueprint isn't registered, the
# input fan-out + reservation hooks are skipped, and the frontend hides the
# Lab nav entry.
ENABLE_SYNC = _bool("ENABLE_SYNC", True)

# WebRTC signaling (video/input/file data planes). Default on; auto-falls
# back gracefully if aiortc is missing even when True.
ENABLE_WEBRTC = _bool("ENABLE_WEBRTC", True)

# Embedded ADB terminal (xterm.js).
ENABLE_TERMINAL = _bool("ENABLE_TERMINAL", True)

# Open the system browser to the UI on startup (dev convenience).
OPEN_BROWSER = _bool("OPEN_BROWSER", True)


# ═════════════════════════════════════════════════════════════════════════
# Web server
# ═════════════════════════════════════════════════════════════════════════
# Bind host. Empty string → auto-detect the LAN IP at runtime (see app.py).
# Set to "127.0.0.1" to bind localhost only.
HOST = _str("HOST", "")
PORT = _int("PORT", 5001)
# Flask session cookie signing key. Empty → a random key is generated per
# start (fine for dev; set a stable value for production so sessions survive
# restarts).
FLASK_SECRET_KEY = _str("FLASK_SECRET_KEY", "")


# ═════════════════════════════════════════════════════════════════════════
# Database (SQLite)
# ═════════════════════════════════════════════════════════════════════════
DB_PATH = Path(_str("WEBAPP_DB_PATH", str(ROOT_DIR / "data" / "app.db")))


# ═════════════════════════════════════════════════════════════════════════
# Logging
# ═════════════════════════════════════════════════════════════════════════
LOG_LEVEL = _str("LOG_LEVEL", "INFO")


# ═════════════════════════════════════════════════════════════════════════
# Authentication / sessions
# ═════════════════════════════════════════════════════════════════════════
SESSION_TTL_HOURS = _int("SESSION_TTL_HOURS", 24)


# ═════════════════════════════════════════════════════════════════════════
# Device reservations (occupancy)
# ═════════════════════════════════════════════════════════════════════════
RESERVATION_MAX_MINUTES = _int("RESERVATION_MAX_MINUTES", 240)
RESERVATION_DEFAULT_MINUTES = _int("RESERVATION_DEFAULT_MINUTES", 60)
# Soft buffer after expiry before teardown (absorbs clock skew / last-second
# "续时"); see services.reservations.
RESERVATION_GRACE_SECONDS = _int("RESERVATION_GRACE_SECONDS", 30)
RESERVATION_SWEEP_INTERVAL_SECONDS = _int("RESERVATION_SWEEP_INTERVAL_SECONDS", 10)


# ═════════════════════════════════════════════════════════════════════════
# scrcpy server
# ═════════════════════════════════════════════════════════════════════════
# The version string MUST match the bundled jar, or the server refuses the
# client. Override both together if dropping in a different server build.
SCRCPY_SERVER_VERSION = _str("SCRCPY_SERVER_VERSION", "4.0")
SCRCPY_SERVER_PATH = _str(
    "SCRCPY_SERVER_PATH", str(ROOT_DIR / "resources" / "scrcpy" / "scrcpy-server.jar")
)


# ═════════════════════════════════════════════════════════════════════════
# uiautomator2 (Lab V2 — semantic sync)
# ═════════════════════════════════════════════════════════════════════════
# Vendored uiautomator helper APKs (offline backup; u2 3.x bundles its own
# u2.jar + app-uiautomator.apk, so these are only used if we pin offline).
U2_APK_DIR = ROOT_DIR / "resources" / "uiautomator"
# Seconds to wait for an u2 selector to appear before giving up.
U2_WAIT_TIMEOUT = float(_str("U2_WAIT_TIMEOUT", "1.5"))


# ═════════════════════════════════════════════════════════════════════════
# Sync (Lab feature) tunables
# ═════════════════════════════════════════════════════════════════════════
# Fallback duration for a mirrored swipe when the event omits one (ms).
SYNC_SWIPE_DEFAULT_MS = _int("SYNC_SWIPE_DEFAULT_MS", 200)
# Semantic mode: a touch down→up that moves more than this many device pixels
# is treated as a SWIPE (coordinate-mirrored) rather than a tap (control click).
SYNC_SWIPE_MIN_PX = _int("SYNC_SWIPE_MIN_PX", 24)
# Rotating log file for sync fan-out diagnostics (logs to file, never DB).
SYNC_LOG_PATH = Path(_str("SYNC_LOG_PATH", str(ROOT_DIR / "logs" / "sync.log")))
SYNC_LOG_MAX_BYTES = _int("SYNC_LOG_MAX_BYTES", 2 * 1024 * 1024)
SYNC_LOG_BACKUP_COUNT = _int("SYNC_LOG_BACKUP_COUNT", 3)

# Failure evidence (V2.4): when a slave action fails both semantically and via
# coordinate fallback, capture a screenshot + UI XML + JSON detail here. These
# are discrete files (no rotation), so they get an age-based cleanup.
SYNC_FAILURE_CAPTURE = _bool("SYNC_FAILURE_CAPTURE", True)
SYNC_FAILURE_DIR = Path(_str("SYNC_FAILURE_DIR", str(ROOT_DIR / "data" / "sync_failures")))
SYNC_FAILURE_RETENTION_DAYS = _int("SYNC_FAILURE_RETENTION_DAYS", 14)

# Stability (V2.6). Semantic taps are expensive (master UI dump), so drop a new
# tap while one is in flight for the same master OR within this interval.
SYNC_TAP_THROTTLE_MS = _int("SYNC_TAP_THROTTLE_MS", 250)
# Max slaves clicked in parallel per tap (per-device u2 calls stay serialised).
SYNC_MAX_CONCURRENCY = _int("SYNC_MAX_CONCURRENCY", 8)
