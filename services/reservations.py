"""Device reservation (occupancy) layer.

A reservation binds one device to one user for a chosen duration. While a
reservation is active, only its owner may *control* the device (stream,
input, files, apps, terminal, shell). Listing devices and reading their
status stays open to every logged-in user so the UI can show availability.

Releasing happens three ways, all routed through :func:`release`:
  * the owner cancels  → immediate release
  * an admin force-releases
  * the duration expires → :func:`sweep` (background thread) reaps it

Every release runs :func:`_teardown`, which kills the live session so a
released device is genuinely free: stop the scrcpy client, close all
WebRTC peers, and broadcast ``device_released`` so the ex-occupant's
browser drops the stream and updates its UI.

``device_id`` is the table's PRIMARY KEY, so :func:`claim` relies on the
UNIQUE constraint to settle races between two users grabbing the same
device at the same instant — the loser gets IntegrityError, not a
half-applied second reservation.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import scrcpy.scrcpyclients as scrcpy_clients
from config import config
from services import socket_bridge
from services.database import get_conn

_log = logging.getLogger(__name__)

# All tunables sourced from the central config (config/config.py).
MAX_RESERVATION_MINUTES = config.RESERVATION_MAX_MINUTES  # hard cap so nobody squats forever
DEFAULT_RESERVATION_MINUTES = config.RESERVATION_DEFAULT_MINUTES
_SWEEP_INTERVAL_SECONDS = config.RESERVATION_SWEEP_INTERVAL_SECONDS
# Grace buffer: a reservation is only torn down this many seconds AFTER its
# nominal ``expires_at``. The frontend warns at T-2min and counts down to the
# deadline; this soft buffer absorbs clock skew / a last-second "续时" click so
# the stream isn't cut the very instant the timer hits zero.
GRACE_SECONDS = config.RESERVATION_GRACE_SECONDS


class ReservationError(Exception):
    """Raised when a claim conflicts or an access check fails."""


def _row_to_dict(row) -> dict:
    return {
        "device_id": row["device_id"],
        "user_id": row["user_id"],
        "username": row["username"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    }


def get(device_id: str) -> Optional[dict]:
    """Active reservation for a device, or None. Expired rows are reaped."""
    if not device_id:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM device_reservations WHERE device_id = ?", (device_id,)
        ).fetchone()
        if row is None:
            return None
        if _is_expired(row["expires_at"]):
            # Lazily reap on read so a stale row never reports as "occupied".
            conn.execute(
                "DELETE FROM device_reservations WHERE device_id = ?", (device_id,)
            )
            conn.commit()
            _teardown(device_id, reason="expired")
            return None
        return _row_to_dict(row)
    finally:
        conn.close()


def list_all() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM device_reservations ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def claim(device_id: str, user: dict, minutes: int, *, allow_multi: bool = False) -> dict:
    """Reserve ``device_id`` for ``user``. Raises ReservationError on conflict.

    ``allow_multi`` is an additive opt-in used ONLY by the experimental sync
    feature (``api.sync``) to let one user batch-reserve a master + several
    slaves. It bypasses the one-device-per-user anti-hogging rule below.
    Every existing caller leaves it ``False`` so default behaviour is
    completely unchanged.
    """
    device_id = (device_id or "").strip()
    if not device_id:
        raise ReservationError("缺少设备ID")
    if device_id not in scrcpy_clients.get_devices():
        raise ReservationError("设备未连接")

    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = DEFAULT_RESERVATION_MINUTES
    minutes = max(1, min(minutes, MAX_RESERVATION_MINUTES))

    # One-device-per-user: a regular user may hold only ONE device at a time
    # (anti-hogging). Admins/super_admins are exempt for operational needs.
    # Re-claiming the SAME device (extend) is always allowed. The sync feature
    # passes allow_multi=True to reserve a whole group at once.
    if not allow_multi and (user.get("role") or "user") not in ("admin", "super_admin"):
        held = _user_active_device(user["id"])
        if held and held != device_id:
            raise ReservationError("你已占用其它设备，请先释放后再占用")

    # Reap any expired holder before we try to claim.
    existing = get(device_id)
    if existing is not None:
        if existing["user_id"] == user["id"]:
            # Owner re-claiming → treat as an extension to the new duration.
            return _extend(device_id, minutes)
        raise ReservationError(f"设备已被 {existing['username']} 占用")

    expires_at = datetime.now() + timedelta(minutes=minutes)
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO device_reservations (device_id, user_id, username, expires_at)
               VALUES (?, ?, ?, ?)""",
            (device_id, user["id"], user["username"], expires_at),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Lost the race: someone inserted between our get() and INSERT.
        raise ReservationError("设备刚刚被他人占用，请刷新后重试")
    finally:
        conn.close()
    _log.info("Device %s reserved by %s for %d min", device_id, user["username"], minutes)
    socket_bridge.broadcast(
        "reservation_changed", {"device_id": device_id, "state": "claimed"}
    )
    return get(device_id)


def _extend(device_id: str, minutes: int) -> dict:
    expires_at = datetime.now() + timedelta(minutes=minutes)
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE device_reservations SET expires_at = ? WHERE device_id = ?",
            (expires_at, device_id),
        )
        conn.commit()
    finally:
        conn.close()
    socket_bridge.broadcast(
        "reservation_changed", {"device_id": device_id, "state": "extended"}
    )
    return get(device_id)


def release(device_id: str, *, reason: str = "released") -> bool:
    """Remove a reservation and tear down the live session. Idempotent."""
    device_id = (device_id or "").strip()
    if not device_id:
        return False
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM device_reservations WHERE device_id = ?", (device_id,)
        )
        conn.commit()
        removed = cur.rowcount > 0
    finally:
        conn.close()
    # Always tear down (even if no row) so a force-release also kills any
    # session that somehow outlived its reservation.
    _teardown(device_id, reason=reason)
    if removed:
        _log.info("Device %s reservation released (%s)", device_id, reason)
    return removed


def assert_owner(device_id: str, user: Optional[dict]) -> None:
    """Gate a control action. Raises ReservationError if not permitted.

    Allowed when: the device is unreserved (anyone logged in May grab-by-use
    is NOT implicit here — callers decide), OR the caller owns the active
    reservation, OR the caller is an admin/super_admin (operational override).
    """
    if user is None:
        raise ReservationError("未登录")
    res = get(device_id)
    if res is None:
        raise ReservationError("请先占用该设备再操作")
    if res["user_id"] == user["id"]:
        return
    if user.get("role") in ("admin", "super_admin"):
        return
    raise ReservationError(f"设备已被 {res['username']} 占用")


def _user_active_device(user_id: int) -> Optional[str]:
    """The device_id this user currently holds (non-expired), or None.

    Under the one-device-per-user rule there's at most one, but we scan all
    rows defensively and skip expired ones (grace-aware via _is_expired)."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT device_id, expires_at FROM device_reservations WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        if not _is_expired(r["expires_at"]):
            return r["device_id"]
    return None


def _is_expired(expires_at) -> bool:
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            # Stored format from sqlite default is 'YYYY-MM-DD HH:MM:SS[.ffffff]'.
            try:
                expires_at = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return False
    # Only consider it expired once the grace buffer has also elapsed.
    return expires_at < datetime.now() - timedelta(seconds=GRACE_SECONDS)


def _teardown(device_id: str, *, reason: str) -> None:
    """Free the device: stop scrcpy, drop WebRTC peers, notify browsers."""
    try:
        scrcpy_clients.stop_client(device_id)
    except (RuntimeError, OSError, AttributeError) as exc:
        _log.warning("stop_client during teardown failed for %s: %s", device_id, exc)
    try:
        from scrcpy.webrtc.peer_manager import get_peer_manager

        get_peer_manager().close_device(device_id)
    except (ImportError, ModuleNotFoundError, RuntimeError):
        pass
    socket_bridge.broadcast(
        "device_released", {"device_id": device_id, "reason": reason}
    )
    # Experimental sync feature: detach this device from any sync group so a
    # released/expired master stops its group and a released slave leaves it.
    # Fully guarded + opt-in so it can never affect the core teardown path.
    try:
        from services import sync as _sync

        if _sync.is_enabled():
            from services.sync import lifecycle as _sync_lifecycle

            _sync_lifecycle.on_device_gone(device_id)
    except Exception as _sync_exc:  # noqa: BLE001
        _log.debug("sync lifecycle hook skipped for %s: %s", device_id, _sync_exc)


# ── expiry sweeper ──────────────────────────────────────────────────────

_sweeper_started = False
_sweeper_lock = threading.Lock()


def sweep() -> int:
    """Reap every expired reservation (past its grace buffer). Returns count."""
    cutoff = datetime.now() - timedelta(seconds=GRACE_SECONDS)
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT device_id FROM device_reservations WHERE expires_at < ?", (cutoff,)
        ).fetchall()
        expired = [r["device_id"] for r in rows]
        if expired:
            conn.executemany(
                "DELETE FROM device_reservations WHERE device_id = ?",
                [(d,) for d in expired],
            )
            conn.commit()
    finally:
        conn.close()
    for device_id in expired:
        _log.info("Reservation for %s expired", device_id)
        _teardown(device_id, reason="expired")
    return len(expired)


def start_sweeper() -> None:
    """Launch the background expiry loop once. Mirrors quality_controller."""
    global _sweeper_started
    with _sweeper_lock:
        if _sweeper_started:
            return
        _sweeper_started = True

    def _loop() -> None:
        while True:
            try:
                sweep()
            except Exception as exc:  # noqa: BLE001 — never let the loop die
                _log.warning("reservation sweep error: %s", exc)
            time.sleep(_SWEEP_INTERVAL_SECONDS)

    threading.Thread(target=_loop, name="reservation-sweeper", daemon=True).start()
    _log.info("Reservation sweeper started (interval=%ss)", _SWEEP_INTERVAL_SECONDS)
